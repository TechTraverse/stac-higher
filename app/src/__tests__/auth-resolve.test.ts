// @vitest-environment node
// (server-side code — jose's WebCrypto checks fail across jsdom realms)
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { resolveAuthContext } from "@/lib/auth/resolve";
import { readSession, writeSession, type CookieJar } from "@/lib/auth/session";

const SECRET = "resolve-test-secret";
const ENV = { AUTH_MODE: "oidc", SESSION_SECRET: SECRET };
const CFG = {
  sessionSecret: SECRET,
  sessionMaxAgeS: 3600,
  redirectUri: "http://localhost:4321/api/auth/callback",
};

function makeJar(): CookieJar & { store: Map<string, string> } {
  const store = new Map<string, string>();
  return {
    store,
    get: (name) => {
      const value = store.get(name);
      return value === undefined ? undefined : { value };
    },
    set: (name, value) => void store.set(name, value),
    delete: (name) => void store.delete(name),
  };
}

/** Build an unsigned JWT-shaped token — resolve decodes, it does not verify
 * (the sealed cookie is the integrity boundary; see resolve.ts). */
function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: object) =>
    Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "RS256", typ: "JWT" })}.${b64(payload)}.sig`;
}

beforeEach(() => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("resolveAuthContext — oidc session", () => {
  it("derives the canonical identity from a live session", async () => {
    const jar = makeJar();
    const exp = Math.floor(Date.now() / 1000) + 600;
    await writeSession(
      jar,
      {
        accessToken: fakeJwt({
          sub: "user-1",
          email: "u1@example.com",
          name: "User One",
          groups: ["/earth-observation"],
          realm_access: { roles: ["member", "offline_access"] },
          exp,
        }),
        refreshToken: "rt",
        idToken: null,
        expiresAt: exp,
      },
      CFG,
    );

    const auth = await resolveAuthContext(jar, ENV);
    expect(auth).toEqual({
      authenticated: true,
      mode: "oidc",
      identity: {
        sub: "user-1",
        email: "u1@example.com",
        name: "User One",
        groups: ["earth-observation"],
        roles: ["member"],
      },
    });
  });

  it("refreshes a near-expiry session and re-seals the cookie", async () => {
    const jar = makeJar();
    const now = Math.floor(Date.now() / 1000);
    const newExp = now + 300;
    const newAccessToken = fakeJwt({
      sub: "user-1",
      groups: ["weather"],
      realm_access: { roles: ["operator"] },
      exp: newExp,
    });

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("openid-configuration")) {
          return new Response(
            JSON.stringify({
              authorization_endpoint: "http://idp/authorize",
              token_endpoint: "http://idp/token",
              end_session_endpoint: "http://idp/logout",
              jwks_uri: "http://idp/jwks",
            }),
            { headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.includes("/token")) {
          return new Response(
            JSON.stringify({
              access_token: newAccessToken,
              refresh_token: "rt-2",
              expires_in: 300,
            }),
            { headers: { "Content-Type": "application/json" } },
          );
        }
        throw new Error(`Unexpected fetch: ${url}`);
      }),
    );

    await writeSession(
      jar,
      {
        accessToken: fakeJwt({ sub: "user-1", exp: now + 10 }),
        refreshToken: "rt-1",
        idToken: null,
        expiresAt: now + 10, // inside the 60 s refresh window
      },
      CFG,
    );

    const auth = await resolveAuthContext(jar, {
      ...ENV,
      // unique issuer so the module-level discovery cache cannot collide
      OIDC_ISSUER: "http://idp/realms/refresh-test",
    });
    expect(auth.authenticated).toBe(true);
    expect(auth.identity?.groups).toEqual(["weather"]);
    expect(auth.identity?.roles).toEqual(["operator"]);

    const updated = await readSession(jar, SECRET);
    expect(updated?.accessToken).toBe(newAccessToken);
    expect(updated?.refreshToken).toBe("rt-2");
    expect(updated?.expiresAt).toBe(newExp);
  });

  it("clears the session and degrades to anonymous when refresh fails", async () => {
    const jar = makeJar();
    const now = Math.floor(Date.now() / 1000);

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 500 })),
    );

    await writeSession(
      jar,
      {
        accessToken: fakeJwt({ sub: "user-1", exp: now - 5 }),
        refreshToken: "rt-dead",
        idToken: null,
        expiresAt: now - 5,
      },
      CFG,
    );

    const auth = await resolveAuthContext(jar, {
      ...ENV,
      OIDC_ISSUER: "http://idp/realms/refresh-fail-test",
    });
    expect(auth.authenticated).toBe(false);
    expect(auth.identity).toBeNull();
    expect(jar.store.size).toBe(0);
  });

  it("treats an expired session without a refresh token as anonymous", async () => {
    const jar = makeJar();
    const now = Math.floor(Date.now() / 1000);
    await writeSession(
      jar,
      {
        accessToken: fakeJwt({ sub: "user-1", exp: now - 5 }),
        refreshToken: null,
        idToken: null,
        expiresAt: now - 5,
      },
      CFG,
    );
    const auth = await resolveAuthContext(jar, ENV);
    expect(auth.authenticated).toBe(false);
    expect(jar.store.size).toBe(0);
  });
});
