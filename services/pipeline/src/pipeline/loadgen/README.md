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

## The two feed profiles

They measure different ceilings and **must be paired correctly** — the
association's metadata strategy and the feed's profile have to match:

| `setup --metadata` | `feed --profile` | measures |
|---|---|---|
| `defaults_only` | `opaque` | the plumbing ceiling: discover → fetch → itemize → outbox, no GDAL |
| `raster_auto` | `raster` | the real cost, including rio-stac EXTRACT over a genuine GeoTIFF |

Mismatch them and every item fails at EXTRACT, and the run measures the failure
path at full speed. (The first S-A run did exactly this: 30 of 30 failed.)

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
