// @vitest-environment node
// (server-side code — jose's WebCrypto checks fail across jsdom realms)
import {
  describe,
  it,
  expect,
  vi,
  beforeAll,
  beforeEach,
  afterEach,
} from "vitest";
import { SignJWT, exportJWK, generateKeyPair, type JWK } from "jose";
import {
  extractBearerToken,
  getBearerAudiences,
  resolveRequestAuth,
} from "@/lib/auth/bearer";
import { writeSession, type CookieJar } from "@/lib/auth/session";

// --- IdP fixture --------------------------------------------------------

let privateKey: CryptoKey;
let publicJwk: JWK;

beforeAll(async () => {
  const pair = await generateKeyPair("RS256", { extractable: true });
  privateKey = pair.privateKey;
  publicJwk = { ...(await exportJWK(pair.publicKey)), kid: "test-key", alg: "RS256", use: "sig" };
});

/** Stub global fetch to serve OIDC discovery + JWKS for `issuer`. Discovery
 * and JWKS caches are module-level, so each test uses a UNIQUE issuer. */
function stubIdp(issuer: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith(`${issuer}/.well-known/openid-configuration`)) {
        return new Response(
          JSON.stringify({
            authorization_endpoint: `${issuer}/authorize`,
            token_endpoint: `${issuer}/token`,
            jwks_uri: `${issuer}/jwks`,
          }),
          { headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.startsWith(`${issuer}/jwks`)) {
        return new Response(JSON.stringify({ keys: [publicJwk] }), {
          headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    }),
  );
}

interface TokenOpts {
  issuer: string;
  audience?: string;
  /** Unix seconds; defaults to 5 minutes from now. */
  exp?: number;
  payload?: Record<string, unknown>;
}

/** Sign a real RS256 JWT with the fixture key — the operator service-account
 * shape the dev `stac-higher-push` client mints. */
async function signBearer(opts: TokenOpts): Promise<string> {
  const jwt = new SignJWT({
    groups: ["/earth-observation"],
    realm_access: { roles: ["operator", "uma_authorization"] },
    azp: "stac-higher-push",
    ...opts.payload,
  })
    .setProtectedHeader({ alg: "RS256", kid: "test-key" })
    .setSubject("svc-push-1")
    .setIssuer(opts.issuer)
    .setAudience(opts.audience ?? "stac-higher")
    .setIssuedAt()
    .setExpirationTime(opts.exp ?? Math.floor(Date.now() / 1000) + 300);
  return jwt.sign(privateKey);
}

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

/** Unsigned JWT-shaped token for the SESSION path — resolve decodes, it does
 * not verify (the sealed cookie is the integrity boundary; see resolve.ts). */
function fakeSessionJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: object) =>
    Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "RS256", typ: "JWT" })}.${b64(payload)}.sig`;
}

function apiRequest(token?: string, path = "/api/uploads"): Request {
  return new Request(`http://localhost:4321${path}`, {
    headers: token ? { authorization: `Bearer ${token}` } : {},
  });
}

