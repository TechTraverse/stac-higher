# ADR 0020 — pgstac session GUC pairings: the writer and the drainer carry OPPOSITE settings

- **Status:** accepted (2026-09-09 — records what M3-A shipped 2026-09-08 and
  the spec correction in `2f81348`; implemented in
  `services/pipeline/src/pipeline/db/pgstac_session.py` and
  `services/pipeline/src/pipeline/stac/query_queue.py`)
- **Related:** ADR 0001 (the pipeline never runs DDL — these are `SET`s on
  the pipeline's own sessions, not schema), ADR 0006 (the pinned
  `pypgstac[psycopg]==0.9.11` whose `PgstacDB(use_queue=True)` seam this
  uses), M3 design spec
  `docs/superpowers/specs/2026-09-01-m3-noaa-scale-design.md` §4.2 / §4.4
  (with the 2026-09-08 corrections), `TODO.md` follow-up "M3-A landed
  2026-09-08".

## Context

pgstac 0.9.11 has two settings the bulk write path cares about, and
`pgstac.get_setting` resolves each one as `conf jsonb → session GUC
(current_setting) → pgstac_settings table`, so a session-level `SET`
overrides the table for that session only:

- **`pgstac.use_queue`** — read by `run_or_queue` **in the calling
  session**. When ON, the item trigger's `update_partition_stats_q` queues
  `SELECT update_partition_stats('_items_<key>', …)` into
  `pgstac.query_queue` instead of running it inline. This is the whole M3-A
  win: 2–3.5 → ~22 items/s measured.
- **`pgstac.update_collection_extent`** — read **inside**
  `update_partition_stats` (`IF get_setting_bool('update_collection_extent')`),
  which under the queue no longer runs in the writer's session at all. It
  runs later, in whichever session executes `CALL
  pgstac.run_queued_queries()`. Its extent-refresh branch calls
  `run_or_queue` **again**, so it also re-reads `use_queue` — in the
  draining session.

The lead's original instruction (spec §4.2, 2026-09-01) was "whatever sets
`use_queue` should also set `update_collection_extent`", on one connection.
That is a reasonable reading of the pgstac docs and turned out not to be
implementable: the two settings are consumed in different sessions. Set only
on the writer, `update_collection_extent` was a silent no-op — nothing
errored, the queue drained, throughput improved, and collection extents
stayed at whatever they were created with (locally, `[-180,-90,180,90]`),
which is the gap §4.4 exists to close.

## Decision

Two connection kinds, two **opposite** pairings, each self-enforcing:

| connection | `pgstac.use_queue` | `pgstac.update_collection_extent` | where |
|---|---|---|---|
| the pgstac **writer's** pool (`psycopg_pool.ConnectionPool`, sync, `pypgstac`'s `PgstacDB(pool=…, use_queue=True)` seam) | **ON** | ON (inert here — never read in this session; kept because it is harmless and the spec's record) | `pipeline/db/pgstac_session.py`, `PGSTAC_SESSION_SQL`, applied by the pool's `configure` hook, which COMMITs (a `SET` is transactional; the pool's reset would undo an uncommitted one) |
| the queue **drainer's** short-lived AUTOCOMMIT connection (`CALL pgstac.run_queued_queries()` is a PROCEDURE that COMMITs inside itself and cannot run in a transaction block) | **explicitly FALSE** | **ON** | `pipeline/stac/query_queue.py`, `DRAIN_CONNECTION_SQL`, applied per connection before the CALL |
| M3-B's async repo pool (`pipeline/db/pool.py`, transactional, serves the `stac_higher.*` repos) | carries the writer pairing through the same `configure_pgstac_session_async` hook — harmless, because no repo statement fires the pgstac item trigger | — | the drainer's repo stays OFF this pool |

Two details are load-bearing and look like mistakes to a fresh reader:

1. **`use_queue` is explicitly `FALSE` on the drainer, not merely unset.**
   `get_setting` COALESCEs an unset GUC through the `pgstac_settings` table
   (or an `ALTER DATABASE` / `ALTER ROLE SET`). An operator enabling the queue
   globally would otherwise silently re-arm the bug in 2. An explicit FALSE
   makes the drainer's pairing independent of that default.
2. **The drainer must never run with `use_queue` ON**, because the extent
   branch re-enters `run_or_queue`. A drain session with the queue on would
   re-queue the extent `UPDATE` instead of executing it — one hop further on
   every tick, forever, with no error and a queue that never empties.

The settings are session GUCs shipped with the release — never a
`pgstac_settings` row, never a deployment step — so the "a deployment
silently runs 10× slower" failure mode does not exist and every session
that is not the bulk writer (search, app BFF writes, `psql`, e2e) keeps
pgstac's inline statistics.

## Consequences

- **Invariant:** the writer's connections carry `use_queue` ON; the drain
  connection carries `update_collection_extent` ON **and** `use_queue`
  explicitly FALSE. Any future pool, hook or "cleanup" that unifies the two
  pairings reintroduces one of the two silent failures above. Both call
  sites comment this and point here.
- Collection extents are refreshed by the drain tick and by nothing else —
  at most one tick stale, indefinitely stale if the drainer stops, which is
  why the stale-queue WARNING (`pipeline_pgstac_query_queue_oldest_age_seconds`)
  matters twice.
- Any test or tool that creates and drops a pgstac collection must clear
  that partition's `query_queue` / `query_queue_history` rows before
  `delete_collection` (the writer queued them; the drain would error on the
  dropped partition). `tests/test_integration_pgstac_queue.py`,
  `tests/test_integration_itemize.py` and `pipeline.loadgen teardown`
  (M3-B0) do.
- Proven, not argued: `tests/test_integration_pgstac_queue.py` (DB-gated)
  asserts the extent is still the world bbox before the drain and the
  item's bbox after.

## Revisit

If a pgstac release moves the `update_collection_extent` check out of
`update_partition_stats` (so it is read in the writer's session), or makes
`run_or_queue` non-reentrant, the drainer's explicit-FALSE stops being
load-bearing. Re-test with the DB-gated queue test on any pgstac pin bump
(ADR 0006 already requires the upsert path be re-tested).
