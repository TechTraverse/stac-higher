// Integration tests: stac-auth-proxy transaction enforcement (Phase 1).
//
// ROADMAP §4 flags stac-auth-proxy's transaction filtering as v1.0-era —
// these tests exercise the write path instead of assuming it.
//
// Preconditions (see tests/integration/README.md):
//   docker compose down -v          # once, so the realm re-imports test users
//   docker compose -f docker-compose.yml -f infra/compose.auth-enforced.yml up -d --wait
//
// Run (repo root):
//   npm run test:integration        # = node --test "tests/integration/**/*.test.mjs"
//
// The whole file SKIPS (never fails) when Keycloak (:8180) or the proxy
// (:8081) is unreachable, or when enforcement is off (anonymous transactions
// accepted → base pass-through stack).
//
// Zero dependencies: node:test + global fetch (Node >= 22 per root engines).

import { test } from "node:test";
import assert from "node:assert/strict";

const KEYCLOAK_URL = process.env.KEYCLOAK_URL ?? "http://localhost:8180";
const PROXY_URL = process.env.AUTH_PROXY_URL ?? "http://localhost:8081";
const REALM = "stac-higher";

// Local-dev credentials from infra/keycloak/realm-stac-higher.json.
const TEST_CLIENT = { id: "stac-higher-test", secret: "stac-higher-test-secret" };
const NOAUD_CLIENT = { id: "stac-higher-test-noaud", secret: "stac-higher-test-noaud-secret" };
const ALICE = { username: "alice", password: "alice-password" }; // operator, /earth-observation
const BOB = { username: "bob", password: "bob-password" }; // member, /weather

const PROBE_TIMEOUT_MS = 3_000;
const REQUEST_TIMEOUT_MS = 15_000;

function collectionBody(id, description = "proxy enforcement integration test") {
  return {
    type: "Collection",
    id,
    stac_version: "1.0.0",
    description,
    license: "proprietary",
    extent: {
      spatial: { bbox: [[-180, -90, 180, 90]] },
      temporal: { interval: [[null, null]] },
    },
    links: [],
  };
}

async function probe(url) {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(PROBE_TIMEOUT_MS) });
    return res.status;
  } catch {
    return null;
  }
}

