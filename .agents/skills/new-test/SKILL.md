---
name: new-test
description: Write tests for a file or feature in the STAC Higher repo. Use whenever the user asks to test, cover, or add specs for something. Covers choosing unit vs component vs e2e, the mocking patterns (fetch, nanostores), file placement, and how to run each suite.
---

# New Test

## 1. Read the source under test first

Understand the public surface before writing assertions.

## 2. Pick the test type

- **Unit** (pure logic, no DOM): `app/src/__tests__/<name>.test.ts`
- **Component** (React rendering): `app/src/__tests__/<name>.test.tsx` — uses `@testing-library/react`
- **E2E** (browser workflow): `app/e2e/<name>.spec.ts` — Playwright. Read the `run-e2e` skill for preconditions and selector gotchas before writing e2e specs.

## 3. Follow existing patterns

- Unit: `import { describe, it, expect } from "vitest"`, path alias `@/`
- Mock fetch with `vi.stubGlobal("fetch", mockFn)` for API-call tests
- Mock nanostores with `vi.mock("@/stores/...", () => ({ ... }))`
- E2E: `import { test, expect } from "@playwright/test"`; `page.goto()`; clear localStorage in `beforeEach`. The suite is **serial** (`workers: 1`, shared DB) — don't write tests that assume parallel isolation, and namespace created records so delete-by-prefix helpers work.

## 4. Run

- Unit/component: `cd app && npm test`
- E2E: `cd app && npm run test:e2e:ci` (list reporter — agent-friendly)
- `npm run verify` from the repo root must pass before declaring done.
