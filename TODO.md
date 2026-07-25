# TODO — M1 work queue

The solo agent loop (AGENTS.md) works this file top-down: pick the **first
unchecked item**, one task per iteration, worktree off `ai/main`, `npm run
verify` (+ pipeline `pytest`/`ruff` when the pipeline is touched) before merge.
Scope sources: ROADMAP §9 "Named milestones" (M1), the Slice B-iii bullet, and
the referenced ISSUES/ADR entries.

## Pre-B-iii hardening wave

- [x] **I-39 pair** — make `associationUpdateSchema` direction-aware
      (discriminated like the create schema) and wrap the per-association body
      in `match_item` — including `parse_delivery_config` — in the isolation
      guard so a bad config skips that association, never the batch.
      (ISSUES I-39; app `associations/schemas.ts` + pipeline
      `delivery/matcher.py`, `dispatcher/loop.py`.)
- [x] **I-53 contract fixtures** — golden JSON fixtures (valid + invalid
      ingest/delivery config documents) in one shared location, consumed by
      both the vitest and pytest suites; add the "new cross-runtime shape ⇒
      new shared fixture" rule to AGENTS.md.
- [x] **I-51 soft-delete half (ADR 0009)** — `deleted_at` on
      connections/associations + credential/host-key scrub on delete; CASCADE →
      RESTRICT FKs; partial unique indexes on `deleted_at IS NULL`; pipeline
      not-deleted filters (scheduler, matcher, reference-source loader);
      counted impact previews on the DELETE routes + UI dialogs;
      reference-backed item removal on connection delete (marks their ledger
      rows so the asset route stops resolving them). The GC-dependent half
      (collection-delete asset removal, `archived`) stays in Phase 6.

## Slice B-iii — retry, dead-letter, crash recovery, concurrency

- [x] **Slice B-iii** per the expanded ROADMAP bullet: delivery retry →
      dead-letter (`next_attempt_at`, app-managed sweep), ingest crash
      recovery (I-52), claim→process→mark in one transaction (I-40),
      per-connection concurrency caps, live SFTP + FTP destination runs
      (I-45), plus the I-49 ride-alongs.

## Slice C — low latency + backfill

- [ ] **Slice C** — NOTIFY-woken dispatcher loop (replaces the 60s poll as the
      primary wake path; poll stays as fallback), bounded retry for the I-38
      visibility race, and user-initiated backfill as chunked bulk jobs.

## BFF (ADR 0008)

- [ ] **I-50 BFF** — built-in-catalog browser writes routed through an app
      server route with server-side session-token injection; register the
      route in the permission guard (RBAC + audit); client routing branch in
      `stacFetch`; UI-path leg in `tests/integration/` replacing the
      password-grant client for this case.

## Slice D — delivery UI

- [ ] **Slice D** — Data-flow tab delivery half: delivery association
      create/edit (path template, filters, payload options, `on_update`,
      overwrite, retry), enable/disable, redeliver action, delivery status
      surfaced from `delivery_log`.

## M1 gate

- [ ] **M1 demo rehearsal** — full loop driven through the UI on the
      auth-enforced stack (lead only: dev server + Docker + e2e): create both
      S3 connections, configure ingest + delivery on a collection, drop a file
      in the source bucket, watch the item appear in the catalog and the
      payload land in the destination bucket; kill the destination mid-run and
      show retry → dead-letter → redeliver. Record evidence in ROADMAP; then
      promote `ai/main → main` via PR.

## Discovered follow-ups

(append here during iterations)

- Fixed in the I-39 iteration: `api-assets.test.ts` 500'd with the Docker
  stack down (the Phase 4 reference seam added an unmocked Postgres query
  ahead of the offline presign path) — now stubs `lookupReferenceHref`.
  Watch for the same pattern if other unit-tested routes grow DB lookups.
- Fixed in the I-53 iteration: whitespace-only `source_path`/`path_template`
  passed Zod's `min(1)` but the Python parsers `.strip()`-reject — the Zod
  schemas now use a non-blank refine (found by writing the fixtures).
- ADR 0009 leaves ASSOCIATION-delete reference semantics implicit: deleting a
  reference-mode ingest association (connection kept) leaves its items serving
  from `source_href` with no update path — the same "unmanaged dead links"
  argument that justified removal on connection delete. Decide in Phase 6
  whether association delete should also remove (or the dialog should push
  toward disable-instead-of-delete).
- Deleted-connection `connection_checks` claims now skip the scrubbed row
  (`deleted_at IS NULL` in the drain's connection load), which strands such a
  check at `running`. Harmless (the app can no longer poll it — the parent
  404s) but worth a cleanup sweep when B-iii adds the stalled-job sweeps.
- B-iii live finding (fixed): SFTP/FTP `put` now creates parent directories;
  the delfer FTP test server does NOT chroot — FTP connections against it need
  `root_path=/ftp/demo` (compose comment corrected). Slice D's connection form
  help text should surface the root-path requirement for FTP servers.
- B-iii live observation: when a NEW item event lands while a row is `failed`
  awaiting retry, the event's delivery resets the attempt cycle (by design,
  I-44) and the sweep's requeue can overlap it — final state is correct
  (delivered), but the Slice D delivery-status UI should explain attempt
  counts as "attempts this cycle," not lifetime.
