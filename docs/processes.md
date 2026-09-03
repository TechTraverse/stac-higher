# Writing a process

A **process** is code the platform runs when items land in a source collection
(or on a schedule), which publishes new items into an output collection. This
is the contract your code is held to.

Related: [ADR 0013](decisions/0013-process-executor-isolation.md) (isolation),
[ADR 0014](decisions/0014-process-output-path.md) (output path), and the
Phase 9 design spec.

## Where your code runs

In its own container, on a platform-built image, with:

- **no network.** Every process runs `isolated` this slice — platform
  storage only; the items that triggered you are staged in (see "What your
  run receives" and "Network access" below).
- **no platform credentials.** No database URL, no master key, no
  platform object-store keys. Your environment contains exactly: the env
  entries on your revision, storage credentials scoped to your own run and
  to reading your source collections, and the variables below.
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

You can list, read and write inside that prefix, and **read** the canonical
prefixes of your process's source collections (`assets/{collection}/…`) — or,
for an extractor, of the collection its association ingests into — nowhere
else. Attempting anything outside that fails as a permissions error, not a
silent no-op.

## What your run receives

Before your container starts, the platform stages the items that triggered
the run and writes a manifest describing them:

| Variable | Meaning |
|---|---|
| `STAC_HIGHER_INPUT_PREFIX` | `staging/runs/{run_id}/inputs/` — read-only from your point of view |
| `STAC_HIGHER_INPUT_MANIFEST` | the key of this batch's `manifest.json` |

```python
import json, os, boto3
s3 = boto3.client("s3")
bucket = os.environ["STAC_HIGHER_OUTPUT_BUCKET"]
manifest = json.loads(
    s3.get_object(Bucket=bucket, Key=os.environ["STAC_HIGHER_INPUT_MANIFEST"])["Body"].read()
)
for entry in manifest["items"]:
    item = entry["item"]                      # the full STAC item
    for name, asset in entry["assets"].items():
        obj = s3.get_object(Bucket=asset["bucket"], Key=asset["key"])   # or rasterio: /vsis3/{bucket}/{key}
```

Every asset has a `bucket` and `key` your credentials can read, whether the
bytes are platform-held (`staged: false`, the canonical object) or were
fetched from elsewhere for this run (`staged: true`, a copy under your inputs
prefix). `href` is the catalog's own href, for provenance. `op` is the event
that triggered the run (`insert` | `update`). An item deleted between trigger
and run appears under `skipped`, not `items`. A cron run has an empty
`items` list. Assets with a relative or missing href are omitted from
`assets` — they are not locations you can read. A canonical `/api/assets/...`
href can still arrive `staged: true`: when its item is reference-mode, the
bytes live at the connection's own source rather than the platform bucket, so
the pipeline stages a copy from the ingest ledger's `source_href` instead of
granting a read to a canonical key that does not exist. The manifest's
top-level `kind` is `"transform"` for an ordinary process run or `"extract"`
for an extractor run (see "Extractors" below).

The manifest shape is pinned by
`tests/contract-fixtures/process-input-manifest.json` and ADR 0018; new keys
may appear in later versions, so ignore what you do not know.

## Network access

A revision's runtime carries a `network` block:

```json
"network": { "level": "isolated", "hosts": [] }
```

| Level | Meaning |
|---|---|
| `isolated` | Platform storage only. Inputs are staged in. **The only level accepted this slice, and the default.** |
| `inputs` | `isolated` plus egress to the hosts of the input assets' hrefs (derived at launch; inputs no longer staged). Arrives with the egress proxy. |
| `hosts` | `isolated` plus the `hosts` you list (bare hostnames). Arrives with the egress proxy. |
| `open` | Unrestricted egress. Arrives with the egress proxy. |

`hosts` must be non-empty for `hosts` and empty otherwise. The deployment
sets a ceiling, `PROCESS_NETWORK_MAX` (default `isolated`): a deploy above it
is refused at the form, and the pipeline refuses to launch a revision above
it regardless — it fails the run naming the level and the cap rather than
silently running with less network than you asked for.

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

## Extractors

A process created with **kind: extractor** is not wired to source and output
collections. It is selected on an **ingest association** (a collection's Data
flow tab → metadata strategy *extractor process*), and the platform hands it
every item that association brings in — **before** the item is catalogued —
so it can fix up what the built-in extraction could not infer: the datetime
from a filename, a footprint from a projection the platform does not read,
product-specific properties.

Your run receives the same manifest as a transform, with two differences:

- `kind` is `"extract"`.
- each `items[].item` is a **draft**: the id, collection and assets the
  platform built, a `datetime` that may be `null`, and a `geometry` that may
  be `null`. Asset `bucket`/`key` work as for a transform (reference-mode
  files are staged in for you).

Write **one `{item_id}.json` per input item** into your output prefix, and
nothing else — no asset files. The platform then validates and catalogues
each document as the ingest would have. These must not change, or the item
is refused with the reason on its ingest ledger row:

- `id` and `collection`;
- the set of asset keys, and every asset `href`.

Everything else is yours — with two exceptions that are also required:
`geometry` and `properties.datetime` **must both be set (non-null)**; an
output that leaves either null is refused with the reason on its ledger row,
same as an immutability violation. `bbox`, any other `properties`, and
per-asset metadata are all optional. An input with no output document is
refused too.

**Failure.** If the run fails, times out, or dies, every file in its batch
is marked `failed` on the ingest ledger with the run's error — there is no
fallback to the draft — and the ingest retries the file like any other
failure. Defaults for an extractor: 600 runs/hour and a 120 s timeout;
back-to-back files coalesce into one run while a run is still queued.

A minimal extractor:

```python
import json, os, boto3

s3 = boto3.client("s3")
bucket = os.environ["STAC_HIGHER_OUTPUT_BUCKET"]
prefix = os.environ["STAC_HIGHER_OUTPUT_PREFIX"]
manifest = json.loads(
    s3.get_object(Bucket=bucket, Key=os.environ["STAC_HIGHER_INPUT_MANIFEST"])["Body"].read()
)
for entry in manifest["items"]:
    item = entry["item"]
    item["properties"]["datetime"] = "2026-09-03T04:01:17Z"   # from the filename, say
    item["geometry"] = {"type": "Point", "coordinates": [-75.0, 0.0]}
    item["bbox"] = [-75.0, 0.0, -75.0, 0.0]
    s3.put_object(Bucket=bucket, Key=f"{prefix}{item['id']}.json", Body=json.dumps(item).encode())
```

## Environment and secrets

A revision carries an `env` list, edited on the process page and deployed with
the code. Each entry is **either** a literal value **or** a secret reference —
never both, and never neither:

```json
[
  { "name": "TILE_SIZE", "value": "512" },
  {
    "name": "SOURCE_TOKEN",
    "secret_ref": { "connection_id": "…uuid…", "key": "secret_access_key" }
  }
]
```

- A **literal** is stored in the revision exactly as written and is visible to
  anyone who can read the process. It is for non-secret configuration.
  **Never put a secret in `value`.** A `value` beside a `secret_ref` is
  rejected outright rather than resolved by precedence, precisely so a
  plaintext secret cannot survive next to the reference that replaced it.
- A **secret reference** names a key inside a connection's encrypted
  credentials. Nothing is resolved when you deploy: the pipeline decrypts it at
  run launch and injects it straight into the run container's environment. It
  never enters the platform worker's own process, never comes back through the
  API, and never appears in audit detail.

Names must be POSIX environment-variable identifiers (`[A-Za-z_][A-Za-z0-9_]*`)
and must be unique within the revision.

### What a secret reference can reach — and why that is the whole namespace

A `secret_ref` points into a **connection's** credentials envelope, so the keys
you can name are exactly that protocol's credential fields:

| Protocol | Keys |
|---|---|
| `s3` | `access_key_id`, `secret_access_key`, `session_token` |
| `ssh`, `sftp` | `username`, `password`, `private_key`, `passphrase` |
| `ftp`, `ftps` | `username`, `password` |

and the connection must belong to **the process's own group**. The deploy is
refused otherwise — a missing connection and one in another group give the same
answer, so the form cannot be used to discover what exists elsewhere.

This limit is deliberate, not an unfinished edge. The platform has one place
where operator secrets are already encrypted, group-owned, audited and
rotatable, and that is `connections`. A general-purpose secret store is a
separate decision (a new owner, a new lifecycle, a new blast radius) — it would
be introduced as such, not by quietly widening what `{connection_id, key}`
means. If your process needs a credential that is not a connection's, say so:
that is a feature request, not a workaround to invent.

If a reference cannot be resolved at launch, the run is marked **dead**
immediately rather than started with the variable unset — user code must never
silently receive an empty string where a credential was intended.

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
- **Relative hrefs must be plain filenames.** `../x.tif` and `a/b.tif` are
  not treated as your files — an item naming one is **rejected**, because
  publishing it verbatim would put a dangling href in the catalog.
- **Inputs are not outputs.** Nothing under `inputs/` is published, and an
  output item cannot name an input file (`inputs/…`) as its asset — copy the
  bytes if you need them published.

## When does a run start?

Within seconds of the triggering item landing. The catalog write wakes the
dispatcher over a Postgres `NOTIFY`, the dispatcher queues your run, and the
run is handed straight to the executor — no clock in between. A one-minute
sweep still exists, but only to recover a run whose immediate job was lost, to
release one the rate ceiling deferred, and to retry a failed one.

Items that arrive for the same source while a run is still **queued** join
that run's batch rather than starting another. Once a run is claimed it is
out of reach, so anything arriving after that starts the next run — you never
lose items to a batch already executing, and you never see the same item in
two runs.

## Rate ceiling

Each process has a `max_runs_per_hour`. A trigger over the ceiling does not
drop work: the run is **deferred**, and further triggers **coalesce** into that
one deferred run rather than queueing more. A deferred run is deliberately NOT
dispatched immediately — that is the ceiling doing its job. A sustained breach
shows as a "rate limited" badge on the run.

## Failure and retry

A failed run retries on its revision's `retry.max_attempts` budget, then goes
`dead`. A run whose inputs could not be staged (a remote file unreachable)
fails the same way — the ledger `error` names the item and asset — and no
container ever started. A revision whose `network.level` exceeds the
deployment cap dies immediately: that is configuration, not a fault. A dead run can be re-run from the UI — which re-executes **the same
revision that failed**, not whatever is current. If you deployed a fix, trigger
a new run; re-running an old row will run the old code.

A run stranded by a worker or executor crash is returned to `queued` by the
stall sweep, so the crash direction is safe: a run may execute twice, never
zero times. The container that crash left behind is removed separately, by the
orphan reaper — so a second attempt does not collide with the first attempt's
leftovers.
