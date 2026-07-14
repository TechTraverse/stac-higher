// @vitest-environment node
// (server-side code — jose's WebCrypto checks fail across jsdom realms)
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  DEFAULT_BYPASS_IDENTITY,
  DEFAULT_OIDC_CLIENT_ID,
  DEFAULT_OIDC_ISSUER,
  getAuthConfig,
  getBypassIdentity,
  resolveAuthMode,
} from "@/lib/auth/config";
import { resolveAuthContext } from "@/lib/auth/resolve";
import type { CookieJar } from "@/lib/auth/session";

beforeEach(() => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => vi.restoreAllMocks());

describe("resolveAuthMode", () => {
  it("defaults to bypass in dev with no OIDC env", () => {
    expect(resolveAuthMode({}, false)).toBe("bypass");
  });

  it("selects oidc when any OIDC env is present", () => {
    expect(resolveAuthMode({ OIDC_ISSUER: "http://idp" }, false)).toBe("oidc");
    expect(resolveAuthMode({ SESSION_SECRET: "s" }, false)).toBe("oidc");
    expect(resolveAuthMode({ OIDC_CLIENT_ID: "c" }, false)).toBe("oidc");
  });

  it("honors an explicit AUTH_MODE in dev", () => {
    expect(resolveAuthMode({ AUTH_MODE: "bypass", OIDC_ISSUER: "http://idp" }, false)).toBe("bypass");
    expect(resolveAuthMode({ AUTH_MODE: "oidc" }, false)).toBe("oidc");
  });

  it("defaults to oidc in production", () => {
    expect(resolveAuthMode({}, true)).toBe("oidc");
  });

  it("refuses bypass in production without the force flag", () => {
    expect(resolveAuthMode({ AUTH_MODE: "bypass" }, true)).toBe("oidc");
  });

  it("allows bypass in production only with AUTH_BYPASS_FORCE=true", () => {
    expect(
      resolveAuthMode({ AUTH_MODE: "bypass", AUTH_BYPASS_FORCE: "true" }, true),
    ).toBe("bypass");
  });
});

describe("getAuthConfig", () => {
  it("ships Keycloak defaults", () => {
    const cfg = getAuthConfig({}, false);
    expect(cfg.issuer).toBe(DEFAULT_OIDC_ISSUER);
    expect(cfg.clientId).toBe(DEFAULT_OIDC_CLIENT_ID);
    expect(cfg.redirectUri).toBe("http://localhost:4321/api/auth/callback");
    expect(cfg.internalIssuer).toBe(cfg.issuer);
    expect(cfg.sessionSecret).toBeNull();
  });

  it("strips trailing slashes and honors the internal issuer", () => {
    const cfg = getAuthConfig(
      {
        OIDC_ISSUER: "http://localhost:8180/realms/stac-higher/",
        OIDC_ISSUER_INTERNAL: "http://keycloak:8080/realms/stac-higher/",
      },
      false,
    );
    expect(cfg.issuer).toBe("http://localhost:8180/realms/stac-higher");
    expect(cfg.internalIssuer).toBe("http://keycloak:8080/realms/stac-higher");
  });
});

describe("getBypassIdentity", () => {
  it("defaults to an operator in earth-observation", () => {
    expect(getBypassIdentity({})).toEqual(DEFAULT_BYPASS_IDENTITY);
    expect(DEFAULT_BYPASS_IDENTITY.roles).toEqual(["operator"]);
    expect(DEFAULT_BYPASS_IDENTITY.groups).toEqual(["earth-observation"]);
  });

  it("merges DEV_AUTH_IDENTITY over the default", () => {
    const identity = getBypassIdentity({
      DEV_AUTH_IDENTITY: JSON.stringify({
        name: "Weather Admin",
        groups: ["weather"],
        roles: ["admin"],
      }),
    });
    expect(identity.sub).toBe(DEFAULT_BYPASS_IDENTITY.sub);
    expect(identity.name).toBe("Weather Admin");
    expect(identity.groups).toEqual(["weather"]);
    expect(identity.roles).toEqual(["admin"]);
  });

  it("drops non-canonical roles", () => {
    const identity = getBypassIdentity({
      DEV_AUTH_IDENTITY: JSON.stringify({ roles: ["superuser", "member"] }),
    });
    expect(identity.roles).toEqual(["member"]);
  });

  it("falls back to the default on invalid JSON", () => {
    expect(getBypassIdentity({ DEV_AUTH_IDENTITY: "{oops" })).toEqual(
      DEFAULT_BYPASS_IDENTITY,
    );
  });
});

describe("resolveAuthContext — bypass mode", () => {
  const emptyJar: CookieJar = {
    get: () => undefined,
    set: () => {},
    delete: () => {},
  };

  it("resolves the static dev identity with no cookies and no IdP", async () => {
    const auth = await resolveAuthContext(emptyJar, {});
    expect(auth.authenticated).toBe(true);
    expect(auth.mode).toBe("bypass");
    expect(auth.identity).toEqual(DEFAULT_BYPASS_IDENTITY);
  });

  it("uses DEV_AUTH_IDENTITY when provided", async () => {
    const auth = await resolveAuthContext(emptyJar, {
      DEV_AUTH_IDENTITY: JSON.stringify({ sub: "alice", roles: ["member"] }),
    });
    expect(auth.identity?.sub).toBe("alice");
    expect(auth.identity?.roles).toEqual(["member"]);
  });

  it("is anonymous in oidc mode with no session cookie", async () => {
    const auth = await resolveAuthContext(emptyJar, {
      AUTH_MODE: "oidc",
      SESSION_SECRET: "secret",
    });
    expect(auth.authenticated).toBe(false);
    expect(auth.mode).toBe("oidc");
    expect(auth.identity).toBeNull();
  });
});
