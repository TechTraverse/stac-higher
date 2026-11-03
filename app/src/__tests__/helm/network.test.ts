// @vitest-environment node
import { describe, expect, it } from "vitest";
import { helmReady, renderChart, scenario } from "../../../../infra/helm/stac-higher/tests/render.mjs";

type Doc = any;
function render(values: object = {}, scen = "base") {
  const r = renderChart({ valuesFiles: scenario(scen), values });
  expect(r.ok, r.stderr).toBe(true);
  return r.docs;
}
const policies = (docs: Doc[]) => docs.filter((d) => d.kind === "NetworkPolicy");
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

  it("policies select the workloads' real labels", () => {
    const docs = render({ networkPolicy: { metricsFrom: [{ podSelector: {} }] } });
    for (const [pol, dep] of [["stac-higher-app", "stac-higher-app"], ["stac-higher-pipeline-metrics", "stac-higher-pipeline"]]) {
      const sel = find(docs, pol).spec.podSelector.matchLabels;
      expect(Object.keys(sel).length).toBeGreaterThan(0);
      expect(podLabels(docs, dep)).toMatchObject(sel);
    }
  });

  it("networkPolicy.enabled false renders none", () => {
    expect(policies(render({ networkPolicy: { enabled: false, metricsFrom: [{ podSelector: {} }] } }))).toEqual([]);
  });
});
