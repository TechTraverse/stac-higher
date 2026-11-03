// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { helmReady, renderChart, scenario, containersOf, effectiveEnv, podSpecs, CHART_DIR } from "../../../../infra/helm/stac-higher/tests/render.mjs";

type Doc = any;
const PEM = "-----BEGIN CERTIFICATE-----\nMIIBplaceholder\n-----END CERTIFICATE-----";
function render(values: object = {}, scen = "base") {
  const r = renderChart({ valuesFiles: scenario(scen), values });
  expect(r.ok, r.stderr).toBe(true);
  return r.docs;
}
const find = (docs: Doc[], kind: string, name: string) => docs.find((d) => d.kind === kind && d.metadata.name === name);
const job = (docs: Doc[], n: string) => find(docs, "Job", `stac-higher-${n}`);
const JOBS: [string, number][] = [["db-bootstrap", -20], ["pgstac-migrate", -15], ["app-migrate", -10]];
const hookOf = (d: Doc) => ({ helm: d.metadata.annotations?.["helm.sh/hook"], argo: d.metadata.annotations?.["argocd.argoproj.io/hook"] });
const weightOf = (d: Doc) => Number(d.metadata.annotations["helm.sh/hook-weight"]);
const SCENARIOS: [string, object][] = [["base", {}], ["existing-secrets", {}], ["base", { database: { caBundle: { existingConfigMap: "", pem: PEM } } }]];

