// @vitest-environment node
import { describe, expect, it } from "vitest";
import { helmReady, renderChart, scenario } from "../../../../infra/helm/stac-higher/tests/render.mjs";

type Doc = any;
function render(values: object = {}, scen = "base") {
  const r = renderChart({ valuesFiles: scenario(scen), values });
  expect(r.ok, r.stderr).toBe(true);
  return r.docs;
}
/** The release namespace's policies; the runs namespace's (R-8) are runs.test.ts's and ignore networkPolicy.enabled. */
const policies = (docs: Doc[]) => docs.filter((d) => d.kind === "NetworkPolicy" && d.metadata.namespace === "stac-higher");
const find = (docs: Doc[], name: string) => policies(docs).find((d) => d.metadata.name === name);
const podLabels = (docs: Doc[], name: string) =>
  docs.find((d) => d.kind === "Deployment" && d.metadata.name === name).spec.template.metadata.labels;
const component = (p: Doc) => p.spec.podSelector.matchLabels?.["app.kubernetes.io/component"];

describe.skipIf(!helmReady())("chart network policies", () => {
  it("default-deny ingress covers the namespace", () => {
    const p = find(render(), "stac-higher-default-deny-ingress");
    expect(p.spec.podSelector).toEqual({});
    expect(p.spec.policyTypes).toEqual(["Ingress"]);
    expect(p.spec.ingress).toBeUndefined();
    expect(p.spec.egress).toBeUndefined();
  });

  it("the app port is open by default and restrictable", () => {
    const p = find(render(), "stac-higher-app");
    expect(p.spec.ingress).toEqual([{ ports: [{ protocol: "TCP", port: 4321 }] }]);
    const from = [{ ipBlock: { cidr: "10.0.0.0/16" } }];
    const r = find(render({ networkPolicy: { appIngressFrom: from } }), "stac-higher-app");
    expect(r.spec.ingress).toEqual([{ from, ports: [{ protocol: "TCP", port: 4321 }] }]);
  });

  it("the pipeline accepts no ingress unless metrics scraping is allowed", () => {
    const docs = render();
    expect(policies(docs).filter((p) => component(p) === "pipeline")).toEqual([]);
    expect(find(docs, "stac-higher-pipeline-metrics")).toBeUndefined();
    const from = [{ namespaceSelector: { matchLabels: { "kubernetes.io/metadata.name": "monitoring" } } }];
    const p = find(render({ networkPolicy: { metricsFrom: from } }), "stac-higher-pipeline-metrics");
    expect(component(p)).toBe("pipeline");
    expect(p.spec.policyTypes).toEqual(["Ingress"]);
    expect(p.spec.ingress).toEqual([{ from, ports: [{ protocol: "TCP", port: 8083 }] }]);
  });

  const PORTS: [string, number][] = [["auth-proxy", 8000], ["titiler", 8084], ["tipg", 8085], ["cube-server", 8086]];
  const appSel = { podSelector: { matchLabels: { "app.kubernetes.io/name": "stac-higher", "app.kubernetes.io/instance": "stac-higher", "app.kubernetes.io/component": "app" } } };

  it("stac-api admits only the auth-proxy", () => {
    const p = find(render(), "stac-higher-stac-api");
    expect(p.spec.ingress).toEqual([
      {
        from: [{ podSelector: { matchLabels: { "app.kubernetes.io/name": "stac-higher", "app.kubernetes.io/instance": "stac-higher", "app.kubernetes.io/component": "auth-proxy" } } }],
        ports: [{ protocol: "TCP", port: 8082 }],
      },
    ]);
  });

  it("stac-api admits the demo seed pod only when the seed is enabled (its writes go there directly, I-169)", () => {
    const docs = render({}, "demo-seed");
    const job = docs.find((d) => d.kind === "Job" && d.spec.template.metadata.labels?.["app.kubernetes.io/component"] === "demo-seed");
    expect(job).toBeTruthy();
    const from = find(docs, "stac-higher-stac-api").spec.ingress[0].from;
    expect(from.map((p: any) => p.podSelector.matchLabels["app.kubernetes.io/component"])).toEqual(["auth-proxy", "demo-seed"]);
    const seedPeer = from[1].podSelector.matchLabels;
    expect(Object.keys(seedPeer).sort()).toEqual(["app.kubernetes.io/component", "app.kubernetes.io/instance", "app.kubernetes.io/name"]);
    expect(job.spec.template.metadata.labels).toMatchObject(seedPeer);
    const off = find(render({ demoSeed: { enabled: false } }, "demo-seed"), "stac-higher-stac-api").spec.ingress[0].from;
    expect(off.map((p: any) => p.podSelector.matchLabels["app.kubernetes.io/component"])).toEqual(["auth-proxy"]);
  });

  it("public catalog ports are open by default and follow publicIngressFrom, else appIngressFrom", () => {
    const open = render();
    for (const [c, port] of PORTS) expect(find(open, `stac-higher-${c}`).spec.ingress, c).toEqual([{ ports: [{ protocol: "TCP", port }] }]);
    const vpc = [{ ipBlock: { cidr: "10.0.0.0/16" } }];
    const other = [{ ipBlock: { cidr: "10.1.0.0/16" } }];
    for (const [values, want] of [
      [{ networkPolicy: { publicIngressFrom: vpc } }, vpc],
      [{ networkPolicy: { appIngressFrom: other } }, other],
      [{ networkPolicy: { appIngressFrom: other, publicIngressFrom: vpc } }, vpc],
      // An empty list is "unset", never "any source": it must not override a restricted appIngressFrom.
      [{ networkPolicy: { appIngressFrom: other, publicIngressFrom: [] } }, other],
    ] as const) {
      const docs = render(values);
      for (const [c, port] of PORTS) {
        const from = c === "auth-proxy" ? [...want, appSel] : want;
        expect(find(docs, `stac-higher-${c}`).spec.ingress, c).toEqual([{ from, ports: [{ protocol: "TCP", port }] }]);
      }
    }
  });

  it("policies select the workloads' real labels", () => {
    const docs = render({ networkPolicy: { metricsFrom: [{ podSelector: {} }] } });
    const catalog = ["auth-proxy", "stac-api", "titiler", "tipg", "cube-server"].map((c) => [`stac-higher-${c}`, `stac-higher-${c}`]);
    for (const [pol, dep] of [["stac-higher-app", "stac-higher-app"], ["stac-higher-pipeline-metrics", "stac-higher-pipeline"], ...catalog]) {
      const sel = find(docs, pol).spec.podSelector.matchLabels;
      expect(Object.keys(sel).length).toBeGreaterThan(0);
      expect(podLabels(docs, dep)).toMatchObject(sel);
    }
  });

  it("networkPolicy.enabled false renders none", () => {
    expect(policies(render({ networkPolicy: { enabled: false, metricsFrom: [{ podSelector: {} }] } }))).toEqual([]);
  });
});