async function proxyFetch(path, { method = "GET", token, body } = {}) {
  const headers = {};
  if (token) headers.authorization = `Bearer ${token}`;
  if (body !== undefined) headers["content-type"] = "application/json";
  return fetch(`${PROXY_URL}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
}

/** Resource-owner password grant against Keycloak; returns an access token. */
async function getToken(realm, params) {
  const res = await fetch(`${KEYCLOAK_URL}/realms/${realm}/protocol/openid-connect/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(params),
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  if (!res.ok) {
    throw new Error(
      `token request to realm "${realm}" failed: ${res.status} ${await res.text()}`,
    );
  }
  return (await res.json()).access_token;
}

const userToken = (user, client = TEST_CLIENT) =>
  getToken(REALM, {
    grant_type: "password",
    client_id: client.id,
    client_secret: client.secret,
    username: user.username,
    password: user.password,
  });

/** Best-effort cleanup so a failed assertion doesn't strand test collections. */
async function deleteCollection(id) {
  try {
    const token = await userToken(ALICE);
    await proxyFetch(`/collections/${id}`, { method: "DELETE", token });
  } catch {
    /* cleanup only */
  }
}

// ---------------------------------------------------------------------------
// Preconditions (top-level await): decide once whether the suite runs.
// ---------------------------------------------------------------------------
let skip = false;

const kcStatus = await probe(`${KEYCLOAK_URL}/realms/${REALM}/.well-known/openid-configuration`);
if (kcStatus !== 200) {
  skip = `Keycloak realm "${REALM}" not reachable at ${KEYCLOAK_URL} (status: ${kcStatus}) — start the stack: docker compose -f docker-compose.yml -f infra/compose.auth-enforced.yml up -d --wait`;
} else {
  const proxyStatus = await probe(`${PROXY_URL}/`);
  if (proxyStatus !== 200) {
    skip = `stac-auth-proxy not reachable at ${PROXY_URL} (status: ${proxyStatus}) — start the stack: docker compose -f docker-compose.yml -f infra/compose.auth-enforced.yml up -d --wait`;
  } else {
    // Enforcement probe with a deliberately INVALID body so nothing is ever
    // created: pass-through forwards it to stac-fastapi (400/422 validation
    // error); with enforcement on, the proxy answers 401/403 before the
    // upstream ever sees the request.
    const res = await proxyFetch("/collections", { method: "POST", body: {} });
    if (![401, 403].includes(res.status)) {
      skip = `auth enforcement is OFF (anonymous POST /collections → ${res.status}, expected 401/403) — restart with: docker compose -f docker-compose.yml -f infra/compose.auth-enforced.yml up -d --wait`;
    }
  }
}

if (skip) {
  // One visible, unmissable line explaining why nothing ran.
  console.warn(`\n[proxy-enforcement] SKIPPED: ${skip}\n`);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test("anonymous POST /collections is rejected", { skip }, async () => {
  const res = await proxyFetch("/collections", {
    method: "POST",
    body: collectionBody("itest-anon-write"),
  });
  assert.ok(
    [401, 403].includes(res.status),
    `expected 401/403, got ${res.status}: ${await res.text()}`,
  );
});

test("anonymous GET /collections still works (reads stay public)", { skip }, async () => {
  const res = await proxyFetch("/collections");
  assert.equal(res.status, 200, `expected 200, got ${res.status}`);
  const json = await res.json();
  assert.ok(Array.isArray(json.collections), "response has a collections array");
});

test("authenticated operator (alice) can POST, PUT, and DELETE a collection", { skip }, async (t) => {
  const id = `itest-proxy-alice-${Date.now()}`;
  t.after(() => deleteCollection(id));
  const token = await userToken(ALICE);

  const created = await proxyFetch("/collections", {
    method: "POST",
    token,
    body: collectionBody(id),
  });
  assert.ok(
    [200, 201].includes(created.status),
    `POST expected 200/201, got ${created.status}: ${await created.text()}`,
  );

  const updated = await proxyFetch(`/collections/${id}`, {
    method: "PUT",
    token,
    body: collectionBody(id, "updated by integration test"),
  });
  assert.ok(
    [200, 201, 204].includes(updated.status),
    `PUT expected 2xx, got ${updated.status}: ${await updated.text()}`,
  );

  const fetched = await proxyFetch(`/collections/${id}`);
  assert.equal(fetched.status, 200, `GET after PUT expected 200, got ${fetched.status}`);
  assert.equal((await fetched.json()).description, "updated by integration test");

  const deleted = await proxyFetch(`/collections/${id}`, { method: "DELETE", token });
  assert.ok(
    [200, 204].includes(deleted.status),
    `DELETE expected 200/204, got ${deleted.status}: ${await deleted.text()}`,
  );

  const gone = await proxyFetch(`/collections/${id}`);
  assert.equal(gone.status, 404, `GET after DELETE expected 404, got ${gone.status}`);
});

test("member (bob) can also write — role gating is NOT yet enforced (ADR 0002 known limitation)", { skip }, async (t) => {
  // Route-level protection only checks for a valid JWT; Keycloak realm roles
  // (member vs operator) are not expressible as scope requirements via
  // config alone. This test pins the CURRENT behavior so the suite starts
  // failing loudly the day role gating lands and this expectation flips.
  const id = `itest-proxy-bob-${Date.now()}`;
  t.after(() => deleteCollection(id));
  const token = await userToken(BOB);

  const created = await proxyFetch("/collections", {
    method: "POST",
    token,
    body: collectionBody(id),
  });
  assert.ok(
    [200, 201].includes(created.status),
    `POST expected 200/201 (documented limitation), got ${created.status}: ${await created.text()}`,
  );
});

test("token from another realm (wrong issuer/signature) is rejected", { skip }, async () => {
  // Keycloak's master realm signs with a different key and issuer than the
  // stac-higher realm the proxy trusts.
  const token = await getToken("master", {
    grant_type: "password",
    client_id: "admin-cli",
    username: "admin",
    password: "admin",
  });
  const res = await proxyFetch("/collections", {
    method: "POST",
    token,
    body: collectionBody("itest-wrong-realm"),
  });
  assert.ok(
    [401, 403].includes(res.status),
    `expected 401/403 for wrong-realm token, got ${res.status}: ${await res.text()}`,
  );
});

test("validly-signed token without the stac-higher audience is rejected", { skip }, async (t) => {
  // Same realm, same user, but minted via the client that has no
  // oidc-audience-mapper — so aud lacks "stac-higher" and must fail
  // ALLOWED_JWT_AUDIENCES (set by infra/compose.auth-enforced.yml).
  const id = "itest-wrong-aud";
  t.after(() => deleteCollection(id)); // defensive: only exists if the assertion fails
  const token = await userToken(ALICE, NOAUD_CLIENT);
  const res = await proxyFetch("/collections", {
    method: "POST",
    token,
    body: collectionBody(id),
  });
  assert.ok(
    [401, 403].includes(res.status),
    `expected 401/403 for wrong-audience token, got ${res.status}: ${await res.text()}`,
  );
});
