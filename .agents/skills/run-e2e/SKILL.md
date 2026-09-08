---
name: run-e2e
description: Run the Playwright e2e suite for the STAC Higher app, or debug a failing e2e test. Use whenever the user asks to run e2e/browser/integration tests, or when a task changed UI flows that the suite covers. Covers backend preconditions, the agent-friendly reporter, and the suite's CSRF/IPv6/port/selector gotchas.
---

# Run E2E

## Preconditions

1. **Docker backend** for most of the suite: `extensions.spec.ts` and
   `proxy.spec.ts` need the catalog backend on :8082; the **app-DB specs**
   (`data-flow`, `monitoring`, `assets`, `collection-settings`) hit
   `stac_higher.*` tables, which the same stack's Postgres provides. Check
   `docker compose ps`; if not `Up`, run `docker compose up -d` from the repo
   root and wait a few seconds. (`catalogs`, `connections`, `extension-forms`
   are UI-surface-only and pass without the stack.) `map.spec.ts`'s 2nd and
   3rd tests also need the backend's built-in catalog to actually have
   products (they add one as a layer); it is also the first spec to mount a
   real MapLibre canvas, so the suite now fetches its basemap style from
   `basemaps.cartocdn.com` — an outbound network dependency.
2. **Dev server / port**: Playwright reuses an existing server on the target
   port, otherwise auto-starts one. **Something else may own :4321** (editors
   with built-in servers have caused this) — Playwright then silently tests
   the wrong server or times out. The config honors `E2E_PORT`
   (`app/playwright.config.ts` threads it into `baseURL`, the CSRF `Origin`
   header, and the webServer command): run
   `E2E_PORT=4399 npm run test:e2e:ci` when :4321 is taken. Never run e2e
   while another agent owns the dev server or the suite — the DB is shared
   and the suite is serial.
3. **Credentials key**: `data-flow.spec.ts` creates a connection via the API,
   which needs `CREDENTIALS_MASTER_KEY` in the dev server's env. It lives in
   the repo-root `.env` (docker-compose's env file), which Astro does NOT load
   — source it before running: `set -a && source ../.env && set +a` (from
   `app/`), or export the var before starting a dev server manually.
   **From a worktree, `../.env` does not exist**: `.env` is gitignored, so
   only the main checkout has one. Source it by absolute path
   (`set -a && source /path/to/stac-higher/.env && set +a`) or
   `data-flow.spec.ts` fails in `beforeAll` with an unhelpful
   `expect(conn.ok())` false that looks like a regression.
4. **Live-data specs**: `goes-loop.spec.ts` runs only with `E2E_LIVE_NODD=1`.
   It needs the internet, the FULL stack including the pipeline
   (`docker compose up -d --wait`), the process runtime image
   (`stac-higher-process-runtime:local`, see
   `services/pipeline/src/pipeline/demo/README.md`), migration 027 applied
   (load any app page), the `stac-higher-deliveries` bucket (`minio-init`
   creates it), and `CREDENTIALS_MASTER_KEY` in the dev server env (the
   delivery connection). Budget ~5 minutes; the bound is 10. Run it alone:
   `E2E_LIVE_NODD=1 npm run test:e2e:ci -- goes-loop`.

## Run

From `app/`:
- Full suite: `npm run test:e2e:ci` (list reporter — streams to stdout, no HTML
  report or browser window; always use this variant in non-interactive runs)
- Filtered: `npm run test:e2e:ci -- <filter>`

Current specs: `assets`, `catalogs`, `collection-settings`, `connections`,
`data-flow`, `extension-forms`, `extensions`, `goes-loop` (gated), `map`,
`monitoring`, `processes`, `proxy`.

Report pass/fail counts; on failure list only failing test names plus the first
error line each. Don't dump the report directory.

## Environment flags

| Flag | Purpose |
| --- | --- |
| `E2E_PORT` | Dev server port (default 4321) |
| `E2E_LIVE_NODD` | `1` enables the live GOES spec (`goes-loop.spec.ts`) |
| `E2E_STAC_URL` | STAC API base for a stack on other ports (default `http://localhost:8082`) |
| `E2E_TITILER_URL` | titiler-pgstac base for a stack on other ports (default `http://localhost:8084`) |
| `E2E_S3_ENDPOINT` | S3/MinIO endpoint for a stack on other ports (default `http://localhost:9000`) |

## Gotchas (each of these has burned an agent before)

- **Astro's dev toolbar swallows clicks.** It is a fixed bottom-centre overlay,
  so a control underneath it is unclickable and the failure reads like a broken
  selector (`<astro-dev-toolbar> intercepts pointer events`, dozens of click
  retries). `playwright.config.ts` sets `E2E=1` on its webServer and
  `astro.config.mjs` disables the toolbar on that signal — but only for a
  server Playwright STARTS. Running against an already-running dev server
  (`reuseExistingServer`) keeps the toolbar and can still hit this.
- **Astro 7 daemonizes `astro dev` under AI agents**: it auto-detects agent
  environments and backgrounds the server (parent exits → Playwright reports
  "webServer exited early", and an orphaned server holds the port —
  `npx astro dev stop` clears it). `playwright.config` sets
  `ASTRO_DEV_BACKGROUND` in the webServer env to force foreground — do not
  remove it.
- **Test-data contamination**: `monitoring.spec.ts`'s channel cleanup clicks
  the `.last()` Remove button in any row matching its URL — a leftover
  channel from manual/lead work (e.g. a rehearsal webhook) can make it delete
  the wrong row and fail. Clear stray `notification_channels` rows before a
  full run.
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
- A pipeline container built before the current migrations can spam errors
  into the shared DB's logs during e2e (e.g. an alerts `ON CONFLICT` miss
  after a dedup-index migration) — after merging migration-bearing work,
  `docker compose build pipeline && docker compose up -d pipeline` (and grep
  the build output for `ERROR`; BuildKit can exit 0 on a failed pull).
- **The live GOES spec depends on NOAA.** A NODD outage or an hour with no
  CONUS scans (rare, e.g. during ABI maintenance) fails it in
  `newestMcmipcKey`, which is a precondition failure, not a regression. It
  leaves nothing behind on success; on a mid-run failure, `afterAll` still
  removes its `e2e-goes-*` rows — check `/processes` and `/connections` if a
  run was killed.
