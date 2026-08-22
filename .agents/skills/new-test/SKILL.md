---
name: new-test
description: Write tests for a file or feature in the STAC Higher repo. Use whenever the user asks to test, cover, or add specs for something. Covers choosing unit vs component vs API-route vs e2e vs pytest, the mocking patterns (fetch, nanostores, locals.auth, domain query modules), contract fixtures, file placement, and how to run each suite.
---

# New Test

## 1. Read the source under test first

Understand the public surface before writing assertions.

## 2. Pick the test type

- **Unit** (pure logic, no DOM): `app/src/__tests__/<name>.test.ts`
- **API route / authz gate** (Astro server route): `app/src/__tests__/api-<name>.test.ts`
  or `authz-<name>-gate.test.ts` — the most common new test in this repo
- **Component** (React rendering): `app/src/__tests__/<name>.test.tsx` — uses `@testing-library/react`
- **E2E** (browser workflow): `app/e2e/<name>.spec.ts` — Playwright. Read the `run-e2e` skill for preconditions and selector gotchas before writing e2e specs.
- **Pipeline (Python)**: `services/pipeline/tests/test_<name>.py` — pytest;
  behavioral contracts live in the Fake repos (`FakeIngestRepo` etc.), Pg SQL
  is `# pragma: no cover` by convention. Run with `uv run pytest` (and
  `uv run ruff check .`) from `services/pipeline/`.
- **Proxy integration** (auth-enforced stack): `tests/integration/*.test.mjs` —
  node --test; lead/human only (needs Docker in enforced mode; skips cleanly
  otherwise).

## 3. Follow existing patterns

- Unit: `import { describe, it, expect } from "vitest"`, path alias `@/`
- Mock fetch with `vi.stubGlobal("fetch", mockFn)` for API-call tests
- Mock nanostores with `vi.mock("@/stores/...", () => ({ ... }))`
- **API routes**: import the route's exported handlers (`GET`, `POST`, …),
  call them with a hand-built context (`locals.auth` stubbed with the
  identity under test, `params`, a `Request`), and assert status + the
  `{ error, code }` JSON shape on 401/403. Mock the DB/lib layer the route
  calls (see `api-alerts.test.ts`, `authz-channels-gate.test.ts`). Gotcha:
  when a unit-tested route grows a DB lookup, stub it — an unmocked query
  500s with the Docker stack down (the M1-era `api-assets` lesson).
- **Components**: mock the domain query module, not fetch —
  `vi.mock("@/lib/monitoring/queries", …)` with `vi.hoisted()` for shared
  mock state; select via `getByTestId`/roles (see `monitoring-page.test.tsx`).
- **Contract fixtures**: a new or changed cross-runtime shape (association
  config, expectations, webhook config, …) requires a new/updated golden
  fixture in `tests/contract-fixtures/`, consumed by BOTH
  `app/src/__tests__/contract-fixtures.test.ts` and
  `services/pipeline/tests/test_contract_fixtures.py` — see that directory's
  README for the accept/reject format.
- E2E: `import { test, expect } from "@playwright/test"`; `page.goto()`; clear localStorage in `beforeEach`. The suite is **serial** (`workers: 1`, shared DB) — don't write tests that assume parallel isolation, and namespace created records so delete-by-prefix helpers work.

## 4. Run

- Unit/component/API-route: `cd app && npm test`
- Pipeline: `cd services/pipeline && uv run pytest` (add `DATABASE_URL=…` for the DB-gated integration tests)
- E2E: `cd app && npm run test:e2e:ci` (list reporter — agent-friendly)
- `npm run verify` from the repo root must pass before declaring done (add pytest+ruff when the pipeline was touched).
