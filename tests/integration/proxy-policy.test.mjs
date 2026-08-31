// Integration tests: ADR 0015 proxy write policy (Phase 7, P7-G).
//
// Exercises the services/proxy-policy items-filter factory through the
// ENFORCED stack: reads stay unchanged (including POST /search), direct item
// writes require operator/admin + an externally-writable collection,
// bulk_items is denied without the BFF header, and the X-BFF-Auth shared
// secret exempts app-mediated writes.
//
// Preconditions (see tests/integration/README.md):
//   docker compose down -v          # once, so the realm re-imports test users
//   export CATALOG_BFF_SHARED_SECRET=<secret>   # mandatory for the overlay
//   docker compose -f docker-compose.yml -f infra/compose.auth-enforced.yml up -d --build --wait
//
// Run (repo root):
//   npm run test:integration
//
// The whole file SKIPS (never fails) when Keycloak (:8180) or the proxy
// (:8081) is unreachable, when enforcement is off (base pass-through stack),
// or when the write POLICY is not active (enforced stack running the plain
// upstream image — pre-P7-G). Individual legs additionally skip when their
// own precondition is missing (psql access for flagging a collection; the
// CATALOG_BFF_SHARED_SECRET env for the BFF-exemption legs).
//
// Flagging a collection externally_writable writes stac_higher.
// collection_settings directly via `docker compose exec database psql` —
// the settings API lives in the Astro app, which this suite deliberately
// does not require (bff-catalog-writes.test.mjs covers the app).
//
// NOTE: direct-push item bodies MUST carry "collection" — the policy filter
// references that property and upstream cql2 raises (500-shaped, not 403)
// when it is absent (pinned in services/proxy-policy unit tests).
//
// Zero npm dependencies: node:test + global fetch (Node >= 22 per root engines).

import { execFileSync } from "node:child_process";
import { after, test } from "node:test";
import assert from "node:assert/strict";

const KEYCLOAK_URL = process.env.KEYCLOAK_URL ?? "http://localhost:8180";
const PROXY_URL = process.env.AUTH_PROXY_URL ?? "http://localhost:8081";
const REALM = "stac-higher";
const BFF_SECRET = process.env.CATALOG_BFF_SHARED_SECRET ?? "";
const REPO_ROOT = new URL("../..", import.meta.url).pathname;

// Local-dev credentials from infra/keycloak/realm-stac-higher.json.
const TEST_CLIENT = { id: "stac-higher-test", secret: "stac-higher-test-secret" };
const ALICE = { username: "alice", password: "alice-password" }; // operator
const BOB = { username: "bob", password: "bob-password" }; // member

const PROBE_TIMEOUT_MS = 3_000;
const REQUEST_TIMEOUT_MS = 15_000;
// The factory caches the externally-writable set for ~15 s; a freshly flagged
// collection must become writable within one TTL.
const POLICY_TTL_DEADLINE_MS = 25_000;

const FLAGGED = `itest-policy-flagged-${Date.now()}`;
const UNFLAGGED = `itest-policy-unflagged-${Date.now()}`;

function collectionBody(id) {
  return {
    type: "Collection",
    id,
    stac_version: "1.0.0",
    description: "proxy write-policy integration test",
    license: "proprietary",
    extent: {
      spatial: { bbox: [[-180, -90, 180, 90]] },
      temporal: { interval: [[null, null]] },
    },
    links: [],
  };
}

