# R-1: App runtime config, S3 credential chain, RDS TLS, credential budget, audience claim, migrate CLI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Issue:** #137 (queue R, epic #136).

**Goal:** Make the published `app` image deployable unchanged into any environment. Every browser-visible setting is delivered at runtime by the server. S3 falls back to the AWS default credential chain. node-postgres verifies RDS TLS against a CA bundle. A process deploy over the STS credential cap is refused. The bearer audience claim is configurable. Migrations run as a command.

**Architecture:** On every SSR render, `Layout.astro` serializes one `RuntimeConfig` object, built from plain env vars, into an inline `<script>` that sets `window.__STAC_HIGHER_CONFIG__`. Islands read it through one accessor, `getRuntimeConfig()`, and never through `import.meta.env.PUBLIC_*`. The other five changes are small server-side modules, each behind an injectable-`env` function (the repo's existing pattern). The credential budget gets a contract fixture shared with R-2 (the pipeline half).

**Tech Stack:** Astro 7 SSR (`@astrojs/node` standalone), React 19 islands, nanostores, `@aws-sdk/client-s3` v3, `pg` 8.20, `jose` 6, vitest 4, esbuild 0.28.2 (already in the lockfile through Vite, declared explicitly here).

**Spec:** `docs/superpowers/specs/2026-11-02-deployment-artifacts-design.md` §8 (code changes), §7.2 (TLS to RDS), §6.1 (app migrate Job), §6.2 (`publicUrls` values contract). ADR `docs/decisions/0024-deployment-artifacts.md`.

## Global Constraints

- **Gate:** `npm run verify` from the repo root is the only gate. No e2e, no dev server, no Docker, no push. Every task ends with the targeted `cd app && npx vitest run <file>` (or verify) and a commit. The PostToolUse hook runs `astro check` after each `.ts`/`.tsx`/`.astro` edit. Fix what it reports before moving on.
- **Runtime config delivery:** the server injects one JSON object into the HTML shell (`Layout.astro` → `window.__STAC_HIGHER_CONFIG__`). It is read by ONE accessor (`getRuntimeConfig()` in `app/src/lib/runtime-config/index.ts`). Islands never read `import.meta.env.PUBLIC_*`. The server reads plain env vars. New names come first. The old `PUBLIC_*` names keep working as fallbacks, so local dev and `.env.example` keep working. The cube-server URL is included as `CUBE_SERVER_URL`.
- **Runtime env names (this plan's ruling; first non-empty wins, values trimmed, trailing `/` stripped):**

  | Config key | Env, in order | Default |
  |---|---|---|
  | `builtInCatalogUrl` | `BUILTIN_CATALOG_PUBLIC_URL`, `PUBLIC_BUILTIN_CATALOG_URL` | `http://localhost:8081` |
  | `titilerUrl` | `TITILER_URL`, `PUBLIC_TITILER_URL` | `http://localhost:8084` |
  | `tipgUrl` | `TIPG_URL`, `PUBLIC_TIPG_URL` | `http://localhost:8085` |
  | `cubeServerUrl` | `CUBE_SERVER_URL` | `http://localhost:8086` |
  | `processNetworkMax` | `PROCESS_NETWORK_MAX`, `PUBLIC_PROCESS_NETWORK_MAX` | `isolated` |

  `BUILTIN_CATALOG_URL` is NOT reused for the browser URL. It already means the server-side BFF forward target (`app/src/lib/catalog/transactions.ts`), which in a cluster is an internal service address. URL values must be absolute `http:`/`https:` URLs (the spec's §6.1 ingress is one public host per service). Anything else throws an error that names the env var.
- **Audience claim (cross-slice ruling with R-3, the proxy half, verbatim semantics):** setting `AUTH_BEARER_AUDIENCE_CLAIM`.
  - Unset or blank means `aud`. The only other accepted value is `client_id`. The value is **trimmed and case-sensitive**.
  - Any other value fails at config load with a clear message. Astro SSR has no startup hook, so the nearest equivalent is `getAuthConfig()`, which the middleware runs on every request: it throws, every request 500s, and the message is logged. A typo is never silent.
  - In `client_id` mode, `AUTH_BEARER_AUDIENCES` lists the allowed client ids, matched against the token's `client_id` claim. A token that carries an `aud` claim is **rejected**. This mirrors the proxy, where PyJWT rejects an `aud` claim when no audience is configured.
  - In `aud` mode (the default), behaviour is unchanged.
- **S3:** the app reads `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` (verified in `app/src/lib/storage/config.ts`). When both are unset or empty, `credentials` is omitted, so the AWS SDK default chain runs (Pod Identity / IRSA). The compose-MinIO keys (`minioadmin`) are applied only when none of `S3_ENDPOINT`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` is set at all, which is the bare local-dev case. If exactly one key is non-empty, that is a configuration error.
- **RDS TLS:** setting `PGSSLROOTCERT` (path to a CA bundle) → node-postgres `ssl: { ca, rejectUnauthorized: true }`. Unset or empty = today's behaviour. `DATABASE_URL` stays the connection string.
- **Credential budget (cross-slice contract with R-2, verbatim):**
  - `PROCESS_MAX_CREDENTIAL_SECONDS`: integer seconds. Unset or empty = no limit (today's behaviour).
  - Grace: the app reads `PROCESS_CREDENTIAL_GRACE_SECONDS` (default `300`), the same env var and default the pipeline reads (`services/pipeline/src/pipeline/config.py:187,557`).
  - Deploy-side budget = `max_queue_wait_seconds + timeout_seconds + grace`, using the hardware profile the revision names. `max_queue_wait_seconds` **already exists** in the profile contract (K-1: `hardwareProfileSchema`, `tests/contract-fixtures/hardware-profiles.json`). It is required there, so the "treat as 0" fallback is never needed.
  - Refusal: HTTP **422**, body `{ error, code: "credential_budget_exceeded" }`. The error message names the budget and the cap in seconds.
  - Fixture `tests/contract-fixtures/process-credential-budget.json` with the exact content given in Task 6. R-2 creates the identical file.
- **Migrate CLI:** `node app/dist/server/migrate.mjs` (built by `npm run build`, shipped in the image) runs `runMigrations()` and exits 0 on success, 1 on failure. The first-request migration path stays.
- **Never commit** account ids, ARNs, hostnames, CIDRs or secrets. Examples use placeholders (`https://catalog.example.org`, `/etc/ssl/rds/global-bundle.pem`).
- **Docs fold into the owning task:** `docs/backend.md` app environment table, `docs/auth.md` (audience claim), `docs/FEATURES.md` (slice entry, last task). No ADR (ADR 0024 covers this).
- **No migration** in this slice. No new runtime dependency. `esbuild` is declared as an app devDependency at the version already resolved in `package-lock.json` (`0.28.2`, pulled in by Vite), so no new package enters the tree.
- `packages/shared/src` reads no env at all (audit below), so everything here is app-local (`app/src/lib/...`).

### Audit: every `PUBLIC_*` and `import.meta.env` read (part of the deliverable)

| File:line | Read | Side | Disposition |
|---|---|---|---|
| `app/src/stores/catalogStore.ts:20-26` | `import.meta.env.PUBLIC_BUILTIN_CATALOG_URL` | browser (every island) | → `getRuntimeConfig().builtInCatalogUrl` (Task 2) |
| `app/src/lib/serving/urls.ts:19-24` | `import.meta.env` → `PUBLIC_TITILER_URL` | browser (SettingsTab, ProductOverview, item/collection previews) | → `getRuntimeConfig().titilerUrl` (Task 2) |
| `app/src/lib/serving/urls.ts:106-111` | `import.meta.env` → `PUBLIC_TIPG_URL` | browser | → `getRuntimeConfig().tipgUrl` (Task 2) |
| `app/src/components/processes/ProcessDetailPage.tsx:245` | `import.meta.env.PUBLIC_PROCESS_NETWORK_MAX` | browser | → `getRuntimeConfig().processNetworkMax`. The server derives it from the **enforced** `PROCESS_NETWORK_MAX`, so the UI mirror can no longer drift from the cap (Task 2) |
| `app/src/lib/catalog/transactions.ts:81` | `process.env.PUBLIC_BUILTIN_CATALOG_URL` (BFF fallback) | server, runtime | fallback becomes the runtime config's `builtInCatalogUrl`, so the new name also reaches the BFF (Task 2) |
| `app/src/lib/auth/config.ts:58` | `import.meta.env.PROD` | server | **stays**: a build-mode flag (prod build vs dev/vitest), not a deployment setting |
| `app/src/components/collections/CollectionList.tsx:27` | `PUBLIC_BUILTIN_CATALOG_URL` in copy | browser text | copy names `BUILTIN_CATALOG_PUBLIC_URL` (Task 2) |
| `app/src/lib/storage/config.ts:6`, `app/src/lib/processes/network.ts:8` | comments only | — | comments updated in Tasks 2/3 |
| `app/src/__tests__/catalog-store.test.ts:89`, `serving-urls.test.ts:18,41` | test env stubs | tests | rewritten in Task 2 |
| `packages/shared/src/**` | none | — | nothing to do |

Task 2 adds a source-scan test that keeps this table true. It fails if any non-test file under `app/src` or `packages/shared/src` reads `import.meta.env.PUBLIC_`, or reads `import.meta.env` anywhere except `app/src/lib/auth/config.ts`.

## Review Focus

1. **A config value containing `</script>`, `<!--` or U+2028** must not break out of the injected script or corrupt the page. Expected: it round-trips as data. Test in Task 1 (`serializeRuntimeConfig` escapes `<`, U+2028, U+2029).
2. **Partial or absent S3 keys.** With exactly one key set, expected a loud config error, not a half-static client. With nothing set on a dev laptop, expected the MinIO defaults: the SDK chain must never pick up the developer's personal AWS key and sign MinIO URLs with it (the access key id appears in every presigned URL). Tests in Task 3.
3. **`PGSSLROOTCERT` pointing at a missing or empty file** must fail with the path in the message. Expected: never a silent fallback to unverified or plaintext connections. Tests in Task 4.
4. **Every deploy path is budget-checked.** Besides the hand-written deploy, `POST /api/processes/builtin` (first pick, revision 1) and `{ from_builtin: true }` ("Update to current") create revisions too. All three must 422. A malformed cap (`"1h"`, `"0"`, `"-5"`, `"600"`, below the STS minimum of 900) must error, not silently mean "no limit". Tests in Task 6.
5. **A misspelt or mis-cased `AUTH_BEARER_AUDIENCE_CLAIM`** (e.g. `azp`, `Client_ID`) must fail at config load with a message naming the setting. It must never be read as `aud`, and no audience check may be skipped. Test in Task 7.

---

## File structure

| File | Responsibility |
|---|---|
| Create `app/src/lib/runtime-config/index.ts` | `RuntimeConfig` type, defaults, global name, browser accessor `getRuntimeConfig()`. Imported by islands. No `process.env`, no server imports. |
| Create `app/src/lib/runtime-config/server.ts` | `buildRuntimeConfig(env)`, `runtimeConfigValue(key, env)`, `serializeRuntimeConfig(config)`. Server-only. |
| Modify `app/src/layouts/Layout.astro` | inject the serialized config as the first `<head>` script |
| Modify `app/src/stores/catalogStore.ts`, `app/src/lib/serving/urls.ts`, `app/src/components/processes/ProcessDetailPage.tsx`, `app/src/lib/catalog/transactions.ts`, `app/src/components/collections/CollectionList.tsx` | consume the runtime config |
| Modify `app/src/lib/storage/config.ts`, `app/src/lib/storage/client.ts` | credentials-or-default-chain |
| Modify `app/src/lib/db/connection.ts` | `poolConfigFromEnv()` with TLS |
| Create `app/src/lib/db/migrate-cli.ts`, `app/src/cli/migrate.ts` | testable CLI core + the bundled entry |
| Modify `app/package.json`, `app/Dockerfile`, `scripts/apply-app-migrations.ts` | build the CLI bundle, guard it in the image, reuse the core in CI's script |
| Create `app/src/lib/processes/credential-budget.ts` | budget settings, check, 422 response |
| Modify `app/src/pages/api/processes/[id]/revisions.ts`, `app/src/pages/api/processes/builtin.ts` | apply the budget at all three deploy paths |
| Create `tests/contract-fixtures/process-credential-budget.json`; modify `tests/contract-fixtures/README.md` | cross-runtime fixture |
| Modify `app/src/lib/auth/bearer.ts` | `AUTH_BEARER_AUDIENCE_CLAIM` |
| Docs | `docs/backend.md`, `docs/auth.md`, `docs/serving.md`, `docs/processes.md`, `docs/ISSUES.md`, `docs/FEATURES.md`, `.env.example`, `app/README.md` |

---

### Task 1: Runtime config core + HTML injection

**Files:**
- Create: `app/src/lib/runtime-config/index.ts`
- Create: `app/src/lib/runtime-config/server.ts`
- Modify: `app/src/layouts/Layout.astro` (frontmatter + first child of `<head>` after the two `<meta>` tags)
- Test: `app/src/__tests__/runtime-config.test.ts`

**Interfaces:**
- Consumes: `getNetworkMax`, `DEFAULT_NETWORK_MAX` from `@/lib/processes/network` (server.ts only).
- Produces (index.ts):
  ```ts
  export interface RuntimeConfig {
    builtInCatalogUrl: string; titilerUrl: string; tipgUrl: string;
    cubeServerUrl: string; processNetworkMax: string;
  }
  export const DEFAULT_RUNTIME_CONFIG: Readonly<RuntimeConfig>; // values in Global Constraints
  export const RUNTIME_CONFIG_GLOBAL = "__STAC_HIGHER_CONFIG__";
  export function getRuntimeConfig(): RuntimeConfig;
  declare global { var __STAC_HIGHER_CONFIG__: Partial<RuntimeConfig> | undefined; }
  ```
- Produces (server.ts):
  ```ts
  export const RUNTIME_CONFIG_ENV: { readonly [K in keyof RuntimeConfig]: readonly string[] }; // table in Global Constraints
  export function runtimeConfigValue<K extends keyof RuntimeConfig>(key: K, env?: Record<string, string | undefined>): RuntimeConfig[K];
  export function buildRuntimeConfig(env?: Record<string, string | undefined>): RuntimeConfig;
  export function serializeRuntimeConfig(config: RuntimeConfig): string;
  ```

- [ ] **Step 1: Write the failing tests** (`// @vitest-environment node` at the top. `afterEach` deletes `globalThis.__STAC_HIGHER_CONFIG__`.)

```ts
describe("buildRuntimeConfig", () => {
  it("defaults to the compose ports", () => {
    expect(buildRuntimeConfig({})).toEqual({
      builtInCatalogUrl: "http://localhost:8081", titilerUrl: "http://localhost:8084",
      tipgUrl: "http://localhost:8085", cubeServerUrl: "http://localhost:8086",
      processNetworkMax: "isolated",
    });
  });
  it("reads the new names and strips trailing slashes", () => {
    const c = buildRuntimeConfig({
      BUILTIN_CATALOG_PUBLIC_URL: "https://catalog.example.org/", TITILER_URL: "https://tiles.example.org//",
      TIPG_URL: "https://features.example.org", CUBE_SERVER_URL: "https://cubes.example.org/",
    });
    expect(c.builtInCatalogUrl).toBe("https://catalog.example.org");
    expect(c.titilerUrl).toBe("https://tiles.example.org");
    expect(c.cubeServerUrl).toBe("https://cubes.example.org");
  });
  it("falls back to the PUBLIC_* names, and the new name wins when both are set", () => {
    expect(buildRuntimeConfig({ PUBLIC_TITILER_URL: "https://old.example.org" }).titilerUrl).toBe("https://old.example.org");
    expect(buildRuntimeConfig({ TITILER_URL: "https://new.example.org", PUBLIC_TITILER_URL: "https://old.example.org" }).titilerUrl).toBe("https://new.example.org");
  });
  it("treats an empty or blank value as unset", () => {
    expect(buildRuntimeConfig({ TIPG_URL: "  ", PUBLIC_TIPG_URL: "https://old.example.org" }).tipgUrl).toBe("https://old.example.org");
  });
  it("refuses a URL that is not absolute http(s), naming the variable", () => {
    expect(() => buildRuntimeConfig({ TITILER_URL: "tiles.example.org" })).toThrow(/TITILER_URL/);
    expect(() => buildRuntimeConfig({ CUBE_SERVER_URL: "javascript:alert(1)" })).toThrow(/CUBE_SERVER_URL/);
  });
  it("mirrors the enforced PROCESS_NETWORK_MAX, then PUBLIC_PROCESS_NETWORK_MAX", () => {
    expect(buildRuntimeConfig({ PROCESS_NETWORK_MAX: "open", PUBLIC_PROCESS_NETWORK_MAX: "inputs" }).processNetworkMax).toBe("open");
    expect(buildRuntimeConfig({ PUBLIC_PROCESS_NETWORK_MAX: "inputs" }).processNetworkMax).toBe("inputs");
  });
  it("degrades an invalid network cap to the default instead of failing the page", () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(buildRuntimeConfig({ PROCESS_NETWORK_MAX: "everything" }).processNetworkMax).toBe("isolated");
  });
});

describe("serializeRuntimeConfig", () => {
  it("is a script that sets the global, and round-trips hostile values (Review Focus 1)", () => {
    const hostile = { ...DEFAULT_RUNTIME_CONFIG, titilerUrl: "https://x.example.org/</script><script>alert(1)</script><!--  " };
    const script = serializeRuntimeConfig(hostile);
    expect(script.startsWith("window.__STAC_HIGHER_CONFIG__=")).toBe(true);
    expect(script).not.toMatch(/<| | /);
    const sandbox: { window: Record<string, unknown> } = { window: {} };
    new Function("window", script)(sandbox.window);
    expect(sandbox.window.__STAC_HIGHER_CONFIG__).toEqual(hostile);
  });
});

describe("getRuntimeConfig", () => {
  it("returns the defaults when nothing was injected", () => {
    expect(getRuntimeConfig()).toEqual(DEFAULT_RUNTIME_CONFIG);
  });
  it("reads the injected object, per key, ignoring non-string or empty values", () => {
    globalThis.__STAC_HIGHER_CONFIG__ = { titilerUrl: "https://tiles.example.org", tipgUrl: "" , cubeServerUrl: 42 as never };
    const c = getRuntimeConfig();
    expect(c.titilerUrl).toBe("https://tiles.example.org");
    expect(c.tipgUrl).toBe("http://localhost:8085");
    expect(c.cubeServerUrl).toBe("http://localhost:8086");
  });
  it("pins the network default to the server's DEFAULT_NETWORK_MAX", () => {
    expect(DEFAULT_RUNTIME_CONFIG.processNetworkMax).toBe(DEFAULT_NETWORK_MAX);
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd app && npx vitest run src/__tests__/runtime-config.test.ts`
Expected: FAIL (module `@/lib/runtime-config` not found).

- [ ] **Step 3: Implement `index.ts`.** `DEFAULT_RUNTIME_CONFIG.processNetworkMax` is the literal `"isolated"`, not an import from `network.ts`: that module pulls `processes/schemas.ts` (zod) into every island's chunk. The test above pins the two values equal. `getRuntimeConfig()` reads `globalThis[RUNTIME_CONFIG_GLOBAL]` on each call, without memoizing (tests swap it), and takes each key from it only when it is a non-empty string. Header comment: islands read only this. Server code calls `buildRuntimeConfig`, because on the server this accessor sees no global and returns the defaults.

- [ ] **Step 4: Implement `server.ts`.** `runtimeConfigValue` takes the first non-empty trimmed value from `RUNTIME_CONFIG_ENV[key]`, else the default. For URL keys it validates with `new URL()` and protocol `http:`/`https:`, throwing `` `${name} must be an absolute http(s) URL; got ${JSON.stringify(raw)}` `` with the name of the variable that supplied the value, then strips trailing `/`. For `processNetworkMax` it calls `getNetworkMax({ PROCESS_NETWORK_MAX: raw })`. If that throws, it does `console.warn` and returns `DEFAULT_NETWORK_MAX`, the same posture as `ProcessDetailPage`'s `readUiNetworkMax` today, because the deploy route still enforces the real cap. `serializeRuntimeConfig` returns `` `window.${RUNTIME_CONFIG_GLOBAL}=${json};` `` where `json` is `JSON.stringify(config)` with `<` → `<`, U+2028 → ` `, U+2029 → ` `, written as escape sequences.

- [ ] **Step 5: Inject in `Layout.astro`.** Frontmatter: `import { buildRuntimeConfig, serializeRuntimeConfig } from "@/lib/runtime-config/server";` and `const runtimeConfigScript = serializeRuntimeConfig(buildRuntimeConfig());`. In `<head>`, before the theme script: `<script is:inline set:html={runtimeConfigScript}></script>`, with an Astro comment above it. The comment says: runtime config (R-1, spec §8), built per request from the server env, so one image serves any deployment. It runs before the deferred module scripts, so `catalogStore`'s module-init seeding sees it. A bad URL value fails the render loudly (500), naming the variable.

- [ ] **Step 6: Run to verify they pass**

Run: `cd app && npx vitest run src/__tests__/runtime-config.test.ts`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/src/lib/runtime-config app/src/layouts/Layout.astro app/src/__tests__/runtime-config.test.ts
git commit -m "R-1: runtime config object injected into the HTML shell"
```

---

### Task 2: Every browser-visible read moves to the runtime config + audit guard

**Files:**
- Modify: `app/src/stores/catalogStore.ts:17-27` (`builtInCatalogUrl()`)
- Modify: `app/src/lib/serving/urls.ts:15-25,102-112` (`titilerBaseUrl`, `tipgBaseUrl`)
- Modify: `app/src/components/processes/ProcessDetailPage.tsx:223-252` (`readUiNetworkMax` + its doc comment)
- Modify: `app/src/lib/processes/network.ts:1-10` (doc comment only)
- Modify: `app/src/lib/catalog/transactions.ts:70-85` (`builtinCatalogUrl`)
- Modify: `app/src/components/collections/CollectionList.tsx:27` (copy)
- Modify: `app/src/__tests__/catalog-store.test.ts:86-100`, `app/src/__tests__/serving-urls.test.ts:16-45`
- Create: `app/src/__tests__/runtime-config-audit.test.ts`
- Docs: `docs/backend.md` (stack table line 14; app env table row `PUBLIC_BUILTIN_CATALOG_URL`), `docs/serving.md:98-99` (+ the `{PUBLIC_TITILER_URL}`/`{PUBLIC_TIPG_URL}` placeholders → `{TITILER_URL}`/`{TIPG_URL}`), `docs/auth.md:142-143`, `app/README.md:109`, `.env.example` (OGC serving block)

**Interfaces:**
- Consumes: `getRuntimeConfig`, `RuntimeConfig` (Task 1, index.ts); `runtimeConfigValue` (Task 1, server.ts).
- Produces:
  ```ts
  export function titilerBaseUrl(config: Pick<RuntimeConfig, "titilerUrl"> = getRuntimeConfig()): string;
  export function tipgBaseUrl(config: Pick<RuntimeConfig, "tipgUrl"> = getRuntimeConfig()): string;
  // transactions.ts, unchanged signature:
  export function builtinCatalogUrl(): string; // BUILTIN_CATALOG_URL, else runtimeConfigValue("builtInCatalogUrl")
  ```

- [ ] **Step 1: Write the failing tests**

`serving-urls.test.ts`: replace the two env-argument tests.
```ts
afterEach(() => { delete globalThis.__STAC_HIGHER_CONFIG__; vi.unstubAllEnvs(); });
it("defaults the bases and strips a trailing slash", () => {
  expect(titilerBaseUrl()).toBe("http://localhost:8084");
  expect(titilerBaseUrl({ titilerUrl: "https://tiles.example.org/" })).toBe("https://tiles.example.org");
  expect(tipgBaseUrl({ tipgUrl: "https://features.example.org//" })).toBe("https://features.example.org");
});
it("reads the server-injected config, not import.meta.env (no rebuild for a URL change)", () => {
  vi.stubEnv("PUBLIC_TITILER_URL", "https://inlined.example.org");
  expect(titilerBaseUrl()).toBe("http://localhost:8084");
  globalThis.__STAC_HIGHER_CONFIG__ = { titilerUrl: "https://runtime.example.org", tipgUrl: "https://vt.example.org" };
  expect(titilerBaseUrl()).toBe("https://runtime.example.org");
  expect(tipgLandingUrl()).toBe("https://vt.example.org/");
});
```
`catalog-store.test.ts`: replace `"uses PUBLIC_BUILTIN_CATALOG_URL when set"` with the two tests below. The `afterEach` also deletes the global.
```ts
it("seeds the built-in URL from the server-injected config", async () => {
  globalThis.__STAC_HIGHER_CONFIG__ = { builtInCatalogUrl: "https://catalog.example.org" };
  const store = await importStore();
  expect(store.$builtInCatalog.get()?.url).toBe("https://catalog.example.org");
});
it("ignores an inlined PUBLIC_BUILTIN_CATALOG_URL and re-pins a persisted entry to the runtime URL", async () => {
  vi.stubEnv("PUBLIC_BUILTIN_CATALOG_URL", "http://example.test:9999");
  globalThis.__STAC_HIGHER_CONFIG__ = { builtInCatalogUrl: "https://catalog-b.example.org" };
  const store = await importStore(persistedCatalogs([
    { id: "built-in", name: "Built-in Catalog", url: "https://catalog-a.example.org", isDefault: true, builtIn: true },
  ]));
  expect(store.$builtInCatalog.get()?.url).toBe("https://catalog-b.example.org");
});
```
Add a `builtinCatalogUrl` (BFF target) block to `runtime-config.test.ts`, using `vi.stubEnv` because the function reads `process.env`:
```ts
it("BFF target: BUILTIN_CATALOG_URL wins, else the public URL (new name, then PUBLIC_)", () => {
  vi.stubEnv("BUILTIN_CATALOG_PUBLIC_URL", "https://catalog.example.org/");
  expect(builtinCatalogUrl()).toBe("https://catalog.example.org");
  vi.stubEnv("BUILTIN_CATALOG_URL", "http://stac-auth-proxy:8000");
  expect(builtinCatalogUrl()).toBe("http://stac-auth-proxy:8000");
});
```
`runtime-config-audit.test.ts` (`// @vitest-environment node`) walks `app/src` and `packages/shared/src` with `readdirSync(dir, { recursive: true })`. It covers `.ts`, `.tsx`, `.astro`, `.mjs` and skips `__tests__/`, `*.test.*`, `*.stories.*`. The scan is textual and comments count. Comments in non-test sources (including Task 1's) must say "Vite-inlined `PUBLIC_*` vars", never the literal `import.meta.env` token.
```ts
it("no source reads import.meta.env.PUBLIC_* (browser config is runtime, spec §8)", () => {
  expect(filesMatching(/import\.meta\.env\.PUBLIC_/)).toEqual([]);
});
it("import.meta.env is read only for the build-mode flag in auth/config.ts", () => {
  expect(filesMatching(/import\.meta\.env/)).toEqual(["app/src/lib/auth/config.ts"]);
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd app && npx vitest run src/__tests__/serving-urls.test.ts src/__tests__/catalog-store.test.ts src/__tests__/runtime-config.test.ts src/__tests__/runtime-config-audit.test.ts`
Expected: FAIL. The audit lists `catalogStore.ts`, `serving/urls.ts` and `ProcessDetailPage.tsx`, and the store and URL tests see the inlined env.

- [ ] **Step 3: Implement the consumers.**
  - `catalogStore.ts`: `builtInCatalogUrl()` returns `getRuntimeConfig().builtInCatalogUrl`. Delete `DEFAULT_BUILT_IN_URL` and the `import.meta` guard. The comment says the server injects the value per request (Layout.astro).
  - `serving/urls.ts`: the new signatures above. Keep `.replace(/\/+$/, "")`. Delete both `DEFAULT_*_URL` constants (the defaults live in `DEFAULT_RUNTIME_CONFIG`).
  - `ProcessDetailPage.tsx`: `getNetworkMax({ PROCESS_NETWORK_MAX: getRuntimeConfig().processNetworkMax })` inside the existing try/catch. The comment says the server mirrors the enforced `PROCESS_NETWORK_MAX`.
  - `transactions.ts`: `(process.env.BUILTIN_CATALOG_URL?.trim() || runtimeConfigValue("builtInCatalogUrl")).replace(/\/+$/, "")`. Rewrite the doc comment: there is no longer a default literal to keep in sync.
  - `CollectionList.tsx:27`: "…check BUILTIN_CATALOG_PUBLIC_URL."

- [ ] **Step 4: Run to verify they pass** (same command as Step 2). Expected: PASS.

- [ ] **Step 5: Prove the bundle no longer inlines.** Run from the repo root:

```bash
PUBLIC_BUILTIN_CATALOG_URL=https://inline-canary.invalid PUBLIC_TITILER_URL=https://inline-canary.invalid \
PUBLIC_TIPG_URL=https://inline-canary.invalid npm run build --workspace app \
&& ! grep -rl "inline-canary" app/dist/client && grep -rl "__STAC_HIGHER_CONFIG__" app/dist/server | head -1
```
Expected: no file listed by the first grep. The second prints one `app/dist/server/...` chunk.

- [ ] **Step 6: Docs.**
  - `docs/backend.md`: replace the `PUBLIC_BUILTIN_CATALOG_URL` row with a **Browser runtime config** row. That row gives the five keys, their env names and fallbacks, and defaults (the Global Constraints table). It says the values are read by the server per request and injected into every page as `window.__STAC_HIGHER_CONFIG__`, so a change needs a restart, not a rebuild, and that a non-http(s) URL fails every page render with the variable named. Add a `BUILTIN_CATALOG_URL` row: the server-side BFF forward target, falling back to the public catalog URL. In a cluster it is the internal service address, and that host must then be in `SAFE_FETCH_ALLOW_HOSTS`, because `forwardUpstream` and `snapshotPriorItem` go through `safeFetch`. Line 14: `PUBLIC_BUILTIN_CATALOG_URL` → `BUILTIN_CATALOG_PUBLIC_URL`.
  - `docs/serving.md`: env table → `TITILER_URL` / `TIPG_URL` (fallback `PUBLIC_*`), plus `CUBE_SERVER_URL` (`http://localhost:8086`, carried for the chart; no UI consumer yet).
  - `docs/auth.md:142-143`: "falls back to the public catalog URL (`BUILTIN_CATALOG_PUBLIC_URL`, then `PUBLIC_BUILTIN_CATALOG_URL`)".
  - `app/README.md:109`: same rename.
  - `.env.example`: the OGC block lists `# TITILER_URL=`, `# TIPG_URL=`, `# CUBE_SERVER_URL=`, `# BUILTIN_CATALOG_PUBLIC_URL=`, with a note that the old `PUBLIC_*` names still work.

- [ ] **Step 7: Run verify, commit**

Run: `npm run verify` (repo root). Expected: check, build and tests all green.
```bash
git add -A app/src docs/backend.md docs/serving.md docs/auth.md app/README.md .env.example
git commit -m "R-1: islands read browser URLs from the runtime config; PUBLIC_* audit test"
```

---

### Task 3: S3 default credential chain

**Files:**
- Modify: `app/src/lib/storage/config.ts` (`StorageConfig`, `getStorageConfig`)
- Modify: `app/src/lib/storage/client.ts` (`buildClient`)
- Test: `app/src/__tests__/storage-config-presign.test.ts` (update the first test and `TEST_CONFIG`; add cases)
- Docs: `docs/backend.md` S3 row; `.env.example` S3 block comment

**Interfaces:**
- Produces:
  ```ts
  export interface StorageCredentials { accessKeyId: string; secretAccessKey: string }
  export interface StorageConfig {
    endpoint: string; region: string; bucket: string;
    /** null = the AWS SDK default credential chain (Pod Identity / IRSA / env / profile). */
    credentials: StorageCredentials | null;
    forcePathStyle: boolean;
  }
  export function getStorageConfig(env?: Record<string, string | undefined>): StorageConfig;
  export function s3ClientConfig(config: StorageConfig): S3ClientConfig; // client.ts
  export function buildClient(config: StorageConfig): S3Client;          // = new S3Client(s3ClientConfig(config))
  ```

- [ ] **Step 1: Write the failing tests**

```ts
it("defaults to the local MinIO compose settings when nothing is set", () => {
  expect(getStorageConfig({})).toEqual({
    endpoint: "http://localhost:9000", region: "us-east-1", bucket: "stac-higher",
    credentials: { accessKeyId: "minioadmin", secretAccessKey: "minioadmin" }, forcePathStyle: true,
  });
});
it("uses the default chain when an endpoint is configured without keys", () => {
  expect(getStorageConfig({ S3_ENDPOINT: "https://s3.us-east-1.amazonaws.com" }).credentials).toBeNull();
});
it("uses the default chain when both keys are explicitly empty, even with no endpoint", () => {
  expect(getStorageConfig({ S3_ACCESS_KEY_ID: "", S3_SECRET_ACCESS_KEY: " " }).credentials).toBeNull();
});
it("keeps explicit static keys", () => {
  expect(getStorageConfig({ S3_ACCESS_KEY_ID: "AKIA", S3_SECRET_ACCESS_KEY: "shhh" }).credentials)
    .toEqual({ accessKeyId: "AKIA", secretAccessKey: "shhh" });
});
it("refuses exactly one key (Review Focus 2)", () => {
  expect(() => getStorageConfig({ S3_ACCESS_KEY_ID: "AKIA" })).toThrow(/S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY/);
  expect(() => getStorageConfig({ S3_ENDPOINT: "https://s3.example.org", S3_SECRET_ACCESS_KEY: "shhh" })).toThrow(/S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY/);
});
it("omits credentials from the S3 client config for the default chain", () => {
  expect("credentials" in s3ClientConfig({ ...TEST_CONFIG, credentials: null })).toBe(false);
  expect(s3ClientConfig(TEST_CONFIG).credentials).toEqual(TEST_CONFIG.credentials);
});
it("presigns with whatever the default chain resolves (env provider here)", async () => {
  vi.stubEnv("AWS_ACCESS_KEY_ID", "AKIDEFAULTCHAIN");
  vi.stubEnv("AWS_SECRET_ACCESS_KEY", "default-chain-secret");
  const url = await presignGetUrl("assets/c/i/x.tif", { config: { ...TEST_CONFIG, credentials: null } });
  expect(url).toContain("X-Amz-Credential=AKIDEFAULTCHAIN");
});
```
`TEST_CONFIG` becomes `{ …, credentials: { accessKeyId: "minioadmin", secretAccessKey: "minioadmin" }, … }`. Add `afterEach(() => vi.unstubAllEnvs())`.

- [ ] **Step 2: Run to verify they fail**

Run: `cd app && npx vitest run src/__tests__/storage-config-presign.test.ts`
Expected: FAIL (`credentials` undefined, `s3ClientConfig` not exported).

- [ ] **Step 3: Implement.** In `getStorageConfig`, a key is "empty" when it is `undefined` or blank after trim. The raw value is used when non-empty (secrets are not trimmed). Both empty: MinIO defaults if `S3_ENDPOINT`, `S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY` are all `undefined`, else `null`. One empty: throw `"S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be set together, or both left empty to use the AWS default credential chain"`. In `s3ClientConfig`, spread `credentials` only when non-null. Update both file header comments. `config.ts`: the dev-only MinIO default exists so that a laptop with personal AWS credentials never signs MinIO URLs with them. `client.ts`: the default chain refreshes Pod Identity / IRSA credentials itself, and the 300 s / 900 s presign TTLs sit well inside a session.

- [ ] **Step 4: Run to verify they pass** (same command). Expected: PASS.

- [ ] **Step 5: Docs.** `docs/backend.md` S3 row: add the credential rule. Both keys empty → AWS SDK default credential chain (Pod Identity / IRSA on EKS). Nothing S3 set at all → compose MinIO `minioadmin`. One key set → error. `.env.example`: one comment line above the commented S3 keys saying the same.

- [ ] **Step 6: Commit**

```bash
git add app/src/lib/storage app/src/__tests__/storage-config-presign.test.ts docs/backend.md .env.example
git commit -m "R-1: S3 presigning falls back to the AWS default credential chain"
```

---

### Task 4: RDS TLS for node-postgres

**Files:**
- Modify: `app/src/lib/db/connection.ts`
- Test: `app/src/__tests__/db-connection-config.test.ts` (create; `// @vitest-environment node`)
- Docs: `docs/backend.md` `DATABASE_URL` row + new `PGSSLROOTCERT` row; `.env.example` (commented `# PGSSLROOTCERT=`)

**Interfaces:**
- Produces:
  ```ts
  export const DEFAULT_DATABASE_URL = "postgresql://username:password@localhost:5433/postgis";
  export function poolConfigFromEnv(
    env?: Record<string, string | undefined>,
    readFile?: (path: string) => string, // default: (p) => readFileSync(p, "utf8")
  ): pg.PoolConfig;
  // query() / getClient() unchanged; getPool() builds from poolConfigFromEnv() on first use.
  ```

- [ ] **Step 1: Write the failing tests**

```ts
it("is today's config when PGSSLROOTCERT is unset or blank", () => {
  expect(poolConfigFromEnv({})).toEqual({ connectionString: DEFAULT_DATABASE_URL });
  expect(poolConfigFromEnv({ DATABASE_URL: "postgresql://u:p@db.example.org:5432/app", PGSSLROOTCERT: " " }))
    .toEqual({ connectionString: "postgresql://u:p@db.example.org:5432/app" });
});
it("verifies the server certificate against the CA bundle when PGSSLROOTCERT is set", () => {
  const readFile = vi.fn(() => "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n");
  const cfg = poolConfigFromEnv({ PGSSLROOTCERT: "/etc/ssl/rds/global-bundle.pem" }, readFile);
  expect(readFile).toHaveBeenCalledWith("/etc/ssl/rds/global-bundle.pem");
  expect(cfg.ssl).toEqual({ ca: "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n", rejectUnauthorized: true });
});
it("fails loudly, naming the path, when the bundle cannot be read (Review Focus 3)", () => {
  const readFile = () => { throw new Error("ENOENT"); };
  expect(() => poolConfigFromEnv({ PGSSLROOTCERT: "/missing.pem" }, readFile)).toThrow(/PGSSLROOTCERT.*\/missing\.pem.*ENOENT/);
});
it("refuses an empty bundle rather than connecting unverified", () => {
  expect(() => poolConfigFromEnv({ PGSSLROOTCERT: "/empty.pem" }, () => "  \n")).toThrow(/PGSSLROOTCERT.*\/empty\.pem.*empty/);
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd app && npx vitest run src/__tests__/db-connection-config.test.ts`
Expected: FAIL (`poolConfigFromEnv` not exported).

- [ ] **Step 3: Implement** `poolConfigFromEnv` with the messages the tests pin: `` `PGSSLROOTCERT: could not read the CA bundle at ${path}: ${msg}` `` and `` `PGSSLROOTCERT: the CA bundle at ${path} is empty` ``. `getPool()` calls it lazily. `DATABASE_URL` was read at module load until now, and reading it at first use is equivalent. The header comment records two node-postgres facts the deployment must know. (a) pg merges a parsed connection string OVER the config object (`pg/lib/connection-parameters.js`), so a `DATABASE_URL` carrying `sslmode=`/`sslrootcert=` overrides this `ssl` object; keep TLS out of the URL and use `PGSSLROOTCERT`. (b) With `PGSSLROOTCERT` unset, pg's own `PGSSLMODE` handling is untouched (today's behaviour). The same variable is what libpq reads natively, so the pipeline (R-2) and the app share one setting. Node's TLS verifies the hostname as well (`servername` is the host), so this is `verify-full`.

- [ ] **Step 4: Run to verify they pass** (same command). Expected: PASS.

- [ ] **Step 5: Docs.** `docs/backend.md`: `DATABASE_URL` row gains "keep `sslmode`/`sslrootcert` out of it (they override `PGSSLROOTCERT`)". New `PGSSLROOTCERT` row: path to the CA bundle (on RDS, the region's bundle, mounted from the chart's `database.caBundle` ConfigMap, spec §7.2). Set → TLS with certificate and hostname verification. Unset → today's behaviour. An unreadable or empty file fails the first query, naming the path.

- [ ] **Step 6: Commit**

```bash
git add app/src/lib/db/connection.ts app/src/__tests__/db-connection-config.test.ts docs/backend.md .env.example
git commit -m "R-1: node-postgres verifies TLS against PGSSLROOTCERT"
```

---

### Task 5: Migrate CLI in the app image

**Files:**
- Create: `app/src/lib/db/migrate-cli.ts`
- Create: `app/src/cli/migrate.ts`
- Modify: `app/package.json` (scripts + `esbuild` devDependency), `package-lock.json` (via npm only)
- Modify: `app/Dockerfile` (build-stage guard + comment)
- Modify: `scripts/apply-app-migrations.ts` (delegate to `runMigrateCli`)
- Test: `app/src/__tests__/migrate-cli.test.ts` (create; `// @vitest-environment node`)
- Docs: `docs/backend.md` (one sentence under the app environment table: "Migrations as a command")

**Interfaces:**
- Consumes: `runMigrations()` from `app/src/lib/db/migrate.ts`; `poolConfigFromEnv` (Task 4) indirectly through `connection.ts`.
- Produces:
  ```ts
  export function redactDatabaseUrl(url: string): string; // "postgresql://u:p@h/db" -> "postgresql://***@h/db"
  export async function runMigrateCli(deps: {
    migrate: () => Promise<void>;
    databaseUrl?: string;
    log?: (line: string) => void;      // default console.log
    error?: (err: unknown) => void;    // default console.error
  }): Promise<0 | 1>;
  ```
  Bundle: `app/dist/server/migrate.mjs`, run as `node app/dist/server/migrate.mjs` from `/srv` in the image.

- [ ] **Step 1: Write the failing tests**

```ts
const BUNDLE = fileURLToPath(new URL("../../dist/server/migrate.mjs", import.meta.url));

it("returns 0 and logs a redacted URL when migrations apply", async () => {
  const log = vi.fn();
  const code = await runMigrateCli({ migrate: async () => {}, databaseUrl: "postgresql://owner:s3cret@db.example.org:5432/app", log });
  expect(code).toBe(0);
  const out = log.mock.calls.flat().join("\n");
  expect(out).toContain("postgresql://***@db.example.org:5432/app");
  expect(out).not.toContain("s3cret");
  expect(out).toContain("Migrations applied.");
});
it("names the dev default when DATABASE_URL is unset", async () => {
  const log = vi.fn();
  await runMigrateCli({ migrate: async () => {}, log });
  expect(log.mock.calls.flat().join("\n")).toContain("DATABASE_URL unset");
});
it("returns 1 and reports the error when migrations fail", async () => {
  const error = vi.fn();
  const code = await runMigrateCli({ migrate: async () => { throw new Error("boom"); }, log: () => {}, error });
  expect(code).toBe(1);
  expect(error).toHaveBeenCalledWith(expect.objectContaining({ message: "boom" }));
});
it.skipIf(!existsSync(BUNDLE))("the built bundle exits 1 against an unreachable database, without leaking the password", () => {
  const r = spawnSync(process.execPath, [BUNDLE], {
    env: { ...process.env, DATABASE_URL: "postgresql://owner:s3cret@127.0.0.1:1/none", PGSSLROOTCERT: "" },
    encoding: "utf8", timeout: 20_000,
  });
  expect(r.status).toBe(1);
  expect(`${r.stdout}${r.stderr}`).not.toContain("s3cret");
}, 30_000);
```
The bundle test runs inside `npm run verify`, because verify builds before it tests. It skips only in a bare `npx vitest run` without a build.

- [ ] **Step 2: Run to verify they fail**

Run: `cd app && npx vitest run src/__tests__/migrate-cli.test.ts`
Expected: FAIL (`@/lib/db/migrate-cli` not found).

- [ ] **Step 3: Implement `migrate-cli.ts`.** Lift the body of `scripts/apply-app-migrations.ts` (log line, redaction regex `/:\/\/[^@]*@/` → `://***@`, "Migrations applied.") into `runMigrateCli`. Catch everything and return 1. The function never calls `process.exit`. A pg error can embed connection details, so `error` receives the error object and the default `console.error` prints it. The bundle test pins that the password does not appear.

- [ ] **Step 4: Implement `app/src/cli/migrate.ts`** with **relative** imports (`../lib/db/migrate`, `../lib/db/migrate-cli`), so esbuild needs no alias resolution. Body: `runMigrateCli({ migrate: runMigrations, databaseUrl: process.env.DATABASE_URL }).then((code) => process.exit(code))`. The comment says `process.exit` is needed because `connection.ts`'s module-level pool keeps the event loop alive, the same reason the existing script gives.

- [ ] **Step 5: Wire the build.** From the repo root: `npm install --save-dev --save-exact esbuild@0.28.2 --workspace app`. Expected: `package-lock.json` changes only in the `app` workspace's devDependencies, and no new `node_modules/esbuild` version. Never hand-edit the lockfile. In `app/package.json`:
  - `"build": "astro build && npm run build:migrate"` (order matters: `astro build` empties `dist/`)
  - `"build:migrate": "esbuild src/cli/migrate.ts --bundle --platform=node --format=esm --target=node22 --packages=external --outfile=dist/server/migrate.mjs"` (`pg` stays external and resolves from the image's pruned `node_modules`)
  - `"migrate": "node dist/server/migrate.mjs"`

- [ ] **Step 6: `scripts/apply-app-migrations.ts`** keeps its header comment (issue #24 context). `main()` becomes `runMigrateCli({ migrate: runMigrations, databaseUrl: process.env.DATABASE_URL }).then((code) => process.exit(code))`, importing `../app/src/lib/db/migrate-cli.ts`. CI's `npm run db:migrate` keeps working unchanged.

- [ ] **Step 7: Dockerfile.** In the build stage, after `npm run build --workspace app`, add `&& test -f app/dist/server/migrate.mjs` to the same `RUN`, so an image without the CLI cannot be built. Above the final `CMD`, add a comment: the chart's app-migrate Job runs `node app/dist/server/migrate.mjs` (spec §6.1) as the owner role. It exits 0 on success, 1 on failure, and the first-request path stays as the fallback. `CMD` is unchanged.

- [ ] **Step 8: Run verify (it builds the bundle, so the bundle test runs), then smoke the bundle by hand**

Run: `npm run verify` (repo root). Expected: green, with `migrate-cli.test.ts` 4 passed (none skipped).
Run: `DATABASE_URL=postgresql://u:p@127.0.0.1:1/none node app/dist/server/migrate.mjs; echo "exit=$?"`
Expected: an error mentioning `ECONNREFUSED`, then `exit=1`.

- [ ] **Step 9: Docs + commit.** `docs/backend.md`, one sentence below the app env table: "**Migrations as a command**: `node app/dist/server/migrate.mjs` in the app image (`npm run migrate --workspace app` locally after a build) applies `runMigrations()` and exits 0/1. It is used by the chart's app-migrate Job. The first-request path stays (advisory-locked, so both can race safely)."

```bash
git add app/src/lib/db/migrate-cli.ts app/src/cli app/package.json package-lock.json app/Dockerfile scripts/apply-app-migrations.ts app/src/__tests__/migrate-cli.test.ts docs/backend.md
git commit -m "R-1: migrate CLI bundled into the app image"
```

---

### Task 6: Credential-budget deploy refusal + contract fixture

**Files:**
- Create: `app/src/lib/processes/credential-budget.ts`
- Create: `tests/contract-fixtures/process-credential-budget.json`
- Modify: `tests/contract-fixtures/README.md` (new section before "## Why `app` and `pipeline` expectations can differ")
- Modify: `app/src/pages/api/processes/[id]/revisions.ts` (builtin path after line 100; hand-written path after line 142)
- Modify: `app/src/pages/api/processes/builtin.ts` (after line 81)
- Test: `app/src/__tests__/processes-credential-budget.test.ts` (create; node env), `app/src/__tests__/contract-fixtures.test.ts` (new describe), `app/src/__tests__/api-processes.test.ts` (deploy describe), `app/src/__tests__/api-processes-builtin.test.ts`
- Docs: `docs/backend.md` (env row; `/api/processes/[id]/revisions` and `/api/processes` route rows gain the 422), `docs/processes.md` (near line 385, the timeout paragraph), `docs/ISSUES.md` (one limitation entry), `.env.example` (process block)

**Interfaces:**
- Consumes: `HardwareProfileSet`, `findProfile` from `@/lib/processes/hardware`; `jsonResponse` from `@/lib/http/response`.
- Produces:
  ```ts
  export const MAX_CREDENTIAL_SECONDS_ENV = "PROCESS_MAX_CREDENTIAL_SECONDS";
  export const CREDENTIAL_GRACE_SECONDS_ENV = "PROCESS_CREDENTIAL_GRACE_SECONDS";
  export const DEFAULT_PROCESS_CREDENTIAL_GRACE_SECONDS = 300;
  export const STS_MIN_SESSION_SECONDS = 900;
  export const CREDENTIAL_BUDGET_CODE = "credential_budget_exceeded";
  export const CREDENTIAL_BUDGET_STATUS = 422;
  export function getMaxCredentialSeconds(env?: Record<string, string | undefined>): number | null;
  export function getCredentialGraceSeconds(env?: Record<string, string | undefined>): number;
  export interface CredentialBudgetInput {
    maxCredentialSeconds: number | null; maxQueueWaitSeconds: number; timeoutSeconds: number; graceSeconds: number;
  }
  export function credentialBudgetError(input: CredentialBudgetInput): string | null;
  export function revisionCredentialBudgetError(
    runtime: { timeout_seconds: number; hardware: { profile: string } },
    profiles: HardwareProfileSet,
    env?: Record<string, string | undefined>,
  ): string | null; // null when allowed, or when the profile is missing (the hardware gate refuses that first)
  export function credentialBudgetRefused(message: string): Response; // 422 { error: message, code: CREDENTIAL_BUDGET_CODE }
  ```

- [ ] **Step 1: Create the fixture with EXACTLY this content** (R-2 creates the identical file, so do not reformat it):

```json
{
  "setting": "PROCESS_MAX_CREDENTIAL_SECONDS",
  "unset_means": "no limit",
  "grace_setting": "PROCESS_CREDENTIAL_GRACE_SECONDS",
  "grace_default_seconds": 300,
  "deploy_refusal": { "status": 422, "code": "credential_budget_exceeded" },
  "mint_refusal": { "error": "credential_budget_exceeded" },
  "cases": [
    { "name": "at budget", "max_credential_seconds": 3600, "max_queue_wait_seconds": 600, "timeout_seconds": 2700, "grace_seconds": 300, "deploy_allowed": true, "mint_allowed": true },
    { "name": "one over", "max_credential_seconds": 3600, "max_queue_wait_seconds": 600, "timeout_seconds": 2701, "grace_seconds": 300, "deploy_allowed": false, "mint_allowed": true },
    { "name": "mint over", "max_credential_seconds": 3600, "max_queue_wait_seconds": 0, "timeout_seconds": 3301, "grace_seconds": 300, "deploy_allowed": false, "mint_allowed": false },
    { "name": "unset", "max_credential_seconds": null, "max_queue_wait_seconds": 0, "timeout_seconds": 86400, "grace_seconds": 300, "deploy_allowed": true, "mint_allowed": true }
  ]
}
```

- [ ] **Step 2: Write the failing tests**

`contract-fixtures.test.ts`:
```ts
describe("process credential budget contract (tests/contract-fixtures/process-credential-budget.json)", () => {
  const fx = loadFixture("process-credential-budget.json") as unknown as {
    setting: string; grace_setting: string; grace_default_seconds: number;
    deploy_refusal: { status: number; code: string };
    cases: { name: string; max_credential_seconds: number | null; max_queue_wait_seconds: number;
             timeout_seconds: number; grace_seconds: number; deploy_allowed: boolean }[];
  };
  it("pins the setting names, the grace default and the refusal", () => {
    expect(fx.setting).toBe(MAX_CREDENTIAL_SECONDS_ENV);
    expect(fx.grace_setting).toBe(CREDENTIAL_GRACE_SECONDS_ENV);
    expect(fx.grace_default_seconds).toBe(DEFAULT_PROCESS_CREDENTIAL_GRACE_SECONDS);
    expect(fx.deploy_refusal).toEqual({ status: CREDENTIAL_BUDGET_STATUS, code: CREDENTIAL_BUDGET_CODE });
  });
  it.each(fx.cases)("$name", (c) => {
    expect(credentialBudgetError({ maxCredentialSeconds: c.max_credential_seconds, maxQueueWaitSeconds: c.max_queue_wait_seconds,
      timeoutSeconds: c.timeout_seconds, graceSeconds: c.grace_seconds }) === null).toBe(c.deploy_allowed);
  });
});
```
`processes-credential-budget.test.ts`:
```ts
it("reads no limit from unset or blank", () => {
  expect(getMaxCredentialSeconds({})).toBeNull();
  expect(getMaxCredentialSeconds({ PROCESS_MAX_CREDENTIAL_SECONDS: " " })).toBeNull();
  expect(getMaxCredentialSeconds({ PROCESS_MAX_CREDENTIAL_SECONDS: "3600" })).toBe(3600);
});
it.each(["1h", "0", "-5", "600", "3600.5"])("refuses a malformed cap %j (Review Focus 4)", (raw) => {
  expect(() => getMaxCredentialSeconds({ PROCESS_MAX_CREDENTIAL_SECONDS: raw })).toThrow(/PROCESS_MAX_CREDENTIAL_SECONDS/);
});
it("reads the grace like the pipeline: default 300, non-negative integer", () => {
  expect(getCredentialGraceSeconds({})).toBe(300);
  expect(getCredentialGraceSeconds({ PROCESS_CREDENTIAL_GRACE_SECONDS: "120" })).toBe(120);
  expect(() => getCredentialGraceSeconds({ PROCESS_CREDENTIAL_GRACE_SECONDS: "five" })).toThrow(/PROCESS_CREDENTIAL_GRACE_SECONDS/);
});
it("names the budget, its parts and the cap in seconds", () => {
  expect(credentialBudgetError({ maxCredentialSeconds: 3600, maxQueueWaitSeconds: 600, timeoutSeconds: 2701, graceSeconds: 300 }))
    .toBe("credential budget 3601s (max_queue_wait_seconds 600 + timeout_seconds 2701 + grace 300) exceeds this deployment's cap of 3600s (PROCESS_MAX_CREDENTIAL_SECONDS)");
});
```
`api-processes.test.ts`, in the deploy describe. The checkout's `infra/hardware-profiles/local.json` `standard` has `max_queue_wait_seconds: 1800`, so the budget is `1800 + timeout + 300`. Add `afterEach(() => vi.unstubAllEnvs())`.
```ts
it.each([[1499, 201], [1500, 201], [1501, 422]])("cap 3600: timeout %i → %i (below, at, above the budget)", async (timeout, status) => {
  vi.stubEnv("PROCESS_MAX_CREDENTIAL_SECONDS", "3600");
  vi.mocked(deployRevision).mockResolvedValue({ id: REVISION_ID } as never);
  const res = await call(deployRoute, operator, { method: "POST",
    body: { runtime: { kind: "inline_python", timeout_seconds: timeout }, code: "print(1)", env: [] } });
  expect(res.status).toBe(status);
  if (status === 422) {
    expect(await res.json()).toEqual({ code: "credential_budget_exceeded",
      error: "credential budget 3601s (max_queue_wait_seconds 1800 + timeout_seconds 1501 + grace 300) exceeds this deployment's cap of 3600s (PROCESS_MAX_CREDENTIAL_SECONDS)" });
    expect(deployRevision).not.toHaveBeenCalled();
  }
});
it("deploys an 86400 s timeout when the cap is unset (today's behaviour)", async () => { /* no stub; expect 201 */ });
```
`api-processes-builtin.test.ts`: two tests with `vi.stubEnv("PROCESS_MAX_CREDENTIAL_SECONDS", "900")`. Budget for `stactools-goes` = 1800 + 300 + 300 = 2400 > 900. (1) `POST /api/processes/builtin` → 422 `credential_budget_exceeded`, `createBuiltinProcess` not called. (2) `{ from_builtin: true }` on the builtin process → 422, `deployRevision` not called.

- [ ] **Step 3: Run to verify they fail**

Run: `cd app && npx vitest run src/__tests__/processes-credential-budget.test.ts src/__tests__/contract-fixtures.test.ts src/__tests__/api-processes.test.ts src/__tests__/api-processes-builtin.test.ts`
Expected: FAIL (module missing; the new route cases get 201 instead of 422).

- [ ] **Step 4: Implement `credential-budget.ts`.** `getMaxCredentialSeconds`: blank → `null`. Otherwise the trimmed value must match `/^\d+$/` and be ≥ `STS_MIN_SESSION_SECONDS` (900; the pipeline never asks STS for less, `MIN_DURATION_SECONDS` in `credentials.py`, so a lower cap could never mint). Else throw, naming the variable, the value and "an integer number of seconds ≥ 900". `getCredentialGraceSeconds`: blank → 300, else `/^\d+$/`, else throw. The budget is the sum of the three. `credentialBudgetError` returns `null` when `maxCredentialSeconds === null || budget <= max`, otherwise the message pinned above. `revisionCredentialBudgetError` reads both settings from `env` (default `process.env`), finds the profile, and returns `null` if it is absent. The header comment cites spec §8: the role-chaining 1 h cap, and "the pipeline refuses at mint (R-2); this is the deploy-time half so the operator hears no at the form".

- [ ] **Step 5: Apply at the three deploy paths.** Directly after each existing `hardwareBoundsError` check (`revisions.ts` builtin path and hand-written path, `builtin.ts`), pass the same `loadHardwareProfiles()` result: hoist it into a `const profiles` where the call is now inline. Then: `const budgetError = revisionCredentialBudgetError(<runtime>, profiles); if (budgetError) return credentialBudgetRefused(budgetError);`. `<runtime>` is `data.runtime` or `template.runtime`. A malformed cap throws into the routes' existing `catch`, which gives a 500 with the message (the same posture as a malformed `PROCESS_NETWORK_MAX`). Add one line to the `revisions.ts` header comment.

- [ ] **Step 6: Run to verify they pass** (same command). Expected: PASS.

- [ ] **Step 7: README entry.** Add the following before "## Why `app` and `pipeline` expectations can differ" in `tests/contract-fixtures/README.md`:

```markdown
## Additional fixture styles (R queue, R-1/R-2)

- `process-credential-budget.json` — a settings contract (no `style` marker). It pins the deployment cap `PROCESS_MAX_CREDENTIAL_SECONDS` (unset = no limit), the grace setting `PROCESS_CREDENTIAL_GRACE_SECONDS` and its default (300 s), the deploy refusal (422, `code` `credential_budget_exceeded`) and the mint refusal's error. Each `cases[]` entry gives the three budget inputs and the cap, with `deploy_allowed` (the app's deploy gate, `app/src/lib/processes/credential-budget.ts`: `max_queue_wait_seconds + timeout_seconds + grace ≤ cap`) and `mint_allowed` (the pipeline's STS mint, `pipeline/process/credentials.py`). The vitest consumer checks `deploy_allowed`; pytest checks `mint_allowed`.
```

- [ ] **Step 8: Docs.**
  - `docs/backend.md`: env row `PROCESS_MAX_CREDENTIAL_SECONDS` / `PROCESS_CREDENTIAL_GRACE_SECONDS`. Integer seconds, unset = no limit, at least 900. Set to `3600` on AWS (role-chaining cap). The grace is shared with the pipeline (default 300). The `/api/processes/[id]/revisions` row: add **422** `credential_budget_exceeded` when the profile's `max_queue_wait_seconds` + `timeout_seconds` + grace exceeds the cap; the same on `POST /api/processes/builtin` (find its row and add it).
  - `docs/processes.md` near line 385: two sentences for authors. On a deployment with a credential cap, `timeout_seconds` plus the profile's queue wait plus the grace must fit the cap, or the deploy is refused with 422 `credential_budget_exceeded`. On AWS the cap is 3600 s.
  - `docs/ISSUES.md`: the next free `I-` number (I-148 at plan time; renumber if R-2 took it). Entry 🟡 "Credential budget is checked at deploy only". A revision deployed before the cap was set, or before its profile's `max_queue_wait_seconds` grew, is not re-checked. The pipeline's mint refusal (R-2) is the backstop and fails the run naming the cap. Until K-4 (#12) the pipeline mints `timeout + grace` without the queue wait, so the deploy gate is deliberately stricter than the mint.
  - `.env.example`: `# PROCESS_MAX_CREDENTIAL_SECONDS=` under the existing grace line, with a one-line comment.

- [ ] **Step 9: Run verify, commit**

Run: `npm run verify`. Expected: green.
```bash
git add app/src/lib/processes/credential-budget.ts app/src/pages/api/processes tests/contract-fixtures app/src/__tests__ docs .env.example
git commit -m "R-1: refuse a process deploy whose credential budget exceeds PROCESS_MAX_CREDENTIAL_SECONDS"
```

---

### Task 7: Configurable bearer audience claim + FEATURES entry

**Files:**
- Modify: `app/src/lib/auth/config.ts` (`AuthConfig` gains `bearerAudienceClaim`; parsed in `getAuthConfig`)
- Modify: `app/src/lib/auth/bearer.ts` (`verifyBearerToken`)
- Modify: every test literal typed `AuthConfig` that `astro check` flags after the field is added (at plan time: `api-catalog-push.test.ts:~76`, `api-catalog-bff.test.ts:~51`, `auth-resolve.test.ts:~11`, `auth-bearer.test.ts:~254`). Each gains `bearerAudienceClaim: "aud"`.
- Test: `app/src/__tests__/auth-bearer.test.ts`, `app/src/__tests__/auth-config.test.ts`
- Docs: `docs/auth.md` (env table row after `AUTH_BEARER_AUDIENCES`; the "Verification" bullet), `.env.example` (bearer block), `docs/FEATURES.md` (slice entry)

**Interfaces:**
- Consumes: `getBearerAudiences` (existing).
- Produces (in `config.ts`, so `bearer.ts` keeps importing from config and nothing cycles):
  ```ts
  export const BEARER_AUDIENCE_CLAIMS = ["aud", "client_id"] as const;
  export type BearerAudienceClaim = (typeof BEARER_AUDIENCE_CLAIMS)[number];
  /** Unset or blank -> "aud". Trimmed, CASE-SENSITIVE: exactly "aud" or "client_id", else throws. */
  export function parseBearerAudienceClaim(env?: Env): BearerAudienceClaim;
  export interface AuthConfig { /* existing fields */ bearerAudienceClaim: BearerAudienceClaim; }
  // getAuthConfig(env) sets bearerAudienceClaim: parseBearerAudienceClaim(env), so a bad value throws at config load.
  ```
  Error message (pinned): `` `AUTH_BEARER_AUDIENCE_CLAIM must be "aud" or "client_id" (case-sensitive); got ${JSON.stringify(raw)}` ``.

- [ ] **Step 1: Write the failing tests.**

`auth-config.test.ts`:
```ts
describe("AUTH_BEARER_AUDIENCE_CLAIM (config load, R-3-agreed semantics)", () => {
  it("unset or blank means aud", () => {
    expect(getAuthConfig({}).bearerAudienceClaim).toBe("aud");
    expect(getAuthConfig({ AUTH_BEARER_AUDIENCE_CLAIM: "  " }).bearerAudienceClaim).toBe("aud");
  });
  it("accepts aud and client_id, trimmed", () => {
    expect(getAuthConfig({ AUTH_BEARER_AUDIENCE_CLAIM: "aud" }).bearerAudienceClaim).toBe("aud");
    expect(getAuthConfig({ AUTH_BEARER_AUDIENCE_CLAIM: " client_id " }).bearerAudienceClaim).toBe("client_id");
  });
  it.each(["azp", "Client_ID", "AUD", "client-id"])("fails config load on %j, naming the setting (Review Focus 5)", (raw) => {
    expect(() => getAuthConfig({ AUTH_BEARER_AUDIENCE_CLAIM: raw })).toThrow(/AUTH_BEARER_AUDIENCE_CLAIM must be "aud" or "client_id"/);
  });
});
```
`auth-bearer.test.ts`: extend `TokenOpts.audience` to `string | null`, where `null` omits `aud` (a Cognito access token has none). `payload` already lets a test add `client_id`/`token_use`.
```ts
const COGNITO = { client_id: "3example0client0id", token_use: "access", scope: "stac-higher/push" };
const clientIdEnv = (issuer: string) => ({
  AUTH_MODE: "oidc", OIDC_ISSUER: issuer, AUTH_BEARER_AUDIENCE_CLAIM: "client_id",
  AUTH_BEARER_AUDIENCES: "3example0client0id",
});

it("client_id mode accepts a Cognito-shaped access token (client_id, no aud)", async () => {
  const issuer = "http://idp/realms/bearer-cognito-ok"; stubIdp(issuer);
  const token = await signBearer({ issuer, audience: null, payload: COGNITO });
  const result = await resolveRequestAuth(makeJar(), apiRequest(token), clientIdEnv(issuer));
  expect(result.auth.authenticated).toBe(true);
  expect(result.bearerToken).toBe(token);
});
it("client_id mode rejects a mismatched client_id", async () => {
  const issuer = "http://idp/realms/bearer-cognito-mismatch"; stubIdp(issuer);
  const token = await signBearer({ issuer, audience: null, payload: { ...COGNITO, client_id: "someone-else" } });
  expect((await resolveRequestAuth(makeJar(), apiRequest(token), clientIdEnv(issuer))).auth.authenticated).toBe(false);
});
it("client_id mode rejects a token that carries aud, even with a matching client_id (mirrors the proxy)", async () => {
  const issuer = "http://idp/realms/bearer-cognito-hasaud"; stubIdp(issuer);
  const token = await signBearer({ issuer, audience: "3example0client0id", payload: COGNITO });
  expect((await resolveRequestAuth(makeJar(), apiRequest(token), clientIdEnv(issuer))).auth.authenticated).toBe(false);
});
it("aud mode (default) is unchanged: a Cognito-shaped token is rejected", async () => {
  const issuer = "http://idp/realms/bearer-cognito-audmode"; stubIdp(issuer);
  const token = await signBearer({ issuer, audience: null, payload: COGNITO });
  const env = { AUTH_MODE: "oidc", OIDC_ISSUER: issuer, AUTH_BEARER_AUDIENCES: "3example0client0id" };
  expect((await resolveRequestAuth(makeJar(), apiRequest(token), env)).auth.authenticated).toBe(false);
});
```
Every pre-existing `aud`-mode bearer test stays as is. They are the "unchanged" proof.

- [ ] **Step 2: Run to verify they fail**

Run: `cd app && npx vitest run src/__tests__/auth-config.test.ts src/__tests__/auth-bearer.test.ts`
Expected: FAIL (`bearerAudienceClaim` undefined; the Cognito token is rejected in client_id mode).

- [ ] **Step 3: Implement.**
  - `config.ts`: `parseBearerAudienceClaim`. Trim; blank → `"aud"`; exact match against `BEARER_AUDIENCE_CLAIMS`; else throw the pinned message. Add the field to `AuthConfig` and to `getAuthConfig`'s return. Doc comment: there is no startup hook in Astro SSR, so config load is the gate. The middleware calls `getAuthConfig` on every request, so a bad value 500s every request with this message rather than being read as `aud`.
  - Add the field to the flagged test literals.
  - `bearer.ts` `verifyBearerToken`: `aud` mode (`cfg.bearerAudienceClaim === "aud"`) is exactly today's `jwtVerify(..., { issuer, audience: getBearerAudiences(env) })`. `client_id` mode calls `jwtVerify(..., { issuer })` with no `audience`. Then it throws inside the existing `try` (→ warn + anonymous) when `payload.aud !== undefined` (`"client_id mode: token carries an aud claim"`), or when `typeof payload.client_id !== "string" || !getBearerAudiences(env).includes(payload.client_id)` (`` `client_id ${JSON.stringify(payload.client_id)} is not one of AUTH_BEARER_AUDIENCES` ``). `verifyBearerToken` calls `getAuthConfig(env)` before its `try`, so a bad setting throws out of it. That is intended: it is a config error, and `resolveRequestAuth` already called `getAuthConfig` on the session path anyway. Header comment: the audience check is `aud`, or `client_id` for Cognito access tokens, per `AUTH_BEARER_AUDIENCE_CLAIM` (spec §8). The semantics match the R-3 proxy half.

- [ ] **Step 4: Run to verify they pass** (same command). Expected: PASS, including every pre-existing auth test.

- [ ] **Step 5: Docs.**
  - `docs/auth.md`: env table row `AUTH_BEARER_AUDIENCE_CLAIM` | `aud` | the claim `AUTH_BEARER_AUDIENCES` is matched against. `aud` (Keycloak, Entra, most IdPs; unchanged behaviour), or `client_id` (Cognito access tokens): `AUTH_BEARER_AUDIENCES` then lists allowed client ids, and a token carrying any `aud` is rejected. Trimmed, case-sensitive; any other value fails config load, so every request 500s with the message. Same semantics at the enforced proxy (R-3). The "Verification" bullet changes to "the claim named by `AUTH_BEARER_AUDIENCE_CLAIM` (`aud` by default) must contain one of…". Add one sentence to the Cognito claims-mapping example: with Cognito, also set `AUTH_BEARER_AUDIENCE_CLAIM=client_id` and list the app client id in `AUTH_BEARER_AUDIENCES`.
  - `.env.example` bearer block: `# AUTH_BEARER_AUDIENCE_CLAIM=aud` with a one-line comment.
  - `docs/FEATURES.md`: change the header `## Phase 8 — Not started ⬜` to `## Phase 8 — In progress 🔄`. Under it, add `### Deployment artifacts (R queue)` with one line naming the spec and epic #136, and a table (`| Feature | Status | Notes |`) with one row. **R-1 · app runtime config** ✅: browser URLs injected at runtime (`app/src/lib/runtime-config/`, `window.__STAC_HIGHER_CONFIG__`; `BUILTIN_CATALOG_PUBLIC_URL`/`TITILER_URL`/`TIPG_URL`/`CUBE_SERVER_URL`, `PUBLIC_*` fallbacks); S3 default credential chain; `PGSSLROOTCERT` TLS; `PROCESS_MAX_CREDENTIAL_SECONDS` deploy refusal (422 `credential_budget_exceeded`, fixture `process-credential-budget.json`); `AUTH_BEARER_AUDIENCE_CLAIM`; `node app/dist/server/migrate.mjs`.

- [ ] **Step 6: Final verify + commit**

Run: `npm run verify` (repo root). Expected: green.
Run: `git grep -nE "arn:aws|[0-9]{12}|amazonaws\.com/[a-z]" -- docs .env.example app/src | grep -v node_modules`
Expected: no account ids or ARNs introduced by this branch (placeholders only). Any hit must predate the branch (`git diff origin/main --stat` to confirm).
```bash
git add app/src/lib/auth app/src/__tests__ docs/auth.md docs/FEATURES.md .env.example
git commit -m "R-1: configurable bearer audience claim (aud | client_id)"
```

---

## PR notes for the lead

- Title `R-1: app runtime config, S3 credential chain, RDS TLS, credential budget, audience claim, migrate CLI`; body starts `Closes #137`.
- Gates run: `npm run verify`. Lead-only, recommended: e2e (`npm run test:e2e:ci`). Every page's `<head>` changed and the built-in catalog seeding moved to the runtime config, so the suite is the real check that islands hydrate with the injected object. No Docker or live step is required (the cloud check is R-11). An image build (`docker build -f app/Dockerfile …`) would confirm the `test -f` guard.
- Coordinate with R-2: identical `process-credential-budget.json` (verbatim above) and identical README section text (Task 6 Step 7). The cap's validation rule (integer ≥ 900, blank = no limit) is app-side only; R-2 should match it. ISSUES number collision possible (I-148).
- For R-6 (chart): the app needs `BUILTIN_CATALOG_URL` (internal) plus `SAFE_FETCH_ALLOW_HOSTS` for that host, and `BUILTIN_CATALOG_PUBLIC_URL` / `TITILER_URL` / `TIPG_URL` / `CUBE_SERVER_URL` from `publicUrls`, `S3_ENDPOINT` from `publicUrls.objectStore`, `PGSSLROOTCERT` mounted from `database.caBundle`, `PROCESS_MAX_CREDENTIAL_SECONDS=3600`, and the migrate Job command `node app/dist/server/migrate.mjs`.
