// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  containersOf,
  effectiveEnv,
  scenario,
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
});
