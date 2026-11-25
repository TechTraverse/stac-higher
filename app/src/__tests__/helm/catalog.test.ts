// @vitest-environment node
// R-7: the catalog plane (stac-api, auth-proxy, titiler, tipg) and the cube-server.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { parse } from "yaml";
import { CHART_DIR, helmReady, renderChart, scenario } from "../../../../infra/helm/stac-higher/tests/render.mjs";

type Doc = any;
const REPO = join(CHART_DIR, "../../..");
function render(values: object = {}, scen = "base") {
  const r = renderChart({ valuesFiles: scenario(scen), values });
  expect(r.ok, r.stderr).toBe(true);
  return r.docs;
}
const cm = (docs: Doc[], component: string) =>
  docs.find((d) => d.kind === "ConfigMap" && d.metadata.name === `stac-higher-env-${component}`)?.data;
const COMPONENTS = ["stac-api", "auth-proxy", "titiler", "tipg", "cube-server"];

const compose = parse(readFileSync(join(REPO, "docker-compose.yml"), "utf8"));
const enforced = parse(readFileSync(join(REPO, "infra/compose.auth-enforced.yml"), "utf8"));
const chartValues = parse(readFileSync(join(CHART_DIR, "values.yaml"), "utf8"));

/** The enforced overlay's auth-proxy env, with compose's `$$` escapes undone. */
function overlayEnv(): Record<string, string> {
  const out: Record<string, string> = {};
  for (const e of enforced.services["auth-proxy"].environment as string[]) {
    const i = e.indexOf("=");
    out[e.slice(0, i)] = e.slice(i + 1).replaceAll("$$", "$");
  }
  return out;
}

describe("catalog image pins (ADR 0015 pair)", () => {
  it("third-party images match the compose pins", () => {
    expect(chartValues.catalog.stacApi.image).toBe(compose.services.api.image);
    expect(chartValues.catalog.tipg.image).toBe(compose.services.tipg.image);
    const dockerfile = readFileSync(join(REPO, "infra/proxy-policy/Dockerfile"), "utf8");
    expect(dockerfile).toContain(`FROM ${compose.services["auth-proxy"].image}`);
  });
});

