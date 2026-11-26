// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  containersOf,
  effectiveEnv,
  ipv4Contains,
  scenario,
  stripChartMetadata,
} from "../../../../infra/helm/stac-higher/tests/render.mjs";

describe("render harness helpers", () => {
  it("effectiveEnv resolves envFrom ConfigMaps and lets explicit env win", () => {
    const docs = [
      { apiVersion: "v1", kind: "ConfigMap", metadata: { name: "c" }, data: { A: "1", B: "2" } },
    ];
    const container = {
      envFrom: [{ configMapRef: { name: "c" } }],
      env: [
        { name: "B", value: "x" },
        { name: "S", valueFrom: { secretKeyRef: { name: "s", key: "k" } } },
      ],
    };
    expect(effectiveEnv(docs, container)).toEqual({
      A: "1",
      B: "x",
      S: { secret: "s", key: "k" },
    });
  });

  it("containersOf lists init containers first", () => {
    const spec = { initContainers: [{ name: "i" }], containers: [{ name: "c" }] };
    expect(containersOf(spec).map((c: { name: string }) => c.name)).toEqual(["i", "c"]);
  });

  it("scenario layers the named file over base", () => {
    expect(scenario("base")).toHaveLength(1);
    expect(scenario("irsa").map((p: string) => p.split("/").pop())).toEqual(["base.yaml", "irsa.yaml"]);
  });

  it("stripChartMetadata drops only the chart's own labels and annotations", () => {
    const doc = {
      kind: "X",
      metadata: {
        name: "n",
        labels: { "helm.sh/chart": "c", "app.kubernetes.io/name": "s", "pod-security.kubernetes.io/enforce": "restricted" },
        annotations: { "helm.sh/hook": "pre-install", "argocd.argoproj.io/hook": "PreSync" },
      },
    };
    expect(stripChartMetadata(doc)).toEqual({
      kind: "X",
      metadata: { name: "n", labels: { "pod-security.kubernetes.io/enforce": "restricted" } },
    });
    expect(doc.metadata.labels["helm.sh/chart"]).toBe("c");
  });

  it("ipv4Contains does prefix math", () => {
    expect(ipv4Contains("0.0.0.0/0", "169.254.169.254")).toBe(true);
    expect(ipv4Contains("169.254.0.0/16", "169.254.170.23")).toBe(true);
    expect(ipv4Contains("192.0.2.0/24", "169.254.169.254")).toBe(false);
    expect(ipv4Contains("10.100.0.10/32", "10.100.0.10")).toBe(true);
    expect(ipv4Contains("10.100.0.10/32", "10.100.0.11")).toBe(false);
    expect(() => ipv4Contains("fd00::/8", "10.0.0.1")).toThrow();
  });
});
