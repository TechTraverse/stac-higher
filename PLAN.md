# PLAN.md

Backlog of non-blocking follow-ups surfaced during review but deliberately
left out of the current hardening pass. Each entry notes why it was deferred
and what would trigger picking it up.

## Follow-ups

### Decompose `ExtensionForm.tsx`
- **Status:** done (2026-04-17)
- **Outcome:** split into `ExtensionMetaFields` (Basic Information card) and
  `SchemaEditor` (properties `useFieldArray`); container now ~140 lines and
  wires children via `FormProvider` / `useFormContext`. External API
  unchanged: `ExtensionFormPage` still the sole export.
- **Files:** `app/src/components/extensions/ExtensionForm.tsx`,
  `app/src/components/extensions/ExtensionMetaFields.tsx`,
  `app/src/components/extensions/SchemaEditor.tsx`

### Replace in-memory schema cache with a shared store
- **Status:** done (2026-04-17)
- **Outcome:** promoted to `stac_higher.schema_cache` (url PK, schema JSONB,
  fetched_at, expires_at + `expires_at` index) via migration
  `002_create_schema_cache_table`. `schema-cache.ts` now issues SQL for
  get/set (upsert on conflict); TTL still 5 minutes; `getOrFetchSchema`'s
  public signature unchanged, so `resolve-schema.ts` needed no changes.
  `import.ts` left untouched — it's already deduped by `source_url` at the
  extensions table, so cache doesn't help that path.
- **Files:** `app/src/lib/extensions/schema-cache.ts`,
  `app/src/lib/db/migrate.ts`

### Auth on `/api/proxy`
- **Status:** done — partial (2026-04-17)
- **Outcome:** two-layer gate added to `/api/proxy`:
  1. **Always-on same-origin check** — `Sec-Fetch-Site: cross-site` is
     rejected with 403. Modern browsers always set this header, so the
     proxy can no longer be invoked from other web origins. Server-to-
     server callers that omit the header still pass.
  2. **Optional shared token** — when `PROXY_AUTH_TOKEN` env is set, every
     request must include a matching `X-Proxy-Auth` header (constant-time
     compared via `timingSafeEqual`). Unset = current dev behavior; set =
     full lockdown for deployments behind a reverse proxy or middleware
     that injects the header.
- **Why partial:** a real per-user session system is still out of scope
  (no login UI, no session store). The deployer is on the hook for token
  injection if they enable layer 2.
- **Follow-up trigger:** first multi-user setup → add real sessions and
  swap layer 2 for per-user auth.
- **Files:** `app/src/pages/api/proxy.ts`,
  `app/src/__tests__/proxy.test.ts` (5 new cases), `CLAUDE.md` (env doc).

### Fix failing e2e specs
- **Status:** done (2026-04-17)
- **Outcome:** all 20 e2e specs now pass in ~9s. Root causes found and
  fixed:
  1. **Astro CSRF blocks DELETE/PUT/POST without Origin** — Playwright's
     `APIRequestContext` didn't send an `Origin` header, so every delete
     in `beforeEach`/`afterEach` 403'd and test data accumulated. Fixed
     by setting `use.extraHTTPHeaders.Origin = baseURL` in
     `playwright.config.ts`.
  2. **Astro dev server bound to IPv6-only** — Playwright's probe/tests
     hit `127.0.0.1:4321` but the default `astro dev` only listens on
     `::1`. Fixed with `--host 127.0.0.1` in the `webServer.command` and
     matching `baseURL` / `url`.
  3. **Parallel workers fought over shared DB state** — extensions
     helpers delete-by-name-or-prefix globally, so parallel tests
     deleted each other's data. Fixed with `fullyParallel: false` +
     `workers: 1` (e2e hits a shared Postgres; parallelism isn't worth
     the complexity here).
  4. **Selector drift** — `CardTitle` renders as `<div>`, not a heading,
     so `getByRole("heading", { name: "Extensions" })` never matched.
     The extension picker is now a `role=combobox` (not a button), and
     `/collections/new` has two comboboxes (license + picker), so the
     picker needs `.filter({ hasText: /select extensions|selected/i })`.
     Detail-page and list-page locators switched to `getByRole("heading")`
     and `page.locator(\`a[href="/extensions/\${ext.id}"]\`)` to avoid
     strict-mode ambiguity from breadcrumbs/cards.
  5. **`test-collection` didn't exist** — item-form tests navigated to
     `/collections/test-collection/items/new`; added a `beforeAll` that
     `POST`s the fixture collection to the STAC API if missing.
- **Files:** `app/playwright.config.ts`, `app/e2e/proxy.spec.ts`,
  `app/e2e/extensions.spec.ts`, `app/e2e/extension-forms.spec.ts`.

### Request-level observability for outbound fetches
- **Status:** done (2026-04-17)
- **Outcome:** `safeFetch` now emits one structured JSON log line per
  request, covering both success and error paths. Fields: `event`
  (`safe_fetch`), `method`, `host`, `status`, `bytes`, `elapsed_ms`,
  `outcome` (`ok`/`error`), and `code` on errors (matches
  `SafeFetchError.code` or `"unknown"`). Info-level on success,
  warn-level on error, so production log shippers can filter on stream
  and/or `outcome`. Opt-out with `SAFE_FETCH_LOG=0` (used by Playwright
  webserver to keep CI output tidy). Logged host is already normalised
  (lowercased, brackets stripped) for consistent aggregation.
- **Files:** `app/src/lib/http/safe-fetch.ts`,
  `app/src/__tests__/safe-fetch.test.ts` (3 new observability cases —
  total now 34).
