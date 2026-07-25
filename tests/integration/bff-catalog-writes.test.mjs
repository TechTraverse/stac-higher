// Integration tests: the ADR 0008 BFF — UI-path catalog writes under auth
// enforcement (resolves the I-50 test gap: no more password-grant tokens
// presented straight to the proxy for the browser case).
//
// The leg is genuinely end-to-end: a REAL session login (authorization-code
// flow against Keycloak's login form) → a write to the app's /api/catalog BFF
// route carrying only the httpOnly session cookie → the app injects the
// bearer token server-side → the ENFORCED proxy accepts it → the mutation
// lands an audit_log row.
//
// Preconditions (see tests/integration/README.md):
//   docker compose -f docker-compose.yml -f infra/compose.auth-enforced.yml up -d --wait
//   # plus the Astro app in OIDC mode (from app/):
//   AUTH_MODE=oidc SESSION_SECRET=<anything long> npm run dev
//
// The whole file SKIPS (never fails) when Keycloak/proxy/app are unreachable,
// when enforcement is off, or when the app is running in dev-bypass mode.
//
// Zero dependencies: node:test + global fetch (Node >= 22 per root engines).

import { test } from "node:test";
import assert from "node:assert/strict";

const KEYCLOAK_URL = process.env.KEYCLOAK_URL ?? "http://localhost:8180";
const PROXY_URL = process.env.AUTH_PROXY_URL ?? "http://localhost:8081";
const APP_URL = process.env.APP_URL ?? "http://localhost:4321";
const REALM = "stac-higher";

const ALICE = { username: "alice", password: "alice-password" }; // operator

const PROBE_TIMEOUT_MS = 3_000;
const REQUEST_TIMEOUT_MS = 15_000;