describe.skipIf(!helmReady())("chart catalog plane", () => {
  it("renders one env ConfigMap per workload, with no credential in it", () => {
    for (const scen of ["base", "existing-secrets"]) {
      const docs = render({}, scen);
      for (const c of COMPONENTS) {
        const data = cm(docs, c);
        expect(data, c).toBeTruthy();
        for (const [k, v] of Object.entries(data)) {
          expect(k, `${c}.${k}`).not.toMatch(/PASSWORD|_PASS$|SECRET|TOKEN|ACCESS_KEY|^PGUSER$|^POSTGRES_USER$/);
          expect(String(v), `${c}.${k}`).not.toMatch(/\/\/[^/@\s]*@/);
        }
      }
    }
  });

  it("the proxy runs the enforced write policy exactly as the compose overlay does", () => {
    const env = cm(render(), "auth-proxy");
    const o = overlayEnv();
    for (const k of ["DEFAULT_PUBLIC", "ITEMS_FILTER_CLS", "ITEMS_FILTER_PATH"]) expect(env[k], k).toBe(o[k]);
    expect(JSON.parse(env.PRIVATE_ENDPOINTS)).toEqual(JSON.parse(o.PRIVATE_ENDPOINTS));
    expect(env.ALLOWED_JWT_AUDIENCES).toBe("stac-higher-example");
    expect(env.AUTH_BEARER_AUDIENCE_CLAIM).toBe("client_id");
    expect(env.UPSTREAM_URL).toBe("http://stac-higher-stac-api:8082");
    expect(env.OIDC_DISCOVERY_URL).toBe("https://idp.example.com/realms/example/.well-known/openid-configuration");
    expect(env.OIDC_DISCOVERY_INTERNAL_URL).toBe(env.OIDC_DISCOVERY_URL);
    expect(env.POLICY_DATABASE_URL).toBe("postgresql://db.example.com:5432/stac_higher");
    expect(env.PGSSLMODE).toBe("verify-full");
    const internal = cm(render({ auth: { issuerInternal: "http://idp.internal:8080/realms/example/" } }), "auth-proxy");
    expect(internal.OIDC_DISCOVERY_INTERNAL_URL).toBe("http://idp.internal:8080/realms/example/.well-known/openid-configuration");
  });

  it("proxy role mapping follows the app's claims mapping", () => {
    const keycloak = cm(render({ auth: { claimsMapping: null } }), "auth-proxy");
    expect([keycloak.POLICY_ROLES_CLAIM, keycloak.POLICY_WRITE_ROLES]).toEqual(["realm_access.roles", "admin,operator"]);
    const cognito = cm(
      render({
        auth: {
          claimsMapping: {
            roles: "cognito:groups",
            roleMap: { "stac-admins": "admin", "stac-operators": "operator", "stac-members": "member", operator: "member" },
          },
        },
      }),
      "auth-proxy",
    );
    expect(cognito.POLICY_ROLES_CLAIM).toBe("cognito:groups");
    expect(cognito.POLICY_WRITE_ROLES).toBe("admin,stac-admins,stac-operators");
    const explicit = cm(render({ catalog: { authProxy: { rolesClaim: "roles", writeRoles: ["w1", "w2"] } } }), "auth-proxy");
    expect([explicit.POLICY_ROLES_CLAIM, explicit.POLICY_WRITE_ROLES]).toEqual(["roles", "w1,w2"]);
    const r = renderChart({ valuesFiles: scenario("base"), values: { auth: { claimsMapping: { roles: ["a", "b"] } } } });
    expect(r.ok).toBe(false);
    expect(r.stderr).toContain("catalog.authProxy.rolesClaim");
  });

  it("cube-server env: credential-free DSN, the object store endpoint, the default credential chain", () => {
    const env = cm(render(), "cube-server");
    expect(env.CUBE_DB_DSN).toBe("postgresql://db.example.com:5432/stac_higher");
    expect(env.CUBE_S3_ENDPOINT).toBe("https://s3.us-east-1.amazonaws.com");
    expect(env.CUBE_S3_REGION).toBe("us-east-1");
    expect(env.PLATFORM_ASSET_BUCKET).toBe("stac-higher-example-platform");
    expect(env.CUBE_S3_ACCESS_KEY_ID).toBeUndefined();
    expect(cm(render({ objectStore: { internalEndpoint: "http://minio.example.com:9000" } }), "cube-server").CUBE_S3_ENDPOINT).toBe(
      "http://minio.example.com:9000",
    );
  });

  it("titiler env carries the platform bucket and region and no static AWS key", () => {
    const env = cm(render(), "titiler");
    expect(env.PLATFORM_ASSET_BUCKET).toBe("stac-higher-example-platform");
    expect(env.ASSET_HREF_BASE).toBe("/api/assets");
    expect(env.AWS_REGION).toBe("us-east-1");
    expect(env.POSTGRES_HOST).toBe("db.example.com");
    expect(Object.keys(env).filter((k) => k.startsWith("AWS_ACCESS") || k.startsWith("AWS_SECRET"))).toEqual([]);
  });

  it("extra env is merged last and secret-looking keys are refused", () => {
    expect(cm(render({ catalog: { tipg: { env: { TIPG_DB_SCHEMAS: '["public"]', DB_MAX_CONN_SIZE: 8 } } } }), "tipg")).toMatchObject({
      TIPG_DB_SCHEMAS: '["public"]',
      DB_MAX_CONN_SIZE: "8",
    });
    for (const [values, key] of [
      [{ catalog: { stacApi: { env: { PGPASSWORD: "x" } } } }, "catalog.stacApi.env.PGPASSWORD"],
      [{ catalog: { titiler: { env: { POSTGRES_PASS: "x" } } } }, "catalog.titiler.env.POSTGRES_PASS"],
      [{ cubeServer: { env: { CUBE_S3_SECRET_ACCESS_KEY: "x" } } }, "cubeServer.env.CUBE_S3_SECRET_ACCESS_KEY"],
    ] as const) {
      const r = renderChart({ valuesFiles: scenario("base"), values });
      expect(r.ok).toBe(false);
      expect(r.stderr).toContain(key);
    }
  });

  it("refuses an unpinned third-party image", () => {
    const r = renderChart({ valuesFiles: scenario("base"), values: { catalog: { stacApi: { image: "ghcr.io/stac-utils/stac-fastapi-pgstac:6.3.1" } } } });
    expect(r.ok).toBe(false);
    expect(r.stderr).toContain("catalog.stacApi.image");
  });
});
