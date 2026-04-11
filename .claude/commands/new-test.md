Write tests for: $ARGUMENTS

Follow these steps:

1. Read the source file to understand what needs testing.

2. Determine the test type:
   - **Unit test** (pure logic, no DOM): Create at `src/__tests__/<name>.test.ts`
   - **Component test** (React rendering): Create at `src/__tests__/<name>.test.tsx` — needs `@testing-library/react`
   - **E2E test** (browser workflow): Create at `e2e/<name>.spec.ts` — uses Playwright

3. Follow existing test patterns:
   - Unit tests: `import { describe, it, expect } from "vitest"` with path alias `@/`
   - Mock fetch with `vi.stubGlobal("fetch", mockFn)` when testing API calls
   - Mock nanostores with `vi.mock("@/stores/...", () => ({ ... }))`
   - E2E tests: `import { test, expect } from "@playwright/test"` with `page.goto()`, clear localStorage in beforeEach

4. Run the tests: `cd $CLAUDE_PROJECT_DIR/app && npm test` (unit) or `npm run test:e2e` (E2E).

5. Run `cd $CLAUDE_PROJECT_DIR/app && npx astro check` to verify no type errors in test files.
