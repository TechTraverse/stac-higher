# ADR 0018 — Process inputs and network profiles

- **Status:** accepted (2026-09-01 — by the GOES GeoColor loop design spec,
  `docs/superpowers/specs/2026-09-01-goes-geocolor-loop-design.md` §3/§4;
  implemented by slice G-2)
- **Related:** ADR 0013 (executor isolation — the boundaries this ADR works
  inside), ADR 0014 (output path — the mirror image of this input path),
  ADR 0005 (canonical key layout), ISSUES I-90, I-91.

## Context

Phase 9 gave a process run a sandbox, run-scoped credentials for its own
staging prefix, limits, logs and a rate ceiling — and **no way to see the
items that triggered it**. `process_runs.input_items` held bare item ids; the
container's credentials could read only `staging/runs/{run_id}/`; its network
was `none`. A GOES GeoColor process therefore could not open the netCDF it was
triggered by, whether the bytes sat in the platform bucket (copy-mode ingest)
or in NOAA's public bucket (reference-mode ingest).

ADR 0013's invariants bound the answer: user code never receives platform
keys, egress obeys the connections policy, the run's environment is assembled
in one place. The interface chosen here is one user code will depend on, so it
is recorded as a decision rather than left to the implementation.

## Decision

### The manifest (cross-runtime contract, `tests/contract-fixtures/process-input-manifest.json`)

Before the container starts, the pipeline writes
`staging/runs/{run_id}/inputs/{batch_id}/manifest.json` and names it in two
environment variables:

| Variable | Meaning |
|---|---|
| `STAC_HIGHER_INPUT_PREFIX` | `staging/runs/{run_id}/inputs/` |
| `STAC_HIGHER_INPUT_MANIFEST` | the key of this batch's `manifest.json` |

```json
{
  "version": 1,
  "run_id": "…", "process_id": "…", "batch_id": "…",
  "kind": "transform",
  "items": [
    {
      "collection": "goes19-abi-l2-mcmipc",
      "op": "insert",
      "item": { "…full STAC item as stored in pgstac…" },
      "assets": {
        "data": {
          "bucket": "stac-higher",
          "key": "staging/runs/{run_id}/inputs/{batch_id}/{item_id}/OR_ABI-L2-MCMIPC_…nc",
          "staged": true,
          "href": "https://noaa-goes19.s3.us-east-1.amazonaws.com/ABI-L2-MCMIPC/…nc"
        }
      }
    }
  ],
  "skipped": [ { "item_id": "…", "collection": "…", "reason": "not_found" } ]
}
```

`items[].assets` is keyed exactly like `item.assets` and gives, for every
asset the run can read, a `bucket` and `key` its own credentials open. That is
the whole author-facing interface: loop over items, open assets by bucket and
key; never branch on where the bytes came from. `href` is the catalog's href,
for provenance. `op` is the outbox operation (`insert` | `update`; `unknown`
for a run row written before this ADR). An item deleted between trigger and
run is listed under `skipped`, not `items`. A cron run has `items: []`.
Assets with a relative or missing href are omitted from `assets` — they are
not locations. `batch_id` exists so a run may one day receive more than one
batch (warm runs); this slice always writes exactly one.

The manifest is **producer-golden**: the pipeline (`pipeline/process/inputs.py`)
is its only writer and user code its reader, so only the pytest suite consumes
the fixture. Version bumps are additive — readers ignore unknown keys.

### Two ways bytes reach the run, decided per asset

- **Platform-held bytes** — the href is a canonical `/api/assets/{c}/{i}/{f}`
  (copy-mode ingest output, another process's output, a manual upload):
  **no copy.** The key is the canonical `assets/{c}/{i}/{f}` and the run's
  STS session policy gains a read-only statement (`s3:GetObject`, plus
  `s3:ListBucket` conditioned on the prefix) for each **source collection's**
  canonical prefix `assets/{collection}/*`. Granting the collection, not the
  item, keeps the inline policy small and matches the relationship the
  operator created by wiring the collection as a source. At most **8** source
  collections per run (I-90); more refuses to mint.
- **Remote bytes** — any other absolute href (reference-mode ingest, NODD):
  the pipeline fetches the object into
  `inputs/{batch_id}/{item_id}/{filename}` and the manifest says
  `staged: true`. The fetch goes **through the matching enabled reference-mode
  ingest association's connection adapter** (its credentials or anonymity and
  the egress policy come with it); when no association claims the href, an
  egress-checked public HTTPS GET (`connections/http_fetch.py` — `resolve_pinned`
  first, no redirects, size-capped) is the fallback (I-91).

