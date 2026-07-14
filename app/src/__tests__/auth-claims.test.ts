import { describe, it, expect, vi, afterEach } from "vitest";
import {
  DEFAULT_CLAIMS_MAPPING,
  ClaimsMappingError,
  getClaimAtPath,
  loadClaimsMapping,
  mapClaims,
  type ClaimsMappingConfig,
} from "@/lib/auth/claims";

describe("getClaimAtPath", () => {
  it("resolves nested dot paths", () => {
    expect(
      getClaimAtPath({ realm_access: { roles: ["operator"] } }, "realm_access.roles"),
    ).toEqual(["operator"]);
  });

  it("resolves paths whose segment contains special characters", () => {
    expect(getClaimAtPath({ "cognito:groups": ["eo"] }, "cognito:groups")).toEqual(["eo"]);
  });

  it("returns undefined for missing paths", () => {
    expect(getClaimAtPath({ a: 1 }, "a.b.c")).toBeUndefined();
    expect(getClaimAtPath(null, "a")).toBeUndefined();
  });
});

describe("mapClaims — Keycloak default mapping", () => {
  const keycloakPayload = {
    sub: "f3a1-uuid",
    email: "ada@example.com",
    name: "Ada Lovelace",
    preferred_username: "ada",
    groups: ["/earth-observation", "weather"],
    realm_access: {
      roles: ["operator", "member", "offline_access", "uma_authorization", "default-roles-stac-higher"],
    },
  };

  it("maps a Keycloak access-token payload to the canonical identity", () => {
    const identity = mapClaims(keycloakPayload);
    expect(identity).toEqual({
      sub: "f3a1-uuid",
      email: "ada@example.com",
      name: "Ada Lovelace",
      groups: ["earth-observation", "weather"],
      roles: ["operator", "member"],
    });
  });

  it("drops Keycloak default roles that are not canonical", () => {
    const identity = mapClaims(keycloakPayload);
    expect(identity.roles).not.toContain("offline_access");
    expect(identity.roles).not.toContain("default-roles-stac-higher");
  });

  it("falls back through name paths (preferred_username, email)", () => {
    const identity = mapClaims({
      sub: "x",
      preferred_username: "ada",
      groups: [],
      realm_access: { roles: [] },
    });
    expect(identity.name).toBe("ada");
  });

  it("throws when no subject is present", () => {
    expect(() => mapClaims({ email: "no-sub@example.com" })).toThrow(
      ClaimsMappingError,
    );
  });

  it("yields empty groups/roles when claims are absent", () => {
    const identity = mapClaims({ sub: "x" });
    expect(identity.groups).toEqual([]);
    expect(identity.roles).toEqual([]);
  });
});

describe("mapClaims — Cognito-style tokens", () => {
  const cognitoConfig: ClaimsMappingConfig = {
    groups: "cognito:groups",
    roles: "cognito:groups",
    roleMap: { "stac-operators": "operator", "stac-admins": "admin" },
  };

  it("maps cognito:groups into groups and roles via roleMap", () => {
    const identity = mapClaims(
      {
        sub: "us-east-1:abc",
        email: "cog@example.com",
        "cognito:groups": ["earth-observation", "stac-operators"],
      },
      cognitoConfig,
    );
    expect(identity.groups).toEqual(["earth-observation", "stac-operators"]);
    expect(identity.roles).toEqual(["operator"]);
  });
});

describe("mapClaims — Entra-style tokens", () => {
  const entraConfig: ClaimsMappingConfig = {
    sub: "oid",
    email: "preferred_username",
    groups: "groups",
    roles: "roles",
    groupMap: { "9f1c-guid-eo": "earth-observation" },
    roleMap: { Operator: "operator", Reader: "member" },
  };

  it("maps oid, group GUIDs and app roles", () => {
    const identity = mapClaims(
      {
        oid: "entra-object-id",
        sub: "pairwise-sub-ignored",
        preferred_username: "ada@contoso.com",
        name: "Ada L",
        groups: ["9f1c-guid-eo", "unmapped-guid"],
        roles: ["Operator", "Reader", "SomethingElse"],
      },
      entraConfig,
    );
    expect(identity.sub).toBe("entra-object-id");
    expect(identity.email).toBe("ada@contoso.com");
    expect(identity.groups).toEqual(["earth-observation", "unmapped-guid"]);
    expect(identity.roles).toEqual(["operator", "member"]);
  });
});

describe("mapClaims — hygiene", () => {
  it("deduplicates groups and roles", () => {
    const identity = mapClaims({
      sub: "x",
      groups: ["/eo", "eo", "eo"],
      realm_access: { roles: ["member", "member"] },
    });
    expect(identity.groups).toEqual(["eo"]);
    expect(identity.roles).toEqual(["member"]);
  });

  it("ignores non-string entries in group/role arrays", () => {
    const identity = mapClaims({
      sub: "x",
      groups: ["eo", 42, null, { nope: true }],
      realm_access: { roles: [1, "operator"] },
    });
    expect(identity.groups).toEqual(["eo"]);
    expect(identity.roles).toEqual(["operator"]);
  });

  it("accepts a single-string groups claim", () => {
    const identity = mapClaims({ sub: "x", groups: "earth-observation" });
    expect(identity.groups).toEqual(["earth-observation"]);
  });
});

describe("loadClaimsMapping", () => {
  afterEach(() => vi.restoreAllMocks());

  it("returns the Keycloak default with no env", () => {
    expect(loadClaimsMapping({})).toEqual(DEFAULT_CLAIMS_MAPPING);
  });

  it("parses AUTH_CLAIMS_MAPPING inline JSON", () => {
    const config = loadClaimsMapping({
      AUTH_CLAIMS_MAPPING: JSON.stringify({
        groups: "cognito:groups",
        roleMap: { ops: "operator" },
      }),
    });
    expect(config.groups).toBe("cognito:groups");
    expect(config.roleMap).toEqual({ ops: "operator" });
  });

  it("falls back to the default on invalid JSON with a warning", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const config = loadClaimsMapping({ AUTH_CLAIMS_MAPPING: "{not json" });
    expect(config).toEqual(DEFAULT_CLAIMS_MAPPING);
    expect(warn).toHaveBeenCalled();
  });

  it("falls back to the default on schema-invalid config", () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const config = loadClaimsMapping({
      AUTH_CLAIMS_MAPPING: JSON.stringify({ groups: 42 }),
    });
    expect(config).toEqual(DEFAULT_CLAIMS_MAPPING);
  });
});
