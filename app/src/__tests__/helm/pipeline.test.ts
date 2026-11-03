// @vitest-environment node
import { describe, expect, it } from "vitest";
import { helmReady, renderChart, scenario, containersOf, effectiveEnv } from "../../../../infra/helm/stac-higher/tests/render.mjs";

type Doc = any;
function render(values: object = {}, scen = "base") {
  const r = renderChart({ valuesFiles: scenario(scen), values });
  expect(r.ok, r.stderr).toBe(true);
  return r.docs;
}
const find = (docs: Doc[], kind: string, name: string) => docs.find((d) => d.kind === kind && d.metadata.name === name);
const dep = (docs: Doc[]) => find(docs, "Deployment", "stac-higher-pipeline");
const container = (docs: Doc[]) => dep(docs).spec.template.spec.containers[0];
const HUB_ARN = "arn:aws:secretsmanager:us-east-1:000000000000:secret:stac-higher-example/dockerhub-AbCdEf";

describe.skipIf(!helmReady())("chart pipeline workload", () => {
  it("the pipeline is a single Recreate replica on the Kubernetes executor", () => {
    const docs = render();
    const d = dep(docs);
    expect(d.spec.replicas).toBe(1);
    expect(d.spec.strategy).toEqual({ type: "Recreate" });
    expect(d.spec.template.spec.terminationGracePeriodSeconds).toBeGreaterThanOrEqual(30);
    expect(d.spec.template.spec.serviceAccountName).toBe("stac-higher-pipeline");
    expect(d.spec.template.spec.containers[0].image).toBe("ghcr.io/techtraverse/stac-higher-pipeline:0.1.0");
    expect(d.spec.template.spec.containers[0].command).toBeUndefined();
    expect(effectiveEnv(docs, container(docs)).PROCESS_EXECUTOR).toBe("kubernetes");
    expect(container(docs).envFrom.map((e: any) => e.configMapRef.name)).toEqual(["stac-higher-env-shared", "stac-higher-env-pipeline"]);
  });

  it("the pipeline reads DB credentials and the master key from Secrets", () => {
    for (const scen of ["base", "existing-secrets"]) {
      const docs = render({}, scen);
      const env = effectiveEnv(docs, container(docs));
      expect(env.PGUSER).toEqual({ secret: "stac-higher-db-owner", key: "username" });
      expect(env.PGPASSWORD).toEqual({ secret: "stac-higher-db-owner", key: "password" });
      expect(env.CREDENTIALS_MASTER_KEY).toEqual({ secret: "stac-higher-platform", key: "CREDENTIALS_MASTER_KEY" });
      expect(env.SESSION_SECRET).toBeUndefined();
      expect(String(env.DATABASE_URL)).toMatch(/^postgresql:\/\/[^@/]+:\d+\/[^@/]+$/);
    }
    const docs = render({ secrets: { mode: "existing", existing: { platform: "plat", dbMaster: "m", dbOwner: "own", dbStacApi: "a", dbStacReader: "r", dbProxyPolicy: "x", dbCubeServer: "c" } } });
    const env = effectiveEnv(docs, container(docs));
    expect(env.PGUSER).toEqual({ secret: "own", key: "username" });
    expect(env.CREDENTIALS_MASTER_KEY).toEqual({ secret: "plat", key: "CREDENTIALS_MASTER_KEY" });
  });

  it("Docker Hub credentials are wired only when configured", () => {
    const none = effectiveEnv(render(), container(render()));
    expect(none.REGISTRY_DOCKERHUB_USER).toBeUndefined();
    expect(none.REGISTRY_DOCKERHUB_TOKEN).toBeUndefined();
    const docs = render({ secrets: { dockerHubArn: HUB_ARN } });
    const env = effectiveEnv(docs, container(docs));
    expect(env.REGISTRY_DOCKERHUB_USER).toEqual({ secret: "stac-higher-dockerhub", key: "username" });
    expect(env.REGISTRY_DOCKERHUB_TOKEN).toEqual({ secret: "stac-higher-dockerhub", key: "token" });
    const ex = render({ secrets: { mode: "existing", existing: { platform: "p", dbMaster: "m", dbOwner: "o", dbStacApi: "a", dbStacReader: "r", dbProxyPolicy: "x", dbCubeServer: "c", dockerHub: "my-hub" } } });
    expect(effectiveEnv(ex, container(ex)).REGISTRY_DOCKERHUB_TOKEN).toEqual({ secret: "my-hub", key: "token" });
  });

  it("the pipeline pod is hardened with the image uid", () => {
    const docs = render();
    const spec = dep(docs).spec.template.spec;
    expect(spec.securityContext).toMatchObject({ runAsNonRoot: true, runAsUser: 999, runAsGroup: 999, fsGroup: 999, seccompProfile: { type: "RuntimeDefault" } });
    expect(spec.automountServiceAccountToken).toBe(true);
    for (const c of containersOf(spec)) {
      expect(c.securityContext).toMatchObject({ allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: { drop: ["ALL"] } });
      expect(c.volumeMounts.some((m: any) => m.mountPath === "/tmp")).toBe(true);
    }
    expect(spec.volumes.find((v: any) => v.name === "tmp").emptyDir.sizeLimit).toBe("2Gi");
    const env = effectiveEnv(docs, container(docs));
    expect(env.HOME).toBe("/tmp");
    expect(env.PYTHONDONTWRITEBYTECODE).toBe("1");
    const off = dep(render({ pipeline: { readOnlyRootFilesystem: false } })).spec.template.spec.containers[0];
    expect(off.securityContext.readOnlyRootFilesystem).toBe(false);
    expect(off.securityContext.allowPrivilegeEscalation).toBe(false);
  });

  it("probes: ready on /health, alive on the port", () => {
    const c = container(render());
    expect(c.ports).toEqual([{ name: "http", containerPort: 8083, protocol: "TCP" }]);
    expect(c.readinessProbe.httpGet).toEqual({ path: "/health", port: "http" });
    expect(c.livenessProbe.tcpSocket.port).toBe("http");
    expect(c.livenessProbe.httpGet).toBeUndefined();
    const svc = find(render(), "Service", "stac-higher-pipeline");
    expect(svc.spec.ports).toEqual([{ name: "http", port: 8083, targetPort: "http", protocol: "TCP" }]);
    expect(svc.spec.selector).toEqual(dep(render()).spec.selector.matchLabels);
  });

  it("the ServiceMonitor is opt-in", () => {
    expect(render().find((d: Doc) => d.kind === "ServiceMonitor")).toBeUndefined();
    const docs = render({ pipeline: { metrics: { serviceMonitor: { enabled: true, interval: "15s", labels: { release: "prom" } } } }, networkPolicy: { metricsFrom: [{ ipBlock: { cidr: "10.0.0.0/16" } }] } });
    const sm = find(docs, "ServiceMonitor", "stac-higher-pipeline");
    expect(sm.apiVersion).toBe("monitoring.coreos.com/v1");
    expect(sm.metadata.labels.release).toBe("prom");
    expect(sm.spec.endpoints).toEqual([{ port: "http", path: "/metrics", interval: "15s" }]);
    const svcLabels = find(docs, "Service", "stac-higher-pipeline").metadata.labels;
    expect(sm.spec.selector.matchLabels).toEqual(dep(docs).spec.selector.matchLabels);
    for (const [k, v] of Object.entries(sm.spec.selector.matchLabels)) expect(svcLabels[k]).toBe(v);
    expect(sm.spec.namespaceSelector).toEqual({ matchNames: ["stac-higher"] });
  });

  it("both Deployments mount the same config ConfigMap and carry the same checksum", () => {
    const docs = render();
    const app = find(docs, "Deployment", "stac-higher-app").spec.template;
    const pipe = dep(docs).spec.template;
    expect(pipe.metadata.annotations["checksum/config"]).toMatch(/^[0-9a-f]{64}$/);
    expect(pipe.metadata.annotations["checksum/config"]).toBe(app.metadata.annotations["checksum/config"]);
    const cfg = (t: any) => t.spec.volumes.find((v: any) => v.name === "config-files").configMap.name;
    expect(cfg(pipe)).toBe("stac-higher-config-files");
    expect(cfg(pipe)).toBe(cfg(app));
    expect(pipe.spec.containers[0].volumeMounts.find((m: any) => m.mountPath === "/etc/stac-higher/config").readOnly).toBe(true);
    const custom = dep(render({ pipeline: { podAnnotations: { team: "x" } } })).spec.template.metadata.annotations;
    expect(custom.team).toBe("x");
    expect(custom["checksum/config"]).toBe(pipe.metadata.annotations["checksum/config"]);
  });
});
