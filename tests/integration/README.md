# Integration tests

Black-box tests that exercise the running docker-compose stack over HTTP.
They complement (not replace) the unit suite (`npm run verify`) and the
Playwright e2e suite (`app/e2e/`): everything here needs Docker and is
therefore run **only by the lead / a human**, never by teammate agents.

Runner: Node's built-in `node:test` + global `fetch` — zero dependencies, no
build step.

## Running

```sh
# 1. Fresh Keycloak volume so the realm (test users/clients) re-imports.
#    Only needed the first time or after editing infra/keycloak/*.json.
docker compose down -v

# 2. The enforced overlay REQUIRES the BFF shared secret (ADR 0015) — compose
#    fails fast without it. Export it (or put it in .env at the repo root);
#    keep it exported for step 4 so the BFF-exemption legs can send it.
export CATALOG_BFF_SHARED_SECRET=$(openssl rand -hex 32)

# 3. Start the stack WITH auth enforcement (see infra/compose.auth-enforced.yml).
#    --build compiles the derived proxy image (upstream + the ADR 0015
#    write-policy factory from services/proxy-policy).
docker compose -f docker-compose.yml -f infra/compose.auth-enforced.yml up -d --build --wait

# 4. From the repo root:
npm run test:integration        # = node --test "tests/integration/**/*.test.mjs"
```

## Skip behavior

The suite never fails when the environment isn't there — it skips with a
reason printed to stderr when:

- Keycloak (`:8180`, override with `KEYCLOAK_URL`) is unreachable, or
- stac-auth-proxy (`:8081`, override with `AUTH_PROXY_URL`) is unreachable, or
- enforcement is off (anonymous `POST /collections` is accepted — i.e. the
  stack was started with plain `docker compose up`).

So it is always safe to run; it only asserts against the enforced stack.

## What is covered

`proxy-enforcement.test.mjs` — stac-auth-proxy transaction protection
(ROADMAP §4 explicitly distrusts this v1.0-era feature path):

- anonymous `POST /collections` → 401/403
- anonymous `GET /collections` → 200 (reads stay public this phase)
- alice (operator) `POST` / `PUT` / `DELETE /collections/*` → succeeds
- bob (member) can still write **collections** — collection-level
  transactions remain JWT-only (the ADR 0002 limitation, narrowed by ADR
  0015 to collection level only; item writes are policy-gated below)
- master-realm token (wrong issuer/signature) → 401/403
- same-realm token without the `stac-higher` audience → 401/403

`proxy-policy.test.mjs` — the ADR 0015 write policy (the
`services/proxy-policy` items-filter factory baked into the derived proxy
image by the enforced overlay). Additionally skips — with a printed reason —
when the enforced stack runs the plain upstream image (policy probe: a
member item write must be 403). Uses `docker compose exec database psql` to
flag a collection `externally_writable` (the settings API lives in the app,
which this suite does not require); legs needing the flag skip if psql is
unreachable, and the BFF legs skip when `CATALOG_BFF_SHARED_SECRET` is not
in the test process env. Covers:

- reads unchanged with a filter configured: anonymous `GET
  /collections/{id}/items` keeps its FeatureCollection shape (the appended
  `filter=true` is invisible) and anonymous `POST /search` still works (the
  read-served-by-POST carve-out)
- bob (member) item write → 403 even on a flagged collection (role floor)
- alice (operator) item write to a non-flagged collection → 403;
  full item CRUD on a flagged collection succeeds (within the factory's
  ~15 s set-cache TTL — the test retries)
- `bulk_items` without the BFF header → 4xx denial even for an operator
  (denial *shape* recorded as a diagnostic for P7-Z), and no items land
- `X-BFF-Auth` with the correct secret exempts a member writing to an
  unflagged collection; a wrong value falls through to policy → 403

Direct-push item bodies must include `"collection"` — the policy filter
references it, and upstream cql2 raises (500-shaped) when it is missing.

`bff-catalog-writes.test.mjs` — the ADR 0008 BFF (UI-path catalog writes
under enforcement). Additionally needs the Astro app running in OIDC mode
(from `app/`: `AUTH_MODE=oidc SESSION_SECRET=<long random> npm run dev`;
override the test's target with `APP_URL`). Skips when the app is down or in
dev-bypass mode. Covers:

- anonymous BFF write → 401 at the app guard (never reaches the proxy)
- real session login (authorization-code flow against the Keycloak login
  form) → `POST/PUT/DELETE /api/catalog/collections*` with only the httpOnly
  session cookie → 2xx through the ENFORCED proxy → verified via proxy reads
- the create lands an `audit_log` row (`catalog_collection`, attributed)
- non-transaction paths (e.g. `/search`) → 404 — the BFF is not a generic proxy

Test identities live in `infra/keycloak/realm-stac-higher.json`
(alice/alice-password, bob/bob-password, carol/carol-password; password grant
via the confidential `stac-higher-test` client). Local dev only.