### Ordering and failure

Planning and staging happen in `run_one` **after** the revision parses and the
network cap passes and **before** credentials are minted or a container
exists: a staging failure means the run never started. The order is: parse →
network cap → plan (repo reads) → stage remote inputs (bounded concurrency,
`PROCESS_INPUT_STAGE_CONCURRENCY`) → write the manifest LAST → mint + launch.
A cap violation or an undescribable input (`InputPlanError`) is
configuration and the run dies; a staging failure spends an attempt and
retries like a failed run, its ledger `error` naming the item and asset.

### Publishing consequences

The finalize resolver ignores everything under `inputs/` — the manifest and
any staged documents are never output candidates. An output item may not
name an input file as its own asset: a **relative** href that is not a plain
filename (`inputs/…`, `a/b.tif`, `../x`) **rejects the item** (`missing_asset`)
rather than publishing a dangling href; root-relative and scheme'd hrefs stay
reference-style pass-through. Staged inputs age out with the run prefix under
the staging TTL; a successful finalize deletes the prefix as before.

### Network profile

Every revision's runtime carries `network: { level, hosts }`, with `level` in
the ordered set `isolated < inputs < hosts < open` and `hosts` non-empty iff
the level is `hosts` (bare hostnames only). `PROCESS_NETWORK_MAX` (pipeline
env; `PUBLIC_PROCESS_NETWORK_MAX` mirrors it to the UI) is the highest level a
deployment permits. The app refuses a deploy above the cap; **the pipeline
enforces it independently at launch** — a revision above the cap fails the
run naming the level and the cap, never launches lower silently.

**Slice 1 ships the field with `isolated` only**: the write gate accepts no
other level, the reader accepts all four, the fixture pins both, and the deploy
card shows the higher levels disabled. `isolated` is realised as today's
`PROCESS_NETWORK` compose network; the higher levels arrive with the egress
proxy (spec §11), which is ADR 0013's "resolve_pinned analog at the network
boundary". No revision written in slice 1 changes meaning in slice 2.

## Consequences

- Remote inputs cost one copy per file until the `inputs` level exists; for
  GOES that is ~50 MB per MCMIPC file, accepted.
- The inline STS policy bounds the number of source collections a single run
  may read (I-90); the cloud backend must revisit if real STS's 2048-character
  cap bites before MinIO's laxer one does.
- The manifest is versioned additively; a breaking change is a new `version`
  and a new fixture, never an in-place edit.
- Legacy run rows whose `input_items` lack `collection_id` still run when the
  process has exactly one source collection; with more they die as
  `unusable inputs` rather than guess.

## Alternatives rejected

- **Per-item read grants** — precise, but the inline policy size scales with
  batch size and real STS caps it at 2048 characters; collection-prefix
  grants scale with `process_sources`, which is small by construction.
- **Copying platform-held bytes into `inputs/`** — uniform, but doubles every
  copy-mode asset's storage traffic for no isolation gain; the session policy
  already draws the boundary.
- **Opening the run's network so code fetches its own inputs** — contradicts
  ADR 0013 (egress must obey the connections policy) and would put the
  connection's credentials in user code's hands.
- **Passing items inline in the environment** — a 50-item batch of multi-KB
  documents does not fit an environment block, and would make the manifest a
  second, divergent shape once warm runs need more than one batch.

## Invariants added to `docs/decisions/README.md`

- A run's read access outside its own prefix is limited to the canonical
  prefixes of its **source collections**, granted in the STS session policy —
  never by platform keys.
- A revision's `network.level` never exceeds `PROCESS_NETWORK_MAX`; the
  pipeline enforces it at launch independently of the app.
