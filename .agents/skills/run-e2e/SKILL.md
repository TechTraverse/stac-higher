---
name: run-e2e
description: Run the Playwright e2e suite for the STAC Higher app, or debug a failing e2e test. Use whenever the user asks to run e2e/browser/integration tests, or when a task changed UI flows that the suite covers. Covers backend preconditions, the agent-friendly reporter, and the suite's CSRF/IPv6/selector gotchas.
---

# Run E2E

## Preconditions

1. **Docker backend**: `extensions.spec.ts` and `proxy.spec.ts` need pgstac +
   stac-fastapi on :8082. Check `docker compose ps`; if not `Up`, run
   `docker compose up -d` from the repo root and wait a few seconds.
2. **Dev server**: Playwright reuses an existing server on :4321, otherwise
   auto-starts one. Never run e2e while another agent owns the dev server or
   the suite — the DB is shared and the suite is serial.

## Run

From `app/`:
- Full suite: `npm run test:e2e:ci` (list reporter — streams to stdout, no HTML
  report or browser window; always use this variant in non-interactive runs)
- Filtered: `npm run test:e2e:ci -- <filter>`

Report pass/fail counts; on failure list only failing test names plus the first
error line each. Don't dump the report directory.

## Gotchas (each of these has burned an agent before)

- **Astro CSRF**: POST/PUT/DELETE require an `Origin` header matching the dev
  server. `playwright.config` sets `use.extraHTTPHeaders.Origin` — do not remove
  it or API calls 403.
- **IPv6**: `astro dev` binds `::1` only; the webServer command must pass
  `--host 127.0.0.1` so Playwright's `baseURL` on 127.0.0.1 can connect.
- **Shared DB, serial suite**: tests run with `fullyParallel: false`,
  `workers: 1`. Delete-by-prefix helpers race across workers — keep it serial.
- **shadcn `CardTitle` is a `<div>`**, not a heading — `getByRole("heading")`
  won't match it. Use `getByText(..., { exact: true })` or a more specific role.
- **`/collections/new` has two `role=combobox`** (license + extension picker).
  Disambiguate with `.filter({ hasText: /select extensions|extensions? selected/i })`.
- Playwright sets `SAFE_FETCH_LOG=0` to keep `safeFetch` JSON logs out of CI
  output — preserve that when touching the config.
