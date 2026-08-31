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
`pipeline/notify/config.py`):

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
- one list per **writer** (`monitor_kinds`, `notify_kinds`) — the
  single-writer ownership partition. Each writer-side constant
  (`MONITOR_KINDS`, `WEBHOOK_FAILED_KIND`) is asserted equal to its list, and
  the lists must partition `kinds` exactly: adding a kind on either side
  without updating the fixture (or claiming a kind in two writers) fails a
  suite. Growing the enum (Phase 9 appends its kinds) means appending here
  and to the owning writer's list in the same change.

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
