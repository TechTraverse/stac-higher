// @vitest-environment node
// R-7: the catalog plane (stac-api, auth-proxy, titiler, tipg) and the cube-server.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { parse } from "yaml";
import {
  CHART_DIR,
  containersOf,
  effectiveEnv,
  helmReady,
  renderChart,
  scenario,
} from "../../../../infra/helm/stac-higher/tests/render.mjs";

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

/** A compose service's auth-proxy env, with compose's `$$` escapes undone. */
function proxyEnvOf(file: any): Record<string, string> {
  const out: Record<string, string> = {};
  for (const e of file.services["auth-proxy"].environment as string[]) {
    const i = e.indexOf("=");
    out[e.slice(0, i)] = e.slice(i + 1).replaceAll("$$", "$");
  }
  return out;
}
const overlayEnv = () => proxyEnvOf(enforced);
const baseEnv = () => proxyEnvOf(compose);

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

  describe("proxy mode follows auth.mode, as compose's base file and enforced overlay do", () => {
    const BYPASS = { auth: { mode: "bypass", allowDevBypass: true } };
    const proxyContainer = (docs: Doc[]) =>
      docs.find((d) => d.kind === "Deployment" && d.metadata.name === "stac-higher-auth-proxy").spec.template.spec.containers[0];
    const ENFORCED_ONLY = ["ALLOWED_JWT_AUDIENCES", "AUTH_BEARER_AUDIENCE_CLAIM", "ITEMS_FILTER_CLS", "ITEMS_FILTER_PATH", "POLICY_DATABASE_URL", "POLICY_ROLES_CLAIM", "POLICY_WRITE_ROLES"];

    it("bypass renders the pass-through proxy, so the BFF's tokenless writes reach stac-api", () => {
      const docs = render(BYPASS);
      const env = effectiveEnv(docs, proxyContainer(docs));
      expect(env.DEFAULT_PUBLIC).toBe(baseEnv().DEFAULT_PUBLIC);
      expect(JSON.parse(String(env.PRIVATE_ENDPOINTS))).toEqual(JSON.parse(baseEnv().PRIVATE_ENDPOINTS));
      expect(JSON.parse(String(env.PRIVATE_ENDPOINTS))).toEqual({});
      for (const k of [...ENFORCED_ONLY, "CATALOG_BFF_SHARED_SECRET", "PGUSER", "PGPASSWORD"]) expect(env, k).not.toHaveProperty(k);
      // Every proxy setting compose's base file makes, the chart makes too.
      for (const k of Object.keys(baseEnv())) expect(env, k).toHaveProperty(k);
    });

    it("oidc renders the enforced proxy with every setting the overlay makes", () => {
      const docs = render();
      const env = effectiveEnv(docs, proxyContainer(docs));
      for (const k of [...Object.keys(baseEnv()), ...Object.keys(overlayEnv())]) expect(env, k).toHaveProperty(k);
      expect(JSON.parse(String(env.PRIVATE_ENDPOINTS))).toEqual(JSON.parse(overlayEnv().PRIVATE_ENDPOINTS));
      expect(env.CATALOG_BFF_SHARED_SECRET).toEqual({ secret: "stac-higher-platform", key: "CATALOG_BFF_SHARED_SECRET" });
      expect(env.PGUSER).toEqual({ secret: "stac-higher-db-proxy-policy", key: "username" });
    });
  });

  it("the proxy pins the token issuer to auth.issuer exactly as given (upstream v1.4.0 iss check)", () => {
    expect(cm(render(), "auth-proxy").OIDC_ISSUER).toBe("https://idp.example.com/realms/example");
    // With an internal issuer the discovery document reports it; tokens still carry the public one.
    const internal = cm(render({ auth: { issuerInternal: "http://idp.internal:8080/realms/example" } }), "auth-proxy");
    expect(internal.OIDC_ISSUER).toBe("https://idp.example.com/realms/example");
    // Issuers such as Auth0's end in "/": kept for the iss check, trimmed only for the discovery URL.
    const slash = cm(render({ auth: { issuer: "https://tenant.example.com/" } }), "auth-proxy");
    expect(slash.OIDC_ISSUER).toBe("https://tenant.example.com/");
    expect(slash.OIDC_DISCOVERY_URL).toBe("https://tenant.example.com/.well-known/openid-configuration");
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

  describe("workloads", () => {
    const dep = (docs: Doc[], c: string) => docs.find((d) => d.kind === "Deployment" && d.metadata.name === `stac-higher-${c}`);
    const container = (docs: Doc[], c: string) => dep(docs, c).spec.template.spec.containers[0];
    // [component, user env, password env, runtime Secret suffix, existing-mode Secret]
    const LOGINS: [string, string, string, string, string][] = [
      ["stac-api", "PGUSER", "PGPASSWORD", "db-stac-api", "stac-higher-db-stac-api"],
      ["auth-proxy", "PGUSER", "PGPASSWORD", "db-proxy-policy", "stac-higher-db-proxy-policy"],
      ["titiler", "POSTGRES_USER", "POSTGRES_PASS", "db-stac-reader", "stac-higher-db-stac-reader"],
      ["tipg", "POSTGRES_USER", "POSTGRES_PASS", "db-stac-reader", "stac-higher-db-stac-reader"],
      ["cube-server", "PGUSER", "PGPASSWORD", "db-cube-server", "stac-higher-db-cube-server"],
    ];

    it("each workload logs in as its own role from a runtime Secret", () => {
      for (const scen of ["base", "existing-secrets"]) {
        const docs = render({}, scen);
        for (const [c, u, p, suffix, existing] of LOGINS) {
          const secret = scen === "base" ? `stac-higher-${suffix}` : existing;
          const env = effectiveEnv(docs, container(docs, c));
          expect(env[u], `${scen}/${c}`).toEqual({ secret, key: "username" });
          expect(env[p], `${scen}/${c}`).toEqual({ secret, key: "password" });
          for (const v of Object.values(env)) {
            if (typeof v === "string") expect(v, `${scen}/${c}`).not.toMatch(/\/\/[^/@\s]*@/);
          }
        }
        expect(effectiveEnv(docs, container(docs, "auth-proxy")).CATALOG_BFF_SHARED_SECRET).toEqual({
          secret: "stac-higher-platform", // the same name in both scenarios
          key: "CATALOG_BFF_SHARED_SECRET",
        });
      }
    });

    it("catalog workloads read runtime Secrets only", () => {
      const docs = render();
      for (const c of COMPONENTS) {
        expect(JSON.stringify(dep(docs, c).spec), c).not.toMatch(/hook-/);
      }
    });

    it("pods are hardened with the image uid and carry no API token", () => {
      const docs = render();
      const want: Record<string, [number, number, string | undefined]> = {
        "stac-api": [1000, 1000, undefined],
        "auth-proxy": [1001, 1001, undefined],
        titiler: [1001, 999, "stac-higher-titiler"],
        tipg: [65534, 65534, undefined],
        "cube-server": [999, 999, "stac-higher-cube-server"],
      };
      for (const [c, [uid, gid, sa]] of Object.entries(want)) {
        const spec = dep(docs, c).spec.template.spec;
        expect(spec.securityContext, c).toMatchObject({ runAsNonRoot: true, runAsUser: uid, runAsGroup: gid, fsGroup: gid, seccompProfile: { type: "RuntimeDefault" } });
        expect(spec.automountServiceAccountToken, c).toBe(false);
        expect(spec.serviceAccountName, c).toBe(sa);
        for (const k of containersOf(spec)) {
          expect(k.securityContext, c).toMatchObject({ allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: { drop: ["ALL"] } });
          expect(k.volumeMounts.map((m: Doc) => m.mountPath).sort(), c).toEqual(["/etc/stac-higher/db-ca", "/tmp"]);
        }
        expect(spec.volumes.find((v: Doc) => v.name === "db-ca").configMap.name, c).toBe("rds-ca-bundle");
        const env = effectiveEnv(docs, spec.containers[0]);
        expect([env.HOME, env.PGSSLMODE, env.PGSSLROOTCERT], c).toEqual(["/tmp", "verify-full", "/etc/stac-higher/db-ca/ca-bundle.pem"]);
      }
    });

    it("titiler is scheduled on amd64 by default", () => {
      expect(dep(render(), "titiler").spec.template.spec.nodeSelector).toEqual({ "kubernetes.io/arch": "amd64" });
      expect(dep(render(), "tipg").spec.template.spec.nodeSelector).toBeUndefined();
    });

    it("commands, ports, probes and Services", () => {
      const docs = render();
      const want: Record<string, [number, string, string[] | undefined]> = {
        "stac-api": [8082, "/_mgmt/ping", ["uvicorn", "stac_fastapi.pgstac.app:app", "--port", "8082"]],
        "auth-proxy": [8000, "/healthz", undefined],
        titiler: [8084, "/healthz", ["uvicorn", "stac_higher_titiler.main:app", "--port", "8084"]],
        tipg: [8085, "/healthz", ["uvicorn", "tipg.main:app", "--port", "8085"]],
        "cube-server": [8086, "/health", undefined],
      };
      for (const [c, [port, ready, cmd]] of Object.entries(want)) {
        const k = container(docs, c);
        expect(k.ports, c).toEqual([{ name: "http", containerPort: port, protocol: "TCP" }]);
        expect(k.readinessProbe.httpGet, c).toEqual({ path: ready, port: "http" });
        expect(k.livenessProbe.tcpSocket, c).toEqual({ port: "http" });
        // Liveness only starts once the startup probe passes; its budget covers a slow upstream or cold RDS.
        expect(k.startupProbe.httpGet, c).toEqual({ path: ready, port: "http" });
        expect(k.startupProbe.periodSeconds * k.startupProbe.failureThreshold, c).toBeGreaterThanOrEqual(300);
        if (cmd) expect(k.command, c).toEqual([...cmd, "--host", "0.0.0.0", "--proxy-headers", "--forwarded-allow-ips=*"]);
        else expect(k.command, c).toBeUndefined();
        const svc = docs.find((d) => d.kind === "Service" && d.metadata.name === `stac-higher-${c}`);
        expect(svc.spec.ports, c).toEqual([{ name: "http", port, targetPort: "http", protocol: "TCP" }]);
        expect(dep(docs, c).spec.template.metadata.labels, c).toMatchObject(svc.spec.selector);
      }
    });

    it("images: chart keys for ours, the pinned refs for third parties, mirrored alike", () => {
      const docs = render();
      expect(container(docs, "titiler").image).toBe("ghcr.io/techtraverse/stac-higher-titiler:0.1.0");
      expect(container(docs, "auth-proxy").image).toBe("ghcr.io/techtraverse/stac-higher-auth-proxy:0.1.0");
      expect(container(docs, "cube-server").image).toBe("ghcr.io/techtraverse/stac-higher-cube-server:0.1.0");
      expect(container(docs, "stac-api").image).toBe(chartValues.catalog.stacApi.image);
      const m = render({}, "digests-mirror");
      expect(container(m, "tipg").image).toBe(chartValues.catalog.tipg.image.replace(/^ghcr\.io/, "000000000000.dkr.ecr.us-east-1.amazonaws.com/ghcr"));
    });

    it("config changes roll the catalog workloads", () => {
      const sum = (v: object) => dep(render(v), "titiler").spec.template.metadata.annotations["checksum/config"];
      expect(sum({ catalog: { titiler: { env: { GDAL_CACHEMAX: "10%" } } } })).not.toBe(sum({}));
      expect(sum({ catalog: { titiler: { replicas: 5 } } })).toBe(sum({}));
    });
  });

  describe("ingress", () => {
    const ing = (docs: Doc[], name: string) => docs.find((d) => d.kind === "Ingress" && d.metadata.name === `stac-higher-${name}`);
    const HOSTS: [string, string, string, string, number][] = [
      ["catalog", "catalog", "catalog.example.com", "auth-proxy", 8000],
      ["titiler", "titiler", "titiler.example.com", "titiler", 8084],
      ["tipg", "tipg", "tipg.example.com", "tipg", 8085],
      ["cubeServer", "cube-server", "cube.example.com", "cube-server", 8086],
    ];

    it("one Ingress per public host, routed to its Service", () => {
      const docs = render({ publicUrls: { tipg: "https://tipg.example.com/" } });
      for (const [, name, host, svc, port] of HOSTS) {
        const i = ing(docs, name);
        expect(i, name).toBeTruthy();
        expect(i.spec.rules).toEqual([
          { host, http: { paths: [{ path: "/", pathType: "Prefix", backend: { service: { name: `stac-higher-${svc}`, port: { number: port } } } }] } },
        ]);
        expect(i.spec.tls, name).toBeUndefined();
        expect(docs.some((d) => d.kind === "Service" && d.metadata.name === `stac-higher-${svc}`)).toBe(true);
      }
    });

    it("class, annotations and TLS pass through, per host over the shared settings", () => {
      const docs = render({
        ingress: {
          className: "alb",
          annotations: { "alb.ingress.kubernetes.io/group.name": "stac-higher", a: "shared" },
          tlsSecretName: "shared-tls",
          titiler: { annotations: { a: "own" }, tlsSecretName: "titiler-tls" },
        },
      });
      const t = ing(docs, "titiler");
      expect(t.spec.ingressClassName).toBe("alb");
      expect(t.metadata.annotations).toEqual({ "alb.ingress.kubernetes.io/group.name": "stac-higher", a: "own" });
      expect(t.spec.tls).toEqual([{ hosts: ["titiler.example.com"], secretName: "titiler-tls" }]);
      expect(ing(docs, "tipg").metadata.annotations.a).toBe("shared");
      expect(ing(docs, "tipg").spec.tls).toEqual([{ hosts: ["tipg.example.com"], secretName: "shared-tls" }]);
    });

    it("ingress.enabled false renders no Ingress", () => {
      expect(render({ ingress: { enabled: false } }).filter((d) => d.kind === "Ingress")).toEqual([]);
    });
  });

  it("refuses an unpinned third-party image", () => {
    const r = renderChart({ valuesFiles: scenario("base"), values: { catalog: { stacApi: { image: "ghcr.io/stac-utils/stac-fastapi-pgstac:6.3.1" } } } });
    expect(r.ok).toBe(false);
    expect(r.stderr).toContain("catalog.stacApi.image");
  });
});
