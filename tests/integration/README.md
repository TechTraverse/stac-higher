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

# 2. Start the stack WITH auth enforcement (see infra/compose.auth-enforced.yml).
docker compose -f docker-compose.yml -f infra/compose.auth-enforced.yml up -d --wait

# 3. From the repo root:
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
- bob (member) can also write — **documented limitation** pinned by a test;
  role gating needs OPA or a custom filter factory (ADR 0002)
- master-realm token (wrong issuer/signature) → 401/403
- same-realm token without the `stac-higher` audience → 401/403

Test identities live in `infra/keycloak/realm-stac-higher.json`
(alice/alice-password, bob/bob-password, carol/carol-password; password grant
via the confidential `stac-higher-test` client). Local dev only.
