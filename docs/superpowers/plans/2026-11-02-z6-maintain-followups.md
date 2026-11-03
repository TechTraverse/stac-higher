# Z-6 follow-ups: cube maintenance lock hold, failed republish, reads (#115)

Issue: #115. Spec: `docs/superpowers/specs/2026-10-03-virtual-cube-sink-design.md` §10. ADRs 0022/0023. Z-6 plan: `docs/superpowers/plans/2026-10-09-z6-cube-maintain.md`.

**Goal:** keep the PR #105 review's six non-blocking findings from turning into real costs before the Z-9 live gate. No migration, no app change.

## Decisions

1. **The size readout leaves the lock as its own job.** Procrastinate holds a job's `lock` until the job ends, so a listing cannot release it halfway. New task `pipeline.cube_measure_sink(cube_sink_id, started_at)`:
   - no `lock`; `queueing_lock` `cube-measure:{id}`; no retry (next hour);
   - enqueued by `cube_maintain_sink` **after** it records its summary, and only when the repository exists;
   - lists `_cube/`, then merges `sizes`, `total_objects`, `total_bytes`, `durations_ms.list` and the `repo_size` attention into **that run's** `last_maintenance`. That's one `UPDATE … WHERE last_maintenance->>'started_at' = %s`: a measurement never lands on a newer run's summary;
   - a listing failure merges `measure_error` instead and changes no status.

   `warn_bytes` stays in the summary, written by maintenance. The republish **stays under the lock**: it is one short DB transaction, and keeping it next to the trim keeps "record, then publish" ordered against appends.
2. **A failed republish no longer hides the run.** `after_batch` errors are caught: `publish_outcome = "error"`, `publish_error` (truncated), attention `publish_failed`. The run carries on (GC attention, measure job).
3. **Expiry spares the snapshot the collection asset names.** New optional dep `published_snapshot: (sink) -> Awaitable[str | None]`. Production reads `pgstac.collections.content` for the sink's cube collection and returns the `version` of the asset whose `stac_higher:cube_sink_id` is this sink. `maintain_repository`/`expiry_cutoff` take `spare=(recorded, published)`. That covers a publish that keeps failing after a recorded trim, and an append whose publish failed.
4. **No second state read when nothing was trimmed.** `maintain_repository` reuses the pre-trim `CubeState` when `trimmed == 0`. After a real trim it still re-reads (rare: a stopped source).
5. **One boto3 client per lister:** `platform_lister(settings)` builds its client lazily, once, in the closure (boto3 clients are thread-safe).
6. **Bounded expiry walk.** Verified on icechunk 2.3.0: `expire_snapshots` rewrites the history, so a windowed sink's history stays short (9 → 4 in a probe). The long walk is a first run over an old repository, or spared ids missing from the history. Cap the walk at `MAX_EXPIRY_WALK = 1000` snapshots. If the cap is hit before every spared id is found, the cutoff drops to the oldest snapshot walked, which protects everything seen (conservative), and the run logs a WARNING.
7. **One paginator:** `storage/platform.py::_iter_objects(client, bucket, prefix)` yields the raw `Contents` dicts. `list_keys`, `list_objects` and `list_sizes` become one-line projections over it.

## Tasks

- [ ] **T1 paginator + client (D5, D7):** `_iter_objects`; three projections; `platform_lister` caches its client. Tests: the existing `list_*` tests stay green; one test that a lister builds one client across two calls.
- [ ] **T2 state reuse + walk cap (D4, D6):** in `maintain_repository`, reuse the state when `trimmed == 0`; `expiry_cutoff(..., spare=...)` with the cap. Tests: a spared snapshot beyond the cap → conservative cutoff + WARNING; a spared `published` id survives expiry.
- [ ] **T3 publish failure + published snapshot (D2, D3):** catch around `after_batch`; `published_snapshot` dep (fake + Pg); thread `spare` through. Tests: publisher raises → `status=attention`, `publish_failed`, sizes still measured; asset-named snapshot survives expiry after the tip moved on.
- [ ] **T4 measure job (D1):** `run_cube_measure`, `MeasureDeps`, `CubeRepo.record_measurement` (fake + Pg), job registration, enqueue from maintenance with `started_at`. Tests: maintain enqueues measure only when the repo exists and after recording; measure merges into the matching run and is a no-op for a stale `started_at`; `repo_size` flips `ok`→`attention` but never overrides `failed`; listing error → `measure_error`. Existing size tests run maintain then measure.
- [ ] **T5 docs:** `docs/monitoring.md` (`last_maintenance` keys: `publish_failed`, `publish_error`, `measure_error`; sizes arrive from the follow-up job), the `jobs/cubes.py` and `cubes/maintain.py` docstrings, `docs/backend.md` job list if it lists cube jobs.

## Gates

`uv run pytest`, `uv run ruff check .`, `npm run verify`. The DB-gated `test_integration_cubes_repo.py` gets a case for `record_measurement` (CI's pgstac job runs it).
