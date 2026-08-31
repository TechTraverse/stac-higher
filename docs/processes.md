# Writing a process

A **process** is code the platform runs when items land in a source collection
(or on a schedule), which publishes new items into an output collection. This
is the contract your code is held to.

Related: [ADR 0013](decisions/0013-process-executor-isolation.md) (isolation),
[ADR 0014](decisions/0014-process-output-path.md) (output path), and the
Phase 9 design spec.

## Where your code runs

In its own container, on a platform-built image, with:

- **no network by default.** A process that needs egress is a deployment
  decision (`PROCESS_NETWORK`), not something it inherits.
- **no platform credentials.** No database URL, no master key, no
  platform object-store keys. Your environment contains exactly: the env
  entries on your revision, storage credentials scoped to your own run, and
  the two variables below.
- **memory and wall-clock limits** from your revision's runtime settings,
  enforced by the platform. Exceeding the timeout kills the run.

Two variables always present:

| Variable | Meaning |
|---|---|
| `STAC_HIGHER_RUN_ID` | This run's id — matches the run row in the UI |
| `STAC_HIGHER_PROCESS_ID` | The process this run belongs to |

Storage credentials arrive under the standard AWS names (`AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_ENDPOINT_URL`), so ordinary
tooling picks them up with no special casing. They are valid **only** for your
run's output prefix, named in:

| Variable | Meaning |
|---|---|
| `STAC_HIGHER_OUTPUT_BUCKET` | The bucket to write to |
| `STAC_HIGHER_OUTPUT_PREFIX` | `staging/runs/{run_id}/` — the only place you may write |

You can list, read and write inside that prefix and nowhere else. Attempting
anything outside it fails as a permissions error, not a silent no-op.

## How to publish an item

Write, into your output prefix:

1. **One `.json` object per item** — a STAC item document.
2. **The asset files it references**, as siblings in the same prefix.

Asset hrefs are **plain filenames**, resolved against your prefix:

```python
import json, os, boto3

bucket = os.environ["STAC_HIGHER_OUTPUT_BUCKET"]
prefix = os.environ["STAC_HIGHER_OUTPUT_PREFIX"]
s3 = boto3.client("s3")

s3.put_object(Bucket=bucket, Key=f"{prefix}mask.tif", Body=raster_bytes)
s3.put_object(
    Bucket=bucket,
    Key=f"{prefix}S2A_001_mask.json",
    Body=json.dumps({
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": "S2A_001_mask",
        "collection": "cloud-masks",          # must be an OUTPUT of this process
        "geometry": {...},
        "properties": {"datetime": "2026-08-31T00:00:00Z"},
        "assets": {"data": {"href": "mask.tif"}},   # sibling filename
        "links": [],
    }).encode(),
)
```

The platform then validates each document, checksums and moves the assets into
canonical storage, rewrites the hrefs to the asset service, and upserts the
item. You never write to the catalog directly, and the published href is
always `/api/assets/{collection}/{item}/{filename}` — never a storage URL.

An **absolute** href (`https://…`, `s3://…`) is left exactly as written: that
is how you publish a reference-style item whose bytes live elsewhere.

## Rules that will surprise you

- **Only a successful run publishes.** A non-zero exit means nothing is
  published, even if you wrote perfectly good files — they age out of staging.
  Half-publishing a crashed run's intent would put items in the catalog that no
  successful run stands behind.
- **You may only publish into the process's configured output collections.**
  Naming any other collection rejects that item, so a typo cannot write into
  someone else's collection.
- **An item referencing a file you did not write is rejected**, because
  publishing it would put a broken asset href in the catalog. Its siblings are
  unaffected — rejection is per item.
- **A rejected item is simply not published.** Nothing is deleted and nothing
  is rolled back: your outputs were never in the catalog to begin with. (This
  differs from push ingest, whose items are already in the catalog when
  finalize runs.)
- **A run that writes no `.json` at all is a success**, recorded as publishing
  nothing. A filter process that emits nothing for a batch is normal.
- **If every output is rejected, the run is marked dead** even though your code
  exited 0 — otherwise the flow would report as healthy while publishing
  nothing.
- **Relative hrefs cannot escape your prefix.** `../x.tif` and `a/b.tif` are
  not treated as your files.

## Rate ceiling

Each process has a `max_runs_per_hour`. A trigger over the ceiling does not
drop work: the run is **deferred**, and further triggers **coalesce** into that
one deferred run rather than queueing more. A sustained breach shows as a "rate
limited" badge on the run.

## Failure and retry

A failed run retries on its revision's `retry.max_attempts` budget, then goes
`dead`. A dead run can be re-run from the UI — which re-executes **the same
revision that failed**, not whatever is current. If you deployed a fix, trigger
a new run; re-running an old row will run the old code.

A run stranded by a worker or executor crash is returned to `queued` by the
stall sweep, so the crash direction is safe: a run may execute twice, never
zero times.
