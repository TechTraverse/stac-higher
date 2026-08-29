# Phase 9 scoping notes (P9-B…P9-E, 2026-08-29)

Inputs to the Phase 9 design spec (P9-F). Companion to ADR 0013's P9-A
investigation appendix (executor backends) and ADR 0014's finalize-seam
appendix (P9-B, summarized here). Scope sources: ROADMAP §5 `PROCESS_*`,
§5.6, §6.7, §8 Phase 9 table; ISSUES I-60…I-66. Settled inputs (do not
relitigate): M5 precedes M3; Phase 7 first with a producer-parameterized
finalize; slice 1 is `inline_python` on a platform-built image; a
per-process run-rate ceiling + alert is a requirement.

---

## P9-B · Finalize seam check (feeds ADR 0014, Phase 7)

Phase 7 has no design spec yet, so this is the **obligation stated as an
interface**, recorded before Phase 7's design pass so it cannot drift into a
push-ingest-shaped one-off. The full sketch lives in ADR 0014 ("Finalize
seam sketch" appendix); the shape:

```
finalize(run: FinalizeRequest) -> FinalizeResult

FinalizeRequest:
  producer:            "push_ingest" | "process_run"   # extensible enum
  staging_prefix:      str          # staging/uploads/{...} | staging/runs/{run_id}/
  output_collections:  [str]        # the ONLY collections upsert may touch
  items:               [staged item JSON refs]         # discovered or declared
  provenance:          {producer-specific ledger key}  # upload id | run id

FinalizeResult:
  upserted: [{collection_id, item_id}]  # authoritative output_items
  rejected: [{item_ref, reason}]        # validation failures, per item
```

Pipeline-side steps (one module, no producer branching inside the steps):
validate (stac-pydantic, the ITEMIZE gate) → checksum staged assets → move
staging → canonical (§5.3) → rewrite hrefs to `/api/assets/...` → upsert via
pypgstac **restricted to `output_collections`** → emit ordinary outbox
events (delivery composes for free; finalize gating already defers dispatch
for staged items, §6.4). Producer differences live only in the request:
where staging is, which ledger row records the outcome, which collections
are writable. **Check criterion for the Phase 7 spec review:** if its
finalize design cannot accept a `FinalizeRequest` naming a different
producer + staging prefix without code changes, it has violated the seam.

## P9-C · Inline-editor dependency evaluation (I-65)

Measured (npm registry, 2026-08-29): `monaco-editor` **~98MB unpacked**
(0.56.0) — a VS-Code-scale dependency with its own loader/worker
architecture; `codemirror` 6 meta-package 21KB + modular `@codemirror/*`
deps (`@codemirror/lang-python` 37KB unpacked; a realistic browser bundle
for a python editor is a few hundred KB minified). CodeMirror 6 is
MIT-licensed, actively maintained, tree-shakeable, and themes via plain
CSS/`EditorView.theme` (fits the shadcn/dark-theme setup); it loads cleanly
inside a `client:only` island with no worker plumbing. Monaco's size,
worker requirements, and bundler friction are disproportionate to editing
one Python snippet.

**Recommendation:**
- **Slice 1: a plain `<textarea>` suffices** (monospace, tab-key handling,
  the repo's existing form pattern). It keeps the first accreditation
  surface dependency-free per the no-new-deps rule; process code in slice 1
  is short glue-code, not an IDE workload.
- **When editor UX is justified** (operator feedback, longer processes):
  adopt **CodeMirror 6** (`@uiw/react-codemirror` wrapper ~826KB unpacked +
  `@codemirror/lang-python`) — never Monaco. Record the supply-chain review
  (maintainer, license, dep tree) in the adopting PR per the compliance
  posture.

## P9-D · Contract-fixture plan

Phase 9 cross-runtime shapes (Zod write-gate ↔ Python lenient reader — the
established asymmetric contract), each a `tests/contract-fixtures/` file in
the README's `minimal`/`defaults`/`cases[]` format, consumed by both suites:

| Fixture | Shape | Notes |
|---|---|---|
| `process-trigger.json` | §5.6 `trigger`: `{kind:"item_event", filter?}` \| `{kind:"cron", schedule}` | cron schedule validated app-side; filter is CQL2 text (same non-validation caveat as I-41 unless the spec tightens it) |
| `process-runtime.json` | §5.6 `runtime`: `{kind:"inline_python", ...}` \| `{kind:"container", image}` | the shape CARRIES `container` (nothing foreclosed) while slice 1's write path **rejects** it — encode as a `cases[]` entry: `app: "reject"`, `pipeline: "accept"` |
| `process-env.json` | env envelope: `[{name, value}]` \| `[{name, secret_ref}]` | secret_ref resolves pipeline-side at launch (ADR 0013 invariant); plaintext values never for secrets — case for a `secret_ref`+`value` conflict rejection |
| `process-expectation.json` | `{run_within_seconds}` | mirrors `ingest-expectation.json`; scope question (I-63) decided by the spec — fixture named process-level; rename if per-source wins |
| `alert-kinds.json` | the closed enum of alert `kind` strings | **folds in the deferred M2 hygiene follow-up.** Current writers: `MONITOR_KINDS` = `ingest_inactivity, delivery_slo, connection_error, delivery_dead, ingest_failed, backfill_failed` + notify's `webhook_failed`; Phase 9 adds `process_failed, process_stalled`. Consumers to pin: pipeline writers, the app's `EXPECTATION_BREACH_KIND` branch (`lib/monitoring/api.ts`), and the monitoring UI's kind labels. Needs the pytest-side consumer that was the reason for deferral |

Rule reminder for implementation: these land WITH the schema code in the
same slice, not after.

## P9-E · Graph + lineage data design

### `/api/monitoring/flows` → `/api/monitoring/graph`

New member+-scoped route (same derived group scoping as `/flows`: connection
→ group; process → its own `group_id`; collections via `collection_settings`
with unowned-public semantics):

```
GET /api/monitoring/graph
{
  nodes: [
    { id: "conn:{uuid}",  type: "connection", label, protocol, status },
    { id: "coll:{id}",    type: "collection", label, archived, serving_enabled },
    { id: "proc:{uuid}",  type: "process",    label, enabled, last_run_state }
  ],
  edges: [
    { id: "assoc:{uuid}", type: "ingest",         from: "conn:…", to: "coll:…",
      enabled, expectation, flow_stats: {files, items, bytes, last_activity_at,
      last_latency_seconds, counts}, open_alert: bool },
    { id: "assoc:{uuid}", type: "deliver",        from: "coll:…", to: "conn:…", … },
    { id: "psrc:{uuid}",  type: "process_source", from: "coll:…", to: "proc:…",
      trigger_kind, open_alert },
    { id: "pout:{uuid}",  type: "process_output", from: "proc:…", to: "coll:…",
      output_items_24h }
  ]
}
```

One query per table, assembled in-route; `open_alert` derived from the open
alerts list exactly like the M2-D flows card (the alert row stays
authoritative). Cycle detection (ADR 0014 / I-64) runs over these same
edges at association-write time — the graph route and the cycle check share
the edge model, which is an argument for a small shared edge-loading module.

### Daily `flow_stats` history rollup (the lineage panel's 30-day strip)

**Decision needed: table vs derived. Recommendation: a table.** Deriving
30 daily buckets from `delivery_log`/`ingest_files` GROUP BYs is possible
today for live associations, but (a) M2-G's `history_retention` prunes
ledger rows (soft-deleted associations, itemless terminal deliveries), so
history silently thins; (b) at M3 rates (~2.6M items/day) a 30-day GROUP BY
per page view is exactly the unbounded-aggregation shape M2-A removed from
`listDeliveries`. Proposed:

- `stac_higher.flow_stats_daily` (app-owned DDL): `(subject_kind
  'association'|'process', subject_id uuid, day date, files int, items int,
  bytes bigint, delivered int, failed int, dead int, runs int,
  latency_p50_seconds real, latency_max_seconds real, PRIMARY KEY
  (subject_kind, subject_id, day))`.
- Written by a pipeline daily job (UPSERT per subject per day — same
  ledger+sweep idempotence as everything else); TODAY's partial bucket
  derived live from `flow_stats` counts, history read from the table.
- Retention: keep ~400 days, pruned by the existing hourly
  `history_retention` sweep (a bounded DELETE — no partitioning; the row
  count is subjects × days, tiny).

Open for the spec: whether process runs also land here (recommended: yes,
`subject_kind='process'`, `runs` column) so the lineage strip is uniform.