describe.skipIf(!helmReady())("chart db hook Jobs", () => {
  it("the three DB Jobs are ordered pre-install,pre-upgrade hooks under Helm and Argo", () => {
    const docs = render();
    for (const [n, w] of JOBS) {
      const j = job(docs, n);
      expect(j, n).toBeDefined();
      const a = j.metadata.annotations;
      expect(a["helm.sh/hook"]).toBe("pre-install,pre-upgrade");
      expect(a["helm.sh/hook-weight"]).toBe(String(w));
      expect(a["argocd.argoproj.io/hook"]).toBe("PreSync");
      expect(a["argocd.argoproj.io/sync-wave"]).toBe(String(w));
      expect(a["helm.sh/hook-delete-policy"]).toBe("before-hook-creation");
      expect(a["argocd.argoproj.io/hook-delete-policy"]).toBe("BeforeHookCreation");
      expect(j.spec.backoffLimit).toBe(2);
      expect(j.spec.activeDeadlineSeconds).toBe(900);
    }
  });

  it("every object a hook Job reads is an earlier hook or deployment-supplied", () => {
    for (const [scen, values] of SCENARIOS) {
      const docs = render(values, scen);
      const existing = new Set<string>(["rds-ca-bundle"]);
      const supplied = scen === "existing-secrets" ? Object.values({
        platform: "stac-higher-platform", dbMaster: "stac-higher-db-master", dbOwner: "stac-higher-db-owner", dbStacApi: "stac-higher-db-stac-api",
        dbStacReader: "stac-higher-db-stac-reader", dbProxyPolicy: "stac-higher-db-proxy-policy", dbCubeServer: "stac-higher-db-cube-server",
      }) : [];
      for (const s of supplied) existing.add(s);
      for (const [n] of JOBS) {
        const j = job(docs, n);
        const spec = j.spec.template.spec;
        const names = new Set<string>();
        for (const v of spec.volumes ?? []) if (v.configMap) names.add(v.configMap.name);
        for (const c of containersOf(spec)) {
          for (const e of c.env ?? []) {
            if (e.valueFrom?.secretKeyRef) names.add(e.valueFrom.secretKeyRef.name);
            if (e.valueFrom?.configMapKeyRef) names.add(e.valueFrom.configMapKeyRef.name);
          }
          for (const f of c.envFrom ?? []) names.add(f.configMapRef?.name ?? f.secretRef?.name);
        }
        expect(names.size).toBeGreaterThan(0);
        for (const name of names) {
          const provider = docs.find(
            (d) => (d.kind === "ConfigMap" && d.metadata.name === name) || (d.kind === "ExternalSecret" && d.spec.target.name === name),
          );
          if (provider) {
            const h = hookOf(provider);
            expect(h.helm, `${n} reads ${name}`).toBe("pre-install,pre-upgrade");
            expect(h.argo, `${n} reads ${name}`).toBe("PreSync");
            expect(weightOf(provider), `${n} reads ${name}`).toBeLessThan(weightOf(j));
          } else {
            expect(existing.has(name), `${n} reads ${name} (scenario ${scen})`).toBe(true);
          }
        }
      }
    }
  });

  it("Deployments reference no hook object", () => {
    for (const [scen, values] of SCENARIOS) {
      const docs = render(values, scen);
      const hookConfigMaps = docs.filter((d) => d.kind === "ConfigMap" && d.metadata.annotations?.["helm.sh/hook"]).map((d) => d.metadata.name);
      expect(hookConfigMaps).toContain("stac-higher-db-sql");
      for (const p of podSpecs(docs).filter((x) => x.kind === "Deployment")) {
        for (const v of p.spec.volumes ?? []) expect(hookConfigMaps).not.toContain(v.configMap?.name);
        for (const c of containersOf(p.spec)) for (const f of c.envFrom ?? []) expect(hookConfigMaps).not.toContain(f.configMapRef?.name);
      }
    }
  });

  it("only db-bootstrap reads the master credential", () => {
    const docs = render();
    const master = "stac-higher-hook-db-master";
    const readers = podSpecs(docs)
      .filter((p) => JSON.stringify(p.spec).includes(`"${master}"`))
      .map((p) => `${p.kind}/${p.name}`);
    expect(readers).toEqual(["Job/stac-higher-db-bootstrap"]);
    expect(podSpecs(docs).length).toBeGreaterThan(3);
  });

  it("db-bootstrap maps every role's username and password into settings", () => {
    const docs = render();
    const env = effectiveEnv(docs, job(docs, "db-bootstrap").spec.template.spec.containers[0]);
    const roles: Record<string, string> = { OWNER: "owner", API: "stac-api", READER: "stac-reader", PROXY: "proxy-policy", CUBE: "cube-server" };
    for (const [k, s] of Object.entries(roles)) {
      expect(env[`STAC_HIGHER_SQL_${k}_ROLE`]).toEqual({ secret: `stac-higher-hook-db-${s}`, key: "username" });
      expect(env[`STAC_HIGHER_SQL_${k}_PASSWORD`]).toEqual({ secret: `stac-higher-hook-db-${s}`, key: "password" });
    }
    expect(env.PGUSER).toEqual({ secret: "stac-higher-hook-db-master", key: "username" });
    expect(env.PGPASSWORD).toEqual({ secret: "stac-higher-hook-db-master", key: "password" });
    expect(Object.keys(env).filter((k) => k.startsWith("STAC_HIGHER_SQL_"))).toHaveLength(10);
    const c = job(docs, "db-bootstrap").spec.template.spec.containers[0];
    expect(c.command).toEqual(["python", "/etc/stac-higher/db/run_sql.py", "/etc/stac-higher/db/bootstrap.sql"]);
    expect(c.image).toBe("ghcr.io/techtraverse/stac-higher-pipeline:0.1.0");
    expect(env).toMatchObject({ PGHOST: "db.example.com", PGPORT: "5432", PGDATABASE: "stac_higher", PGSSLMODE: "verify-full", PGSSLROOTCERT: "/etc/stac-higher/db-ca/ca-bundle.pem", HOME: "/tmp", PYTHONDONTWRITEBYTECODE: "1" });
  });

  it("pgstac-migrate runs pypgstac migrate before the grants, as the owner", () => {
    const docs = render();
    const spec = job(docs, "pgstac-migrate").spec.template.spec;
    expect(spec.initContainers[0].name).toBe("pypgstac-migrate");
    expect(spec.initContainers[0].command).toEqual(["pypgstac", "migrate"]);
    expect(spec.containers).toHaveLength(1);
    expect(spec.containers[0].command).toEqual(["python", "/etc/stac-higher/db/run_sql.py", "/etc/stac-higher/db/pgstac-grants.sql"]);
    for (const c of containersOf(spec)) {
      const env = effectiveEnv(docs, c);
      expect(env.PGUSER).toEqual({ secret: "stac-higher-hook-db-owner", key: "username" });
      expect(env.PGPASSWORD).toEqual({ secret: "stac-higher-hook-db-owner", key: "password" });
    }
    const env = effectiveEnv(docs, spec.containers[0]);
    expect(env.STAC_HIGHER_SQL_API_ROLE).toEqual({ secret: "stac-higher-hook-db-stac-api", key: "username" });
    expect(env.STAC_HIGHER_SQL_READER_ROLE).toEqual({ secret: "stac-higher-hook-db-stac-reader", key: "username" });
    expect(Object.keys(env).filter((k) => k.startsWith("STAC_HIGHER_SQL_") && k.endsWith("_PASSWORD"))).toEqual([]);
  });

  it("app-migrate runs the migrate CLI from the app image before the app grants", () => {
    for (const scen of ["base", "existing-secrets"]) {
      const docs = render({}, scen);
      const spec = job(docs, "app-migrate").spec.template.spec;
      const init = spec.initContainers[0];
      expect(init.name).toBe("app-migrate");
      expect(init.command).toEqual(["node", "app/dist/server/migrate.mjs"]);
      expect(init.image).toBe("ghcr.io/techtraverse/stac-higher-app:0.1.0");
      const env = effectiveEnv(docs, init);
      expect(env.DATABASE_URL).toBe("postgresql://db.example.com:5432/stac_higher");
      expect(String(env.DATABASE_URL)).toMatch(/^postgresql:\/\/[^@/]+:\d+\/[^@/]+$/);
      const owner = scen === "base" ? "stac-higher-hook-db-owner" : "stac-higher-db-owner";
      expect(env.PGUSER).toEqual({ secret: owner, key: "username" });
      expect(env.PGPASSWORD).toEqual({ secret: owner, key: "password" });
      const g = spec.containers[0];
      expect(g.name).toBe("app-grants");
      expect(g.image).toBe("ghcr.io/techtraverse/stac-higher-pipeline:0.1.0");
      expect(g.command).toEqual(["python", "/etc/stac-higher/db/run_sql.py", "/etc/stac-higher/db/app-grants.sql"]);
      const genv = effectiveEnv(docs, g);
      expect(genv.STAC_HIGHER_SQL_PROXY_ROLE).toEqual({ secret: scen === "base" ? "stac-higher-hook-db-proxy-policy" : "stac-higher-db-proxy-policy", key: "username" });
      expect(genv.STAC_HIGHER_SQL_PROXY_PASSWORD).toBeUndefined();
    }
  });

  it("hook Jobs carry no ServiceAccount token and run hardened", () => {
    const docs = render();
    for (const [n] of JOBS) {
      const spec = job(docs, n).spec.template.spec;
      expect(spec.restartPolicy).toBe("Never");
      expect(spec.automountServiceAccountToken).toBe(false);
      expect(spec.serviceAccountName).toBeUndefined();
      expect(spec.securityContext).toMatchObject({ runAsNonRoot: true, runAsUser: 999, runAsGroup: 999, fsGroup: 999, seccompProfile: { type: "RuntimeDefault" } });
      for (const c of containersOf(spec)) {
        expect(c.securityContext).toMatchObject({ allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: { drop: ["ALL"] } });
        expect(c.volumeMounts).toEqual(expect.arrayContaining([
          expect.objectContaining({ name: "tmp", mountPath: "/tmp" }),
          expect.objectContaining({ name: "db-sql", mountPath: "/etc/stac-higher/db" }),
          expect.objectContaining({ name: "db-ca", mountPath: "/etc/stac-higher/db-ca" }),
        ]));
        expect(c.resources).toEqual({ requests: { cpu: "100m", memory: "256Mi" }, limits: { memory: "1Gi" } });
      }
      expect(spec.volumes.find((v: any) => v.name === "tmp").emptyDir.sizeLimit).toBe("64Mi");
    }
    const am = job(docs, "app-migrate").spec.template.spec;
    expect(am.initContainers[0].securityContext).toMatchObject({ runAsUser: 1000, runAsGroup: 1000 });
    expect(am.containers[0].securityContext.runAsUser).toBeUndefined();
  });

  it("scheduling and retry settings come from dbJobs", () => {
    const docs = render({ dbJobs: { backoffLimit: 5, activeDeadlineSeconds: 60, nodeSelector: { pool: "sys" }, tolerations: [{ key: "k", operator: "Exists" }], affinity: { nodeAffinity: {} } } });
    for (const [n] of JOBS) {
      const j = job(docs, n);
      expect(j.spec.backoffLimit).toBe(5);
      expect(j.spec.activeDeadlineSeconds).toBe(60);
      expect(j.spec.template.spec.nodeSelector).toEqual({ pool: "sys" });
      expect(j.spec.template.spec.tolerations).toEqual([{ key: "k", operator: "Exists" }]);
      expect(j.spec.template.spec.affinity).toEqual({ nodeAffinity: {} });
    }
    expect(job(render(), "db-bootstrap").spec.template.spec.nodeSelector).toBeUndefined();
  });

  it("external-secrets mode renders six hook ExternalSecrets at weight -30", () => {
    const docs = render();
    const hooks = docs.filter((d) => d.kind === "ExternalSecret" && d.metadata.labels["app.kubernetes.io/component"] === "db-hook");
    const byName = Object.fromEntries(hooks.map((d) => [d.metadata.name, d]));
    expect(Object.keys(byName).sort()).toEqual(["owner", "stac-api", "stac-reader", "proxy-policy", "cube-server", "master"].map((s) => `stac-higher-hook-db-${s}`).sort());
    for (const d of hooks) {
      expect(d.metadata.annotations["helm.sh/hook-weight"]).toBe("-30");
      expect(d.spec.data.map((x: any) => [x.secretKey, x.remoteRef.property])).toEqual([["username", "username"], ["password", "password"]]);
    }
    expect(byName["stac-higher-hook-db-master"].spec.data[0].remoteRef.key).toMatch(/rds!db/);
    expect(byName["stac-higher-hook-db-owner"].spec.data[0].remoteRef.key).toMatch(/stac_higher_owner/);
    expect(byName["stac-higher-hook-db-cube-server"].spec.data[0].remoteRef.key).toMatch(/cube_server/);
    // runtime ExternalSecrets are not duplicated
    expect(docs.filter((d) => d.kind === "ExternalSecret" && d.metadata.name === "stac-higher-db-owner")).toHaveLength(1);
  });

  it("existing mode renders the Jobs against the deployment's Secrets and no ExternalSecret", () => {
    const docs = render({}, "existing-secrets");
    expect(docs.filter((d) => d.kind === "ExternalSecret")).toEqual([]);
    expect(job(docs, "db-bootstrap")).toBeDefined();
    const env = effectiveEnv(docs, job(docs, "db-bootstrap").spec.template.spec.containers[0]);
    expect(env.PGUSER).toEqual({ secret: "stac-higher-db-master", key: "username" });
    expect(env.STAC_HIGHER_SQL_CUBE_PASSWORD).toEqual({ secret: "stac-higher-db-cube-server", key: "password" });
    expect(env.STAC_HIGHER_SQL_API_ROLE).toEqual({ secret: "stac-higher-db-stac-api", key: "username" });
  });

  it("the CA bundle reaches the Jobs from a hook ConfigMap or the deployment's own", () => {
    const base = job(render(), "db-bootstrap").spec.template.spec.volumes.find((v: any) => v.name === "db-ca");
    expect(base.configMap).toEqual({ name: "rds-ca-bundle", items: [{ key: "ca-bundle.pem", path: "ca-bundle.pem" }] });
    const docs = render({ database: { caBundle: { existingConfigMap: "", pem: PEM } } });
    const vol = job(docs, "db-bootstrap").spec.template.spec.volumes.find((v: any) => v.name === "db-ca");
    expect(vol.configMap.name).toBe("stac-higher-hook-db-ca");
    const cm = find(docs, "ConfigMap", "stac-higher-hook-db-ca");
    expect(cm.metadata.annotations["helm.sh/hook-weight"]).toBe("-30");
    expect(cm.data["ca-bundle.pem"]).toContain("BEGIN CERTIFICATE");
    expect(find(docs, "ConfigMap", "stac-higher-db-ca").metadata.annotations).toBeUndefined();
    expect(find(render(), "ConfigMap", "stac-higher-hook-db-ca")).toBeUndefined();
    const off = render({ database: { sslMode: "disable", caBundle: { existingConfigMap: "" } } });
    const spec = job(off, "db-bootstrap").spec.template.spec;
    expect(spec.volumes.find((v: any) => v.name === "db-ca")).toBeUndefined();
    expect(effectiveEnv(off, spec.containers[0]).PGSSLMODE).toBeUndefined();
  });

  it("renders identically twice", () => {
    const a = renderChart({ valuesFiles: scenario("base") });
    const b = renderChart({ valuesFiles: scenario("base") });
    expect(a.ok && b.ok).toBe(true);
    expect(JSON.stringify(a.docs)).toBe(JSON.stringify(b.docs));
    expect(a.docs.length).toBeGreaterThan(10);
  });

  it("the db-sql ConfigMap carries the chart's SQL files verbatim", () => {
    const cm = find(render(), "ConfigMap", "stac-higher-db-sql");
    const files = ["run_sql.py", "bootstrap.sql", "pgstac-grants.sql", "app-grants.sql"];
    expect(Object.keys(cm.data).sort()).toEqual([...files].sort());
    for (const f of files) expect(cm.data[f]).toBe(readFileSync(`${CHART_DIR}/files/db/${f}`, "utf8"));
    expect(cm.metadata.annotations["helm.sh/hook-weight"]).toBe("-30");
  });
});
