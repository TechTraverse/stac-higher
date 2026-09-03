# Cross-runtime config contract fixtures (ISSUE I-53)

The §5.1 ingest/delivery config shapes are validated twice: by Zod on the app's
write path (`app/src/lib/associations/schemas.ts`) and by the Python pipeline's
readers (`services/pipeline/src/pipeline/{ingest,delivery}/config.py`). These
golden fixtures are the single source of truth that keeps the two honest —
**both** test suites consume them:

- `app/src/__tests__/contract-fixtures.test.ts` (vitest)
- `services/pipeline/tests/test_contract_fixtures.py` (pytest)

## The rule

**A new or changed cross-runtime config shape ⇒ a new or updated fixture here.**
Change a schema field, default, or enum on either side and the other side's
suite fails until the fixture (and the other validator) is updated with it.

## File format

One JSON file per shape — the §5.1 configs (`ingest-config.json`,
`delivery-config.json`), the direction-specific expectations since M2-A
(`ingest-expectation.json`, `delivery-expectation.json`, validated by
`schemas.ts`'s expectation schemas and `pipeline/flow/expectation.py`), and
the webhook channel config since M2-C (`webhook-channel-config.json`,
validated by `app/src/lib/notifications/schemas.ts` and
`pipeline/notify/config.py`), and the s3 connection `config` since G-1
(`s3-connection-config.json`, validated by `connections/schemas.ts`'s
`s3ConfigSchema` and `pipeline/connections/adapters/s3.py`'s
`parse_s3_config`):

- `minimal` — the smallest valid document a client can submit.
- `defaults` — the defaults-applied document the app writes for `minimal`
  (asserted deep-equal against Zod's parse output; the pytest suite asserts
  each §5.1 default in the parsed dataclass against these values, so a default
  drifting on either side fails one of the suites).
- `cases[]` — `{ name, config, app, pipeline }` where `app`/`pipeline` is
  `"accept"` or `"reject"`.

## Additional fixture styles (Phase 7)

Not every cross-runtime contract is a config document; the Phase 7 spec (§10)
adds fixtures whose files carry a top-level `"style"` marker instead of the
`minimal`/`defaults`/`cases[]` config format:

### `grammar-cases` — `staged-asset-href.json`

A string grammar (`staging://{upload_id}/{filename}`), minted by the app and
parsed by the pipeline (dispatcher detect + finalize resolve). Each case is
`{ name, href, detected, parse }`:

- `detected` — the outcome of the shared detection rule (a case-sensitive
  `staging://` prefix check). An undetected href is simply *not staged* —
  ordinary external asset hrefs pass through untouched, never an error.
- `parse` — the expected `{ upload_id, filename }` from strict parsing, or
  `null` when BOTH sides must reject (traversal segments, directory
  separators, missing filename, unsafe characters). Consumers also assert
  the round-trip: re-minting from a successful parse reproduces the href.

### `status-contract` — `push-upload-status.json`

The `staged_uploads.status` enum + `result` jsonb — the write-gate/lenient-
reader asymmetry INVERTED: the **pipeline** is the strict writer (finalize
recorder + sweeps), the **app** is the lenient Zod reader on the poll route
(`GET /api/uploads/{uploadId}`). The file pins `statuses`, `terminal`, and the
closed `reasons` string set; `cases[]` is `{ name, doc, app, pipeline }` where
`doc` is `{status, result, error}` as served, `app: accept` means the reader
parses it and `pipeline: accept` means the writer may produce it. Cases with
`app: accept` / `pipeline: reject` encode the direction: the reader must
survive a newer writer (unknown result keys, unknown future reason strings),
while both sides reject broken shapes (unknown status, missing reason, wrong
container types).

### `pinned-enum` — `alert-kinds.json`

A bare closed enum with no document shape at all (P7-H — the first change to
the alert `kind` enum since the fixture was deferred in M2). The file pins:

- `kinds` — the full closed enum, in canonical order. Every consumer that
  branches on or displays a kind must recognize all of them (the vitest
  consumer asserts the monitoring UI's label map covers the whole enum, so a
  new kind cannot land unlabeled).
- one list per **writer** (`monitor_kinds`, `notify_kinds`) plus
  `declared_kinds`, the waiting room for kinds that exist in the enum but have
  no writer yet — the single-writer ownership partition. Each writer-side
  constant
  (`MONITOR_KINDS`, `WEBHOOK_FAILED_KIND`) is asserted equal to its list, and
  the lists must partition `kinds` exactly: adding a kind on either side
  without updating the fixture (or claiming a kind in two writers) fails a
  suite. Growing the enum means appending here and to a list in the same
  change — a writer's, or `declared_kinds` when the writer is not decided yet.
  Phase 9 (M5-0) declared `process_stalled`, `process_failed` and
  `process_rate_limited` that way. Ownership is deliberately not a formality:
  `monitor_kinds` membership grants the flow monitor auto-resolve authority
  over a kind, so claiming one before its evaluator exists would let the
  monitor silently close alerts another writer raised. M5-E moves each process
  kind to whichever writer actually raises it, and the partition test is what
  makes that a decision rather than an accident.

## Additional fixture styles (Phase 9)

### `discriminated-union` — `process-trigger.json`, `process-runtime.json`

The §5.6 `trigger` and `runtime` shapes are unions on a `kind` discriminator,
so a single `minimal`/`defaults` pair cannot describe them. These files replace
that pair with:

- `discriminator` — the field the union switches on (`kind`).
- `variants` — one `{ minimal, defaults }` pair **per arm**, keyed by the
  discriminator value. Both suites assert each arm's `minimal` parses to
  exactly its `defaults`.

`cases[]` keeps the ordinary `{ name, config, app, pipeline }` format across
both arms.

**`process-runtime.json` carries the M5 slice-1 asymmetry**, and it is a
decision rather than an oversight: the `container` arm is part of the contract
(the pipeline reader parses it, so nothing is foreclosed) while the app's
**write gate** refuses it — user-supplied images are a supply-chain review
surface deferred past the first accreditation scope (design spec §4, ADR 0013).
Every `container` case is therefore `app: "reject"` / `pipeline: "accept"`,
and the vitest consumer runs the `cases[]` through `processRuntimeWriteSchema`
while asserting `defaults` against the read schema `processRuntimeSchema`. A
`container` document that is *also* malformed (no image) stays `reject`/
`reject` — broken is not the same as gated.

### `process-env.json` and `process-expectation.json`

Ordinary `minimal`/`defaults`/`cases[]` documents. `env` is an ARRAY envelope
(`[{name, value} | {name, secret_ref}]`); a `secret_ref` is
`{connection_id, key}`, a pointer into the §5.2 encrypted-credentials envelope
resolved pipeline-side at launch and injected only into the run container's
environment. A `value` + `secret_ref` collision is `reject`/`reject` on both
sides rather than a precedence rule, because a plaintext secret sitting beside
a reference is a leak a precedence rule would quietly preserve.

## Additional fixture styles (GOES loop, G-2)

### `producer-golden` — `process-input-manifest.json`

The manifest the pipeline writes to
`staging/runs/{run_id}/inputs/{batch_id}/manifest.json` before a process run
starts (GOES spec §3.1, ADR 0018). It is *cross-runtime* in the sense that
matters — user code inside the run container reads it — but the app never
touches it, so **only the pytest suite consumes this file**:
`services/pipeline/tests/test_process_inputs.py` asserts that
`plan_inputs()` produces exactly `expected` from `given`. The document has:

- `given` — the planner's inputs: run/process/batch ids, the bucket, the
  asset href base, the process's source collections, the run's `input_items`
  refs, and the pgstac documents keyed `"{collection}/{item_id}"`.
- `expected` — `manifest_key`, the sorted `read_prefixes` (one canonical
  `assets/{collection}/` prefix per source collection), the `fetches` the
  launcher must perform, and the `manifest` itself.
- **`$ref` convention**: an `item` inside `expected.manifest.items[]` is
  written as `{ "$ref": "given.documents.<key>" }` and the test substitutes the
  named `given.documents` entry — the manifest carries the document verbatim,
  so repeating it would only invite drift.

`version` bumps are additive: a reader must ignore keys it does not know.

`process-extract-manifest.json` is the extractor twin (G-6): `kind: extract`,
`items[].item` is the ref's own `draft` (`$ref: given.refs.<n>.draft`) rather
than a pgstac document — an extractor's item is not catalogued yet — and
`read_prefixes` come from the refs' collections rather than `process_sources`.
Note its draft asset key is a filename **stem** (what `build_assets` produces)
while the staged filename comes from the href's tail, so the two differ by the
extension on purpose.
`given.source_hrefs` (optional, `{collection}/{item_id}` → `{filename: href}`)
is what the ledger says about reference-mode assets; a canonical href with a
matching entry is staged from that source and keeps its catalog href in the
manifest.

## Why `app` and `pipeline` expectations can differ

The contract is deliberately asymmetric. Zod is the **strict write gatekeeper**
(rejects unknown keys, enforces floors and cross-field rules) — nothing invalid
should ever reach the DB. The Python parsers are **lenient readers** that
re-apply defaults and coerce, so the pipeline stays robust against rows that
predate a default or were written by hand. Cases where the writer rejects but
the reader tolerates are annotated `"app": "reject", "pipeline": "accept"`.
Anything both sides must reject (missing/blank required fields, unknown enum
values, wrong container types) is `reject`/`reject` — those are the documents
that would otherwise become silently dead flows or a stalled dispatcher.
