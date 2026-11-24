// @vitest-environment node
import { describe, expect, it } from "vitest";
import { helmReady, renderChart, scenario, podSpecs, containersOf, effectiveEnv } from "../../../../infra/helm/stac-higher/tests/render.mjs";

type Doc = any;
function render(values: object = {}, scen = "base") {
  const r = renderChart({ valuesFiles: scenario(scen), values });
  expect(r.ok, r.stderr).toBe(true);
  return r.docs;
}
const find = (docs: Doc[], kind: string, name: string) => docs.find((d) => d.kind === kind && d.metadata.name === name);
const appDep = (docs: Doc[]) => find(docs, "Deployment", "stac-higher-app");
const appContainer = (docs: Doc[]) => appDep(docs).spec.template.spec.containers[0];
const ingress = (docs: Doc[]) => find(docs, "Ingress", "stac-higher-app");
const shared = (docs: Doc[]) => find(docs, "ConfigMap", "stac-higher-env-shared").data;

const DIGEST_IMG = /^000000000000\.dkr\.ecr\.us-east-1\.amazonaws\.com\/ghcr\/techtraverse\/stac-higher-[a-z-]+@sha256:[0-9a-f]{64}$/;

describe.skipIf(!helmReady())("chart app workload", () => {
  it("every pulled image is digest-pinned and mirrored", () => {
    const docs = render({}, "digests-mirror");
    const specs = podSpecs(docs);
    expect(specs.length).toBeGreaterThan(0);
    for (const s of specs) {
      for (const c of containersOf(s.spec)) {
        expect(c.image, `${s.name}/${c.name}`).toMatch(DIGEST_IMG);
        expect(c.image).not.toContain("//");
      }
    }
  });

  it("rewrites only the mirrored host and trims a trailing slash", () => {
    const img = (v: object) => appContainer(render(v)).image;
    expect(img({ registry: { mirrorPrefixes: { "docker.io": "m.example.com/hub" } } })).toMatch(/^ghcr\.io\//);
    expect(img({ registry: { mirrorPrefixes: { "ghcr.io": "m.example.com/g/" } } })).toBe("m.example.com/g/techtraverse/stac-higher-app:0.1.0");
    expect(
      img({ global: { imageRegistry: "r.example.com/x/" }, registry: { mirrorPrefixes: { "ghcr.io": "m.example.com/g" } } }),
    ).toBe("r.example.com/x/techtraverse/stac-higher-app:0.1.0");
  });

  it("without digests images use the chart appVersion tag", () => {
    expect(appContainer(render()).image).toBe("ghcr.io/techtraverse/stac-higher-app:0.1.0");
    expect(appContainer(render({ global: { imageTag: "1.2.3" } })).image).toBe("ghcr.io/techtraverse/stac-higher-app:1.2.3");
  });

  it("the app reads DB credentials from the owner Secret, never from a URL", () => {
    for (const [scen, name] of [["base", "stac-higher-db-owner"], ["existing-secrets", "stac-higher-db-owner"]] as const) {
      const docs = render({}, scen);
      const env = effectiveEnv(docs, appContainer(docs));
      expect(env.PGUSER).toEqual({ secret: name, key: "username" });
      expect(env.PGPASSWORD).toEqual({ secret: name, key: "password" });
      expect(String(env.DATABASE_URL)).not.toMatch(/@.*@|\/\/[^/]*@/);
    }
    const docs = render({ secrets: { mode: "existing", existing: { platform: "p", dbMaster: "m", dbOwner: "stac-higher-db-owner-existing", dbStacApi: "a", dbStacReader: "r", dbProxyPolicy: "x", dbCubeServer: "c" } } });
    expect(effectiveEnv(docs, appContainer(docs)).PGUSER).toEqual({ secret: "stac-higher-db-owner-existing", key: "username" });
  });

  it("the app gets the four platform secrets and not PROXY_AUTH_TOKEN", () => {
    const docs = render();
    const env = effectiveEnv(docs, appContainer(docs));
    for (const k of ["SESSION_SECRET", "CATALOG_BFF_SHARED_SECRET", "INTERNAL_API_TOKEN", "CREDENTIALS_MASTER_KEY"]) {
      expect(env[k]).toEqual({ secret: "stac-higher-platform", key: k });
    }
    expect(env.PROXY_AUTH_TOKEN).toBeUndefined();
  });

  it("the app pod is hardened", () => {
    const spec = appDep(render()).spec.template.spec;
    expect(spec.securityContext).toMatchObject({ runAsNonRoot: true, runAsUser: 1000, runAsGroup: 1000, fsGroup: 1000, seccompProfile: { type: "RuntimeDefault" } });
    expect(spec.automountServiceAccountToken).toBe(false);
    expect(spec.serviceAccountName).toBe("stac-higher-app");
    for (const c of containersOf(spec)) {
      expect(c.securityContext).toMatchObject({ allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: { drop: ["ALL"] } });
      expect(c.volumeMounts.some((m: any) => m.mountPath === "/tmp")).toBe(true);
    }
    expect(spec.volumes.find((v: any) => v.name === "tmp").emptyDir.sizeLimit).toBe("256Mi");
    const off = appDep(render({ app: { readOnlyRootFilesystem: false } })).spec.template.spec;
    expect(off.containers[0].securityContext.readOnlyRootFilesystem).toBe(false);
    expect(off.containers[0].securityContext.allowPrivilegeEscalation).toBe(false);
  });

  it("config changes roll the app", () => {
    const sum = (v: object) => appDep(render(v)).spec.template.metadata.annotations["checksum/config"];
    const a = sum({});
    expect(a).toMatch(/^[0-9a-f]{64}$/);
    const hp = {
      version: 1,
      profiles: [{ id: "standard", label: "S", description: "d", tier: "cpu", accelerator: null, cpu: { min: 0.25, max: 4, default: 1 }, memory_mb: { min: 128, max: 16384, default: 512 }, gpu_count: null, max_queue_wait_seconds: 900, image: null, backend: { kubernetes: { queue: "process-runs", node_selector: {}, tolerations: [], runtime_class: null, extended_resources: {} } } }],
    };
    expect(sum({ config: { hardwareProfiles: hp } })).not.toBe(a);
    expect(sum({ app: { replicas: 5 } })).toBe(a);
  });

  it("mounts the config files at the paths the env names", () => {
    const docs = render();
    const spec = appDep(docs).spec.template.spec;
    const c = spec.containers[0];
    const mount = (p: string) => c.volumeMounts.find((m: any) => m.mountPath === p);
    const cfg = spec.volumes.find((v: any) => v.name === mount("/etc/stac-higher/config").name);
    expect(cfg.configMap.name).toBe("stac-higher-config-files");
    expect(mount("/etc/stac-higher/config").readOnly).toBe(true);
    const ca = spec.volumes.find((v: any) => v.name === mount("/etc/stac-higher/db-ca").name);
    expect(ca.configMap).toMatchObject({ name: "rds-ca-bundle", items: [{ key: "ca-bundle.pem", path: "ca-bundle.pem" }] });
    const env = effectiveEnv(docs, c);
    expect(env.PROCESS_HARDWARE_PROFILES_FILE).toBe("/etc/stac-higher/config/hardware-profiles.json");
    expect(env.PGSSLROOTCERT).toBe("/etc/stac-higher/db-ca/ca-bundle.pem");
  });

  it("serves the probes and port the brief names", () => {
    const c = appContainer(render());
    expect(c.ports).toEqual([{ name: "http", containerPort: 4321, protocol: "TCP" }]);
    expect(c.readinessProbe.httpGet).toEqual({ path: "/", port: "http" });
    expect(c.livenessProbe.tcpSocket.port).toBe("http");
    const svc = find(render(), "Service", "stac-higher-app");
    expect(svc.spec.ports[0]).toMatchObject({ port: 80, targetPort: "http" });
  });

  it("the HPA replaces replicas", () => {
    const off = render();
    expect(appDep(off).spec.replicas).toBe(2);
    expect(off.some((d) => d.kind === "HorizontalPodAutoscaler")).toBe(false);
    const docs = render({ app: { autoscaling: { enabled: true } } });
    const hpa = find(docs, "HorizontalPodAutoscaler", "stac-higher-app");
    expect(hpa.apiVersion).toBe("autoscaling/v2");
    expect(hpa.spec).toMatchObject({ minReplicas: 2, maxReplicas: 6, scaleTargetRef: { kind: "Deployment", name: "stac-higher-app" } });
    expect(hpa.spec.metrics[0].resource).toEqual({ name: "cpu", target: { type: "Utilization", averageUtilization: 70 } });
    expect(appDep(docs).spec.replicas).toBeUndefined();
  });

  it("derives the ingress host and redirect from publicUrls.app", () => {
    const docs = render({ publicUrls: { app: "https://app.example.com/" } });
    expect(ingress(docs).spec.rules[0].host).toBe("app.example.com");
    expect(effectiveEnv(docs, appContainer(docs)).OIDC_REDIRECT_URI).toBe("https://app.example.com/api/auth/callback");
  });

  it("the ingress host carries no port; the redirect keeps it", () => {
    const docs = render({ publicUrls: { app: "https://app.example.com:8443" }, ingress: { tlsSecretName: "app-tls" } });
    expect(ingress(docs).spec.rules[0].host).toBe("app.example.com");
    expect(ingress(docs).spec.tls[0].hosts).toEqual(["app.example.com"]);
    expect(effectiveEnv(docs, appContainer(docs)).OIDC_REDIRECT_URI).toBe("https://app.example.com:8443/api/auth/callback");
  });

  it("refuses an app URL with a path", () => {
    const r = renderChart({ valuesFiles: scenario("base"), values: { publicUrls: { app: "https://example.com/stac" } } });
    expect(r.ok).toBe(false);
    expect(r.stderr).toContain("publicUrls.app");
  });

  it("the ingress blocks /api/internal ahead of the app", () => {
    const docs = render();
    const paths = ingress(docs).spec.rules[0].http.paths;
    expect(paths[0]).toMatchObject({ path: "/api/internal", pathType: "Prefix", backend: { service: { name: "stac-higher-internal-blocked", port: { number: 80 } } } });
    expect(paths[1]).toMatchObject({ path: "/", pathType: "Prefix", backend: { service: { name: "stac-higher-app", port: { name: "http" } } } });
    expect(paths).toHaveLength(2);
    const blocked = find(docs, "Service", "stac-higher-internal-blocked");
    expect(blocked.spec.ports[0].port).toBe(80);
    const sel = blocked.spec.selector;
    for (const d of docs.filter((x) => ["Deployment", "Job"].includes(x.kind))) {
      const labels = d.spec.template.metadata.labels ?? {};
      expect(Object.entries(sel).every(([k, v]) => labels[k] === v), d.metadata.name).toBe(false);
    }
  });

  it("ingress class, annotations and TLS pass through", () => {
    const docs = render({
      ingress: {
        className: "alb",
        tlsSecretName: "app-tls",
        annotations: { "alb.ingress.kubernetes.io/scheme": "internet-facing", shared: "base" },
        app: { annotations: { shared: "app" } },
      },
    });
    const ing = ingress(docs);
    expect(ing.spec.ingressClassName).toBe("alb");
    expect(ing.metadata.annotations).toEqual({ "alb.ingress.kubernetes.io/scheme": "internet-facing", shared: "app" });
    expect(ing.spec.tls[0]).toEqual({ hosts: ["app.example.com"], secretName: "app-tls" });
    expect(ingress(render()).spec.tls).toBeUndefined();
  });

  it("ingress.enabled=false renders no Ingress", () => {
    expect(render({ ingress: { enabled: false } }).some((d) => d.kind === "Ingress")).toBe(false);
  });

  it("no secret value is a literal in the app env", () => {
    const docs = render();
    for (const e of appContainer(docs).env) expect(e.value).toBeUndefined();
  });
});