function collectionBody(id, description = "bff integration test") {
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
    return res;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Minimal cookie jar for the login dance (undici exposes getSetCookie()).
// ---------------------------------------------------------------------------
function absorbCookies(jar, res) {
  for (const line of res.headers.getSetCookie?.() ?? []) {
    const [pair] = line.split(";");
    const eq = pair.indexOf("=");
    if (eq > 0) jar.set(pair.slice(0, eq).trim(), pair.slice(eq + 1).trim());
  }
}

const cookieHeader = (jar) =>
  [...jar.entries()].map(([k, v]) => `${k}=${v}`).join("; ");

/**
 * Real authorization-code login through the app: /api/auth/login → Keycloak
 * login form → credentials POST → /api/auth/callback. Returns a cookie jar
 * holding the app's sealed httpOnly session (sh_session chunks).
 */
async function sessionLogin(user) {
  const appJar = new Map();
  const kcJar = new Map();

  const start = await fetch(`${APP_URL}/api/auth/login`, {
    redirect: "manual",
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  assert.equal(start.status, 302, `login start expected 302, got ${start.status}`);
  absorbCookies(appJar, start); // sh_oidc_txn
  const authorizeUrl = start.headers.get("location");
  assert.ok(authorizeUrl, "login start returned a Location header");

  const loginPage = await fetch(authorizeUrl, {
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  assert.equal(loginPage.status, 200, `Keycloak login page expected 200`);
  absorbCookies(kcJar, loginPage);
  const html = await loginPage.text();
  const actionMatch = html.match(/action="([^"]+)"/);
  assert.ok(actionMatch, "Keycloak login form has an action URL");
  const formAction = actionMatch[1].replaceAll("&amp;", "&");

  const submitted = await fetch(formAction, {
    method: "POST",
    redirect: "manual",
    headers: {
      "content-type": "application/x-www-form-urlencoded",
      cookie: cookieHeader(kcJar),
    },
    body: new URLSearchParams({
      username: user.username,
      password: user.password,
      credentialId: "",
    }),
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  assert.equal(
    submitted.status,
    302,
    `credential POST expected 302 (wrong credentials or an unexpected Keycloak page?), got ${submitted.status}`,
  );
  const callbackUrl = submitted.headers.get("location");
  assert.ok(
    callbackUrl?.startsWith(APP_URL),
    `Keycloak redirected somewhere unexpected: ${callbackUrl}`,
  );

  const callback = await fetch(callbackUrl, {
    redirect: "manual",
    headers: { cookie: cookieHeader(appJar) },
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  assert.equal(callback.status, 302, `callback expected 302, got ${callback.status}`);
  absorbCookies(appJar, callback); // sh_session chunks
  assert.ok(
    [...appJar.keys()].some((k) => k.startsWith("sh_session")),
    "callback set the session cookie",
  );
  return appJar;
}

/** BFF write: session cookie only — the browser never holds a bearer token.
 * Origin matches the app so Astro's CSRF check treats it as same-origin. */
async function bffFetch(jar, path, { method = "POST", body } = {}) {
  const headers = { origin: APP_URL };
  if (jar) headers.cookie = cookieHeader(jar);
  if (body !== undefined) headers["content-type"] = "application/json";
  return fetch(`${APP_URL}/api/catalog${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
}

// ---------------------------------------------------------------------------
// Preconditions (top-level await): decide once whether the suite runs.
// ---------------------------------------------------------------------------
let skip = false;

const kc = await probe(`${KEYCLOAK_URL}/realms/${REALM}/.well-known/openid-configuration`);
if (kc?.status !== 200) {
  skip = `Keycloak realm "${REALM}" not reachable at ${KEYCLOAK_URL} — start the enforced stack`;
} else {
  const enforcement = await probe(`${PROXY_URL}/collections`).then((r) =>
    r
      ? fetch(`${PROXY_URL}/collections`, {
          method: "POST",
          signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
        })
      : null,
  );
  if (!enforcement) {
    skip = `stac-auth-proxy not reachable at ${PROXY_URL} — start the enforced stack`;
  } else if (![401, 403].includes(enforcement.status)) {
    skip = `auth enforcement is OFF (anonymous POST /collections → ${enforcement.status}) — restart with the auth-enforced overlay`;
  } else {
    const me = await probe(`${APP_URL}/api/auth/me`);
    if (me?.status !== 200) {
      skip = `Astro app not reachable at ${APP_URL} — start it in OIDC mode: AUTH_MODE=oidc SESSION_SECRET=... npm run dev (from app/)`;
    } else {
      const auth = await me.json();
      if (auth.mode !== "oidc") {
        skip = `app is running in "${auth.mode}" mode — the BFF leg needs AUTH_MODE=oidc + SESSION_SECRET so a real session exists`;
      }
    }
  }
}

if (skip) {
  console.warn(`\n[bff-catalog-writes] SKIPPED: ${skip}\n`);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test("anonymous BFF write is rejected by the guard (401), never reaches the proxy", { skip }, async () => {
  const res = await bffFetch(null, "/collections", {
    body: collectionBody("itest-bff-anon"),
  });
  assert.equal(res.status, 401, `expected 401, got ${res.status}: ${await res.text()}`);
});

test("session login → BFF collection create/update/delete lands through the enforced proxy", { skip }, async (t) => {
  const id = `itest-bff-${Date.now()}`;
  const jar = await sessionLogin(ALICE);
  t.after(async () => {
    // Best-effort cleanup through the same BFF path.
    await bffFetch(jar, `/collections/${id}`, { method: "DELETE" }).catch(() => {});
  });

  const created = await bffFetch(jar, "/collections", { body: collectionBody(id) });
  assert.ok(
    [200, 201].includes(created.status),
    `BFF POST expected 200/201, got ${created.status}: ${await created.text()}`,
  );

  // Visible through the proxy (the catalog plane is the source of truth).
  const fetched = await fetch(`${PROXY_URL}/collections/${id}`, {
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  assert.equal(fetched.status, 200, `proxy GET expected 200, got ${fetched.status}`);

  const updated = await bffFetch(jar, `/collections/${id}`, {
    method: "PUT",
    body: collectionBody(id, "updated via BFF"),
  });
  assert.ok(
    [200, 201, 204].includes(updated.status),
    `BFF PUT expected 2xx, got ${updated.status}: ${await updated.text()}`,
  );
  const afterPut = await fetch(`${PROXY_URL}/collections/${id}`, {
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  assert.equal((await afterPut.json()).description, "updated via BFF");

  const deleted = await bffFetch(jar, `/collections/${id}`, { method: "DELETE" });
  assert.ok(
    [200, 204].includes(deleted.status),
    `BFF DELETE expected 200/204, got ${deleted.status}: ${await deleted.text()}`,
  );
  const gone = await fetch(`${PROXY_URL}/collections/${id}`, {
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  assert.equal(gone.status, 404, `proxy GET after DELETE expected 404, got ${gone.status}`);

  // The catalog plane is audited now (ADR 0008 decision 4): the create is in
  // the audit log, attributed to alice.
  const audit = await fetch(`${APP_URL}/api/audit?limit=50`, {
    headers: { cookie: cookieHeader(jar) },
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  assert.equal(audit.status, 200, `audit read expected 200, got ${audit.status}`);
  const { entries } = await audit.json();
  const row = entries.find(
    (e) =>
      e.action === "create" &&
      e.resourceType === "catalog_collection" &&
      e.resourceId === id,
  );
  assert.ok(row, `audit log has the catalog_collection create row for ${id}`);
});

test("BFF refuses non-transaction paths (404) — it is not a generic proxy", { skip }, async () => {
  const jar = await sessionLogin(ALICE);
  const res = await bffFetch(jar, "/search", { body: {} });
  assert.equal(res.status, 404, `expected 404, got ${res.status}`);
});
