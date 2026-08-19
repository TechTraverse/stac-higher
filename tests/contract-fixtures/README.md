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
`delivery-config.json`) and, since M2-A, the direction-specific expectations
(`ingest-expectation.json`, `delivery-expectation.json`, validated by
`schemas.ts`'s expectation schemas and `pipeline/flow/expectation.py`):

- `minimal` — the smallest valid document a client can submit.
- `defaults` — the defaults-applied document the app writes for `minimal`
  (asserted deep-equal against Zod's parse output; the pytest suite asserts
  each §5.1 default in the parsed dataclass against these values, so a default
  drifting on either side fails one of the suites).
- `cases[]` — `{ name, config, app, pipeline }` where `app`/`pipeline` is
  `"accept"` or `"reject"`.

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
