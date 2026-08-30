# ADR 0014 — Process output path

**Status:** accepted (2026-08-29, by the Phase 9 design spec — staged-then-finalized via the P9-B seam; STS session-policy run-scoped credentials)

## Context

A process run (ADR 0013) produces new catalog items: asset bytes plus STAC
item JSON. Something has to get those into pgstac and canonical storage —
and the producer is untrusted user code. Letting that code write pgstac or
canonical storage directly would breach every write-path invariant the
platform has: validation before upsert (§6.1 ITEMIZE), asset hrefs pointing
at `/api/assets/...` (ADR 0005), app-owned DDL and audited mutations (ADR
0001, Phase 1), and the finalize gating that keeps delivery from streaming
half-written assets (§6.4).

Phase 7 (push ingest, §6.2) already defines the shape for exactly this
problem with a different producer: an untrusted external client writes to a
`staging/` prefix, and the **platform** validates, checksums, moves staging →
canonical, rewrites hrefs, and upserts.

## Recommended decision

**User code never writes pgstac or canonical storage. It writes outputs to a
run-scoped staging prefix; the platform finalizes.**

- The executor hands the run **short-lived, scoped credentials** valid only
  for its own staging prefix (extending §5.3's layout, e.g.
  `staging/runs/{run_id}/…`). That prefix is the run's entire writable
  world.
- User code writes asset files plus item JSON documents there and exits.
- On success the platform **finalizes**: validate each item
  (stac-pydantic — the same gate as ITEMIZE), checksum assets, move staging
  → canonical (`assets/{collection}/{item_id}/…`), rewrite asset hrefs to
  `/api/assets/...`, and upsert via pypgstac into the process's declared
  output collections only. On failure (or validation rejection), nothing
  reaches the catalog; the staging prefix is TTL-swept like abandoned
  uploads (ADR 0005), and the run's ledger row records the rejection.
- Finalized items emit ordinary outbox events, so **processes compose with
  delivery for free**: output items flow to delivery associations like any
  other item, including `on_update` semantics when a re-run upserts the
  same item ids.

**This is Phase 7's finalize step with a different producer.**
**Sequencing settled 2026-08-27: Phase 7 lands first and Phase 9 consumes
its finalize** (ROADMAP §9 steering order). The obligation this places on
Phase 7's build: the finalize slice (validate + checksum + move + rewrite +
upsert) must be **parameterized by producer** from day one — push-ingest is
merely its first caller — so Phase 9 plugs in without a parallel path.
`TODO.md` P9-B is the check that Phase 7's design honors this seam.

### Loop hazard

Because outputs re-enter the catalog as ordinary items, a process whose
output collection is also (directly or transitively) one of its source
collections is a feedback loop that would self-trigger indefinitely.
**Detect and refuse at association time** — creating a `process_sources` or
`process_outputs` row that closes a cycle is rejected, mirroring how
built-in-catalog-only is enforced on `collection_connections`. The detection
scope (direct source/output edges only, or also transitive loops through
delivery→re-ingest edges) is an open question (ISSUES I-64) — association
edges alone are cheap and clearly worth it; edges through external systems
are not statically knowable.

## Alternatives considered

- **Direct pypgstac access from user code** — rejected: hands the DB (and
  with it, every `stac_higher` table) to untrusted code; unvalidated items
  enter the catalog; unauditable.
- **User code calls the external push-ingest API (Phase 7) as a client** —
  honest reuse, but it forces per-run OIDC client credentials, routes bulk
  output through the app's HTTP surface (the app never streams bytes — ADR
  0005), and still needs the staging+finalize machinery underneath. The
  staging-prefix contract gets the same safety with less moving surface.

## Consequences

- Validation cost is paid per output item at finalize (same as push
  ingest); a high-fan-out process is throttled by the platform, not by its
  own code's write speed — this is the correct choke point for the §10
  throughput-multiplication risk.
- The run ledger can record `output_items` authoritatively (the finalize
  step knows exactly what it upserted), which the lineage panel and `/graph`
  (§8) rely on.
- Reference-mode outputs (process outputs pointing at external storage) are
  **out of scope** for the first slice — outputs are canonical-storage items;
  note it as a possible later extension rather than complicating the staging
  contract now.
- Finalize gating already defers dispatch for staged items (§6.4), so
  delivery never sees a process output before its hrefs are canonical.

## Finalize seam sketch (P9-B, 2026-08-29)

Phase 7 has no design spec yet, so the seam is recorded here as the
obligation its design must satisfy — stated as an interface so it cannot
drift into a push-ingest-shaped one-off:

```
finalize(run: FinalizeRequest) -> FinalizeResult

FinalizeRequest:
  producer:            "push_ingest" | "process_run"   # extensible enum
  staging_prefix:      str      # staging/uploads/{...} | staging/runs/{run_id}/
  output_collections:  [str]    # the ONLY collections upsert may touch
  items:               [staged item JSON refs]
  provenance:          {producer-specific ledger key}  # upload id | run id

FinalizeResult:
  upserted: [{collection_id, item_id}]   # authoritative output_items
  rejected: [{item_ref, reason}]
```

The steps (validate → checksum → move staging→canonical → rewrite hrefs →
upsert restricted to `output_collections` → ordinary outbox events) contain
no producer branching; producer differences live only in the request. Check
criterion for the Phase 7 design review: if its finalize cannot accept a
`FinalizeRequest` naming a different producer and staging prefix without
code changes, the seam is violated. (Also recorded in the Phase 9 scoping
notes, `docs/superpowers/specs/2026-08-29-phase9-scoping-notes.md`.)

## Revisit

Accept/revise in the Phase 9 design spec. The Phase 7 sequencing question is
settled (Phase 7 first, producer-parameterized finalize — above, now with
the P9-B interface sketch); still open for the spec: the cycle-detection
scope (I-64, with the run-rate-ceiling backstop now a requirement) and the
run-scoped credential mechanics.
