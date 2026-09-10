# M3 load harness (`pipeline.loadgen`)

The synthetic feed + per-stage measurement behind **M3-S-A**, and the driver the
M3 gate rehearsal reuses. Findings live in
`docs/superpowers/specs/2026-08-31-m3-scoping-notes.md`.

Runs from the **host**, against the compose stack's published ports. Nothing
here executes inside the pipeline container, so a load run never perturbs the
process it is measuring beyond the work it offers it.

## Preconditions

1. The stack is up: `docker compose up -d --wait` (repo root).
2. `CREDENTIALS_MASTER_KEY` is exported — the harness seals the synthetic
   connection's credentials with the same envelope the app uses. Sourcing the
   repo-root `.env` is enough:
   `set -a; . .env; set +a`
3. The `stac_higher` schema exists. The **app** owns that DDL (ADR 0001) and
   creates it on its first API request, so the harness cannot make it: run the
   app once against this database if the tables are missing.

## Use

```sh
cd services/pipeline

# one association, copy mode, no GDAL in the path
uv run python -m pipeline.loadgen --label run1 setup --mode copy --metadata defaults_only

# saturation probe: offer everything at once, watch where the backlog forms
uv run python -m pipeline.loadgen --label run1 feed --rate 0 --count 2000 --asset-bytes 65536

# sustained probe: hold a target rate
uv run python -m pipeline.loadgen --label run1 feed --rate 30 --count 1800

sleep 60   # DISCOVER polls every 60s; since G-3 an s3 source settles on
           # FIRST sight (settle: auto), so one poll is enough
uv run python -m pipeline.loadgen --label run1 watch --seconds 300 --interval 20

uv run python -m pipeline.loadgen --label run1 teardown
```

`--label` namespaces one run's rows and objects, so runs can be compared
without a `docker compose down -v` between them. Every subcommand is
re-runnable: `setup` reuses what exists, `teardown` tolerates what is gone.
Since M3-B0 teardown is queue-aware: it resolves the probe collection's
partition name, deletes that partition's rows from `pgstac.query_queue` and
`query_queue_history`, and only then calls `pgstac.delete_collection`, so a
drain tick after a teardown never errors on a dropped partition (the JSON
output reports `queue_rows_cleared`).

## The two feed profiles

They measure different ceilings and **must be paired correctly** — the
association's metadata strategy and the feed's profile have to match:

| `setup --metadata` | `feed --profile` | measures |
|---|---|---|
| `defaults_only` | `opaque` | the plumbing ceiling: discover → fetch → itemize → outbox, no GDAL |
| `raster_auto` | `raster` | the real cost, including rio-stac EXTRACT over a genuine GeoTIFF |

Mismatch them and every item fails at EXTRACT, and the run measures the failure
path at full speed. (The first S-A run did exactly this: 30 of 30 failed.)

## Extractor profile (G-6)

`setup --metadata extractor --deliver` (pairs with `feed --profile opaque`)
installs a third profile: a fixed-id `processes` row plus one deployed
revision — a pass-through extractor that copies each staged draft item back
out with a `datetime` and a point geometry filled in when missing, changing
nothing finalize forbids changing (id, collection, asset hrefs). Ingest routes
every item through this process instead of the inline EXTRACT path, so the
run measures the G-6 process-per-batch handoff rather than rio-stac.

This profile needs the process-run container image to actually exist (the
Docker executor, ADR 0013) — `setup` only writes rows, it does not build or
pull an image. The number to read while a run is going is
`ingest_files_extracting` staying bounded while `itemized` (and `catalog
items`) keep climbing: a persistently growing `ingest_files_extracting` means
runs are queueing faster than the executor drains them, not that ingest itself
is slow. `teardown` removes the installed process (runs → revisions → the
process row) alongside the usual association and connection cleanup.

## Reading the pgstac queue (M3-A)

With `use_queue` on, `catalog items` climbs at the write rate while `BACKLOG
pgstac queue` stays flat and small — it is bounded by the number of partitions
written since the last drain, not by items. Two things to record per run: the
drain tick's mean seconds (`pipeline.pgstac_queue_drain` in the per-job table)
against `pgstac_partitions` in the end-of-window counts, because that cost
scales with partition count and is what sets the drain cadence; and that the
queue returns to 0 within a tick of the feed ending. A queue that only grows
means the drainer is not running — check `PGSTAC_QUEUE_DRAINER` and the
`pipeline_pgstac_query_queue_oldest_seconds` gauge on `/metrics`.

## Isolating stages

`seed-outbox` inserts `item_events` rows directly, so the dispatcher can be
measured without ingest competing for the same worker. The seeded events name
items that do not exist in pgstac, so this measures **dispatcher drain only** —
delivery needs real items, i.e. a `setup --deliver` plus a `feed`.

## Reading the report

- **per-second** is over the whole window; **per-interval trend** is the last
  twelve sample-to-sample rates, which is where a decaying stage shows itself.
- `reset` means a counter went backwards (the pipeline restarted mid-window).
  Deliberately not rendered as a negative rate.
- `BACKLOG *` rows are absolute counts rated per second: a persistently
  positive value is a queue growing, and that is the bottleneck.
- Per-job means are **windowed**, not lifetime. The histogram is cumulative
  since process start, so a lifetime mean hides exactly the improvement a load
  run is trying to see.

## What the harness does NOT do

- It does not create the `stac_higher` schema (the app owns that DDL).
- It does not drive a **process** (Phase 9) leg. The M3 gate wants a synthetic
  process at ~50% source share; that is a gate-rehearsal addition, not a
  measurement the S-A findings needed.
- It does not run against the auth-enforced overlay. S-A measured the plain
  stack; the gate is specified against the enforced one.