function itemBody(collection, id) {
  return {
    type: "Feature",
    stac_version: "1.0.0",
    id,
    collection, // REQUIRED by the policy filter — see header note
    geometry: { type: "Point", coordinates: [0, 0] },
    bbox: [0, 0, 0, 0],
    properties: { datetime: "2026-01-01T00:00:00Z" },
    links: [],
    assets: {},
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

async function proxyFetch(path, { method = "GET", token, body, headers = {} } = {}) {
  const allHeaders = { ...headers };
  if (token) allHeaders.authorization = `Bearer ${token}`;
  if (body !== undefined) allHeaders["content-type"] = "application/json";
  return fetch(`${PROXY_URL}${path}`, {
    method,
    headers: allHeaders,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
}

/** Resource-owner password grant against Keycloak; returns an access token. */
async function getToken(user) {
  const res = await fetch(`${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "password",
      client_id: TEST_CLIENT.id,
      client_secret: TEST_CLIENT.secret,
      username: user.username,
      password: user.password,
    }),
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  if (!res.ok) {
    throw new Error(`token request failed: ${res.status} ${await res.text()}`);
  }
  return (await res.json()).access_token;
}

/**
 * Run SQL against the compose database service. Used ONLY to flip
 * collection_settings.externally_writable (the settings API lives in the app,
 * which this suite does not require). Throws when docker/psql is unavailable.
 */
function psql(sql) {
  return execFileSync(
    "docker",
    [
      "compose",
      "exec",
      "-T",
      "database",
      "psql",
      "-U",
      "username",
      "-d",
      "postgis",
      "-v",
      "ON_ERROR_STOP=1",
      "-tAc",
      sql,
    ],
    { encoding: "utf8", cwd: REPO_ROOT, stdio: ["ignore", "pipe", "pipe"] },
  ).trim();
}

/** Retry an async op until its result satisfies `ok` or the deadline passes. */
async function retryUntil(op, ok, deadlineMs) {
  const deadline = Date.now() + deadlineMs;
  let last;
  for (;;) {
    last = await op();
    if (ok(last) || Date.now() > deadline) return last;
    await new Promise((resolve) => setTimeout(resolve, 1_000));
  }
}

// ---------------------------------------------------------------------------
// Preconditions (top-level await): decide once whether the suite runs.
// ---------------------------------------------------------------------------
let skip = false;
let psqlSkip = false; // flag-dependent legs only
const bffSkip = BFF_SECRET
  ? false
  : "CATALOG_BFF_SHARED_SECRET is not set in this process — export the same secret the stack was started with";

const kcStatus = await probe(`${KEYCLOAK_URL}/realms/${REALM}/.well-known/openid-configuration`);
if (kcStatus !== 200) {
  skip = `Keycloak realm "${REALM}" not reachable at ${KEYCLOAK_URL} (status: ${kcStatus}) — start the enforced stack (see tests/integration/README.md)`;
} else if ((await probe(`${PROXY_URL}/`)) !== 200) {
  skip = `stac-auth-proxy not reachable at ${PROXY_URL} — start the enforced stack (see tests/integration/README.md)`;
} else {
  const anon = await proxyFetch("/collections", { method: "POST", body: {} });
  if (![401, 403].includes(anon.status)) {
    skip = `auth enforcement is OFF (anonymous POST /collections → ${anon.status}) — restart with the auth-enforced overlay`;
  } else {
    // Policy probe: a MEMBER item write. With the ADR 0015 factory active the
    // constant-false filter rejects it 403 before the upstream is consulted;
    // on a pre-policy enforced stack the valid token passes route protection
    // and the upstream answers 404 (collection does not exist).
    const bobToken = await getToken(BOB);
    const res = await proxyFetch("/collections/itest-policy-probe/items", {
      method: "POST",
      token: bobToken,
      body: itemBody("itest-policy-probe", "probe"),
    });
    if (res.status !== 403) {
      skip = `proxy write policy is NOT active (member item write → ${res.status}, expected 403) — rebuild the enforced stack with the derived image (up -d --build)`;
    }
  }
}

if (!skip) {
  try {
    psql("SELECT 1");
  } catch {
    psqlSkip =
      "cannot reach the database via `docker compose exec database psql` — flag-dependent legs skipped";
  }
}

if (skip) {
  console.warn(`\n[proxy-policy] SKIPPED: ${skip}\n`);
} else if (psqlSkip) {
  console.warn(`\n[proxy-policy] PARTIAL: ${psqlSkip}\n`);
}

// ---------------------------------------------------------------------------
// Shared fixtures: one flagged + one unflagged collection.
// ---------------------------------------------------------------------------
const flagSkip = skip || psqlSkip;
let aliceToken;

if (!skip) {
  aliceToken = await getToken(ALICE);
  for (const id of [FLAGGED, UNFLAGGED]) {
    const created = await proxyFetch("/collections", {
      method: "POST",
      token: aliceToken,
      body: collectionBody(id),
    });
    if (![200, 201].includes(created.status)) {
      throw new Error(`fixture collection ${id} create failed: ${created.status}`);
    }
  }
  if (!psqlSkip) {
    psql(
      `INSERT INTO stac_higher.collection_settings (collection_id, externally_writable)
       VALUES ('${FLAGGED}', true)
       ON CONFLICT (collection_id)
       DO UPDATE SET externally_writable = true, updated_at = now()`,
    );
  }
}

after(async () => {
  if (skip) return;
  // Best-effort cleanup; item deletes ride the BFF header when available so
  // strays in the unflagged collection go too.
  try {
    if (!psqlSkip) {
      psql(`DELETE FROM stac_higher.collection_settings WHERE collection_id IN ('${FLAGGED}')`);
    }
    const token = await getToken(ALICE);
    for (const id of [FLAGGED, UNFLAGGED]) {
      await proxyFetch(`/collections/${id}`, { method: "DELETE", token });
    }
  } catch {
    /* cleanup only */
  }
});

// ---------------------------------------------------------------------------
// Reads: unchanged shape even though an items filter is now configured
// (the proxy appends filter=true to GET items and response-validates
// single-item reads — both must stay invisible to clients).
// ---------------------------------------------------------------------------

test("anonymous GET /collections/{id}/items keeps its shape (filter=true is invisible)", { skip }, async () => {
  const res = await proxyFetch(`/collections/${UNFLAGGED}/items`);
  const text = await res.text(); // read once — a template-literal await would eat the body
  assert.equal(res.status, 200, `expected 200, got ${res.status}: ${text}`);
  const json = JSON.parse(text);
  assert.equal(json.type, "FeatureCollection");
  assert.ok(Array.isArray(json.features), "response has a features array");
});

test("anonymous POST /search still works — the read-served-by-POST carve-out", { skip }, async () => {
  const res = await proxyFetch("/search", {
    method: "POST",
    body: { collections: [UNFLAGGED], limit: 1 },
  });
  const text = await res.text();
  assert.equal(res.status, 200, `expected 200, got ${res.status}: ${text}`);
  const json = JSON.parse(text);
  assert.ok(Array.isArray(json.features), "search response has a features array");
});

// ---------------------------------------------------------------------------
// Role floor + externally_writable gate on direct item writes
// ---------------------------------------------------------------------------

test("member (bob) item write is denied even on a flagged collection", { skip: flagSkip }, async () => {
  const token = await getToken(BOB);
  const res = await proxyFetch(`/collections/${FLAGGED}/items`, {
    method: "POST",
    token,
    body: itemBody(FLAGGED, "itest-bob-item"),
  });
  assert.equal(res.status, 403, `expected 403, got ${res.status}: ${await res.text()}`);
});

test("operator (alice) item write to a NON-externally-writable collection is denied", { skip }, async () => {
  const res = await proxyFetch(`/collections/${UNFLAGGED}/items`, {
    method: "POST",
    token: aliceToken,
    body: itemBody(UNFLAGGED, "itest-alice-unflagged"),
  });
  assert.equal(res.status, 403, `expected 403, got ${res.status}: ${await res.text()}`);
});

test("operator (alice) can create, read, update, and delete an item in a flagged collection", { skip: flagSkip }, async () => {
  const itemId = "itest-alice-flagged";

  // The factory's set cache refreshes within ~15 s of the flag flip.
  const created = await retryUntil(
    () =>
      proxyFetch(`/collections/${FLAGGED}/items`, {
        method: "POST",
        token: aliceToken,
        body: itemBody(FLAGGED, itemId),
      }),
    (res) => [200, 201].includes(res.status),
    POLICY_TTL_DEADLINE_MS,
  );
  assert.ok(
    [200, 201].includes(created.status),
    `POST expected 200/201 within the cache TTL, got ${created.status}: ${await created.text()}`,
  );

  // Single-item read passes the response-validation path.
  const fetched = await proxyFetch(`/collections/${FLAGGED}/items/${itemId}`);
  assert.equal(fetched.status, 200, `GET item expected 200, got ${fetched.status}`);
  assert.equal((await fetched.json()).id, itemId);

  // Listing keeps its shape and contains the item.
  const listed = await proxyFetch(`/collections/${FLAGGED}/items`);
  assert.equal(listed.status, 200);
  const listJson = await listed.json();
  assert.ok(
    listJson.features.some((feature) => feature.id === itemId),
    "created item appears in the collection items listing",
  );

  const updatedBody = itemBody(FLAGGED, itemId);
  updatedBody.properties.datetime = "2026-02-01T00:00:00Z";
  const updated = await proxyFetch(`/collections/${FLAGGED}/items/${itemId}`, {
    method: "PUT",
    token: aliceToken,
    body: updatedBody,
  });
  assert.ok(
    [200, 201, 204].includes(updated.status),
    `PUT expected 2xx, got ${updated.status}: ${await updated.text()}`,
  );

  const deleted = await proxyFetch(`/collections/${FLAGGED}/items/${itemId}`, {
    method: "DELETE",
    token: aliceToken,
  });
  assert.ok(
    [200, 204].includes(deleted.status),
    `DELETE expected 200/204, got ${deleted.status}: ${await deleted.text()}`,
  );
});

// ---------------------------------------------------------------------------
// bulk_items: denied without the BFF header — bulk push is unsupported (P7)
// ---------------------------------------------------------------------------

test("bulk_items without the BFF header is denied even for an operator", { skip: flagSkip }, async (t) => {
  const res = await proxyFetch(`/collections/${FLAGGED}/bulk_items`, {
    method: "POST",
    token: aliceToken,
    body: { items: { "itest-bulk-item": itemBody(FLAGGED, "itest-bulk-item") } },
  });
  // The spec accepts any non-2xx denial here but flags the SHAPE for P7-Z:
  // the constant-false filter should reject 403 via the validate middleware.
  // Record the actual shape loudly so drift is visible.
  t.diagnostic(`bulk_items denial shape: ${res.status} ${(await res.text()).slice(0, 200)}`);
  assert.ok(
    res.status >= 400 && res.status < 500,
    `expected a 4xx denial, got ${res.status}`,
  );

  const gone = await proxyFetch(`/collections/${FLAGGED}/items/itest-bulk-item`);
  assert.equal(gone.status, 404, "denied bulk insert must not create items");
});

// ---------------------------------------------------------------------------
// BFF shared-secret exemption (X-BFF-Auth)
// ---------------------------------------------------------------------------

test("the X-BFF-Auth shared secret exempts writes from the flag and role policy", { skip: skip || bffSkip }, async () => {
  const itemId = "itest-bff-item";
  const token = await getToken(BOB); // member + unflagged collection: everything the policy denies

  const created = await proxyFetch(`/collections/${UNFLAGGED}/items`, {
    method: "POST",
    token,
    body: itemBody(UNFLAGGED, itemId),
    headers: { "x-bff-auth": BFF_SECRET },
  });
  assert.ok(
    [200, 201].includes(created.status),
    `BFF-exempt POST expected 200/201, got ${created.status}: ${await created.text()}`,
  );

  const deleted = await proxyFetch(`/collections/${UNFLAGGED}/items/${itemId}`, {
    method: "DELETE",
    token,
    headers: { "x-bff-auth": BFF_SECRET },
  });
  assert.ok(
    [200, 204].includes(deleted.status),
    `BFF-exempt DELETE expected 200/204, got ${deleted.status}: ${await deleted.text()}`,
  );
});

test("a WRONG X-BFF-Auth value falls through to the normal policy (denied)", { skip }, async () => {
  const token = await getToken(BOB);
  const res = await proxyFetch(`/collections/${UNFLAGGED}/items`, {
    method: "POST",
    token,
    body: itemBody(UNFLAGGED, "itest-bff-wrong"),
    headers: { "x-bff-auth": "definitely-not-the-secret" },
  });
  assert.equal(res.status, 403, `expected 403, got ${res.status}: ${await res.text()}`);
});