beforeEach(() => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// --- helpers ------------------------------------------------------------

describe("extractBearerToken", () => {
  it("extracts the token from a Bearer header (case-insensitive)", () => {
    expect(extractBearerToken(apiRequest("abc.def.ghi"))).toBe("abc.def.ghi");
    const req = new Request("http://localhost:4321/api/x", {
      headers: { authorization: "bearer tok" },
    });
    expect(extractBearerToken(req)).toBe("tok");
  });

  it("returns null for absent or non-Bearer headers", () => {
    expect(extractBearerToken(apiRequest())).toBeNull();
    const basic = new Request("http://localhost:4321/api/x", {
      headers: { authorization: "Basic dXNlcjpwdw==" },
    });
    expect(extractBearerToken(basic)).toBeNull();
  });
});

describe("getBearerAudiences", () => {
  it("defaults to stac-higher", () => {
    expect(getBearerAudiences({})).toEqual(["stac-higher"]);
  });
  it("parses a comma-separated override, ignoring blanks", () => {
    expect(
      getBearerAudiences({ AUTH_BEARER_AUDIENCES: "aud-a, aud-b,, " }),
    ).toEqual(["aud-a", "aud-b"]);
  });
});

// --- resolution (§3) ----------------------------------------------------

describe("resolveRequestAuth — bearer path", () => {
  it("resolves a valid bearer token to a mapped operator identity", async () => {
    const issuer = "http://idp/realms/bearer-valid";
    stubIdp(issuer);
    const token = await signBearer({ issuer });

    const result = await resolveRequestAuth(makeJar(), apiRequest(token), {
      AUTH_MODE: "oidc",
      OIDC_ISSUER: issuer,
    });

    expect(result.auth).toEqual({
      authenticated: true,
      mode: "oidc",
      identity: {
        sub: "svc-push-1",
        email: null,
        name: "stac-higher-push", // azp fallback (no profile claims)
        groups: ["earth-observation"],
        roles: ["operator"],
      },
    });
    expect(result.bearerToken).toBe(token);
  });

  it("degrades a wrong-audience token to anonymous", async () => {
    const issuer = "http://idp/realms/bearer-wrong-aud";
    stubIdp(issuer);
    const token = await signBearer({ issuer, audience: "some-other-service" });

    const result = await resolveRequestAuth(makeJar(), apiRequest(token), {
      AUTH_MODE: "oidc",
      OIDC_ISSUER: issuer,
    });

    expect(result.auth).toEqual({
      authenticated: false,
      mode: "oidc",
      identity: null,
    });
    expect(result.bearerToken).toBeUndefined();
  });

  it("degrades an expired token to anonymous", async () => {
    const issuer = "http://idp/realms/bearer-expired";
    stubIdp(issuer);
    const token = await signBearer({
      issuer,
      exp: Math.floor(Date.now() / 1000) - 300,
    });

    const result = await resolveRequestAuth(makeJar(), apiRequest(token), {
      AUTH_MODE: "oidc",
      OIDC_ISSUER: issuer,
    });

    expect(result.auth.authenticated).toBe(false);
    expect(result.bearerToken).toBeUndefined();
  });

  it("ignores bearer tokens outside /api/*", async () => {
    const issuer = "http://idp/realms/bearer-non-api";
    stubIdp(issuer);
    const token = await signBearer({ issuer });

    const result = await resolveRequestAuth(
      makeJar(),
      apiRequest(token, "/collections"),
      { AUTH_MODE: "oidc", OIDC_ISSUER: issuer },
    );

    expect(result.auth.authenticated).toBe(false);
    expect(result.bearerToken).toBeUndefined();
  });
});

describe("resolveRequestAuth — precedence", () => {
  it("a valid session cookie wins over a valid bearer token", async () => {
    const issuer = "http://idp/realms/bearer-session-wins";
    stubIdp(issuer);
    const token = await signBearer({ issuer });

    const secret = "bearer-precedence-secret";
    const jar = makeJar();
    const exp = Math.floor(Date.now() / 1000) + 600;
    await writeSession(
      jar,
      {
        accessToken: fakeSessionJwt({
          sub: "user-1",
          email: "u1@example.com",
          name: "User One",
          groups: ["/weather"],
          realm_access: { roles: ["member"] },
          exp,
        }),
        refreshToken: "rt",
        idToken: null,
        expiresAt: exp,
      },
      {
        sessionSecret: secret,
        sessionMaxAgeS: 3600,
        redirectUri: "http://localhost:4321/api/auth/callback",
      },
    );

    const result = await resolveRequestAuth(jar, apiRequest(token), {
      AUTH_MODE: "oidc",
      OIDC_ISSUER: issuer,
      SESSION_SECRET: secret,
    });

    // The session identity, not the bearer service account.
    expect(result.auth.authenticated).toBe(true);
    expect(result.auth.identity?.sub).toBe("user-1");
    expect(result.bearerToken).toBeUndefined();
  });

  it("leaves dev-bypass mode untouched (bearer header ignored)", async () => {
    // Any fetch would blow up the test — bypass must never hit the IdP.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("bypass mode must not fetch");
      }),
    );

    const result = await resolveRequestAuth(
      makeJar(),
      apiRequest("not-even-a.real.jwt"),
      { AUTH_MODE: "bypass" },
    );

    expect(result.auth).toEqual({
      authenticated: true,
      mode: "bypass",
      identity: {
        sub: "dev-user",
        email: "dev@stac-higher.local",
        name: "Dev Operator",
        groups: ["earth-observation"],
        roles: ["operator"],
      },
    });
    expect(result.bearerToken).toBeUndefined();
  });

  it("stays anonymous with no session and no bearer header", async () => {
    const result = await resolveRequestAuth(makeJar(), apiRequest(), {
      AUTH_MODE: "oidc",
      OIDC_ISSUER: "http://idp/realms/bearer-none",
    });
    expect(result.auth).toEqual({
      authenticated: false,
      mode: "oidc",
      identity: null,
    });
    expect(result.bearerToken).toBeUndefined();
  });
});
