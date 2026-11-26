// @vitest-environment node
// R-8 (#144): processRuns.families as the one source of the profiles' backend.kubernetes, the Kueue objects and
// the NodePools. Plan docs/superpowers/plans/2026-11-26-r8-chart-process-runs.md (R8-R5, R8-R6, R8-R10; addendum).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  CHART_DIR,
  REPO_DIR,
  helmReady,
  renderChart,
  scenario,
} from "../../../../infra/helm/stac-higher/tests/render.mjs";
import { hardwareProfileSetSchema } from "@/lib/processes/hardware";

type Doc = any;
type Opts = Parameters<typeof renderChart>[0];
const base = (values: object): Opts => ({ valuesFiles: scenario("base"), values });
function render(values: object = {}, scen = "base"): Doc[] {
  const r = renderChart({ valuesFiles: scenario(scen), values });
  expect(r.ok, r.stderr).toBe(true);
  return r.docs;
}
function fails(values: object, ...needles: string[]) {
  const r = renderChart(base(values));
  expect(r.ok, `expected a failure naming ${needles.join(", ")}`).toBe(false);
  for (const n of needles) expect(r.stderr).toContain(n);
}
const configMap = (docs: Doc[], suffix: string) =>
  docs.find((d) => d.kind === "ConfigMap" && d.metadata.name === `stac-higher-${suffix}`);
const profilesOf = (docs: Doc[]) => JSON.parse(configMap(docs, "config-files").data["hardware-profiles.json"]);
const profile = (doc: any, id: string) => doc.profiles.find((p: any) => p.id === id);

const EKS_CPU = JSON.parse(readFileSync(join(CHART_DIR, "files/hardware-profiles/eks-cpu.json"), "utf8"));
const KIND_JSON = JSON.parse(readFileSync(join(REPO_DIR, "infra/hardware-profiles/kind.json"), "utf8"));
const RUNS_TOLERATION = { key: "stac-higher.io/runs", operator: "Equal", value: "true", effect: "NoSchedule" };
const GPU_TOLERATION = { key: "nvidia.com/gpu", operator: "Exists", effect: "NoSchedule" };
const cpuBlock = (family: string) => ({
  queue: "process-runs",
  node_selector: { "stac-higher.io/flavor": family },
  tolerations: [RUNS_TOLERATION],
  runtime_class: null,
  extended_resources: {},
});

/** The gpu-family scenario's GPU family and profile (inline here; tests/values/gpu-family.yaml carries the same). */
export const GPU_FAMILY = {
  gpuResource: "nvidia.com/gpu",
  extraTaints: [{ key: "nvidia.com/gpu", effect: "NoSchedule" }],
  quota: { cpu: 16, memoryGi: 64, gpus: 2 },
  nodePool: {
    requirements: [
      { key: "eks.amazonaws.com/instance-gpu-manufacturer", operator: "In", values: ["nvidia"] },
      { key: "eks.amazonaws.com/instance-gpu-name", operator: "In", values: ["l4"] },
      { key: "kubernetes.io/arch", operator: "In", values: ["amd64"] },
      { key: "karpenter.sh/capacity-type", operator: "In", values: ["on-demand"] },
    ],
    headroom: { cpu: 4, memoryGi: 16 },
  },
};
/** Spec §3.2's gpu-l4 profile (queue wait 900 s so it fits the default credential cap). */
export const GPU_PROFILE = {
  id: "gpu-l4",
  label: "GPU — NVIDIA L4 (24 GB)",
  description: "Single L4. Inference, cupy/numba kernels, medium models.",
  tier: "gpu",
  accelerator: { vendor: "nvidia", model: "l4", memory_gb: 24 },
  cpu: { min: 2, max: 16, default: 4 },
  memory_mb: { min: 4096, max: 65536, default: 16384 },
  gpu_count: { min: 1, max: 1, default: 1 },
  max_queue_wait_seconds: 900,
  image: null,
  backend: {},
};
const gpuDoc = (gpu: object = GPU_PROFILE) => ({ version: 1, profiles: [...EKS_CPU.profiles, gpu] });
const gpuValues = (extra: object = {}) => ({
  config: { hardwareProfiles: gpuDoc() },
  processRuns: { families: { "gpu-l4": GPU_FAMILY }, profileFamilies: { "gpu-l4": "gpu-l4" }, ...extra },
});

describe.skipIf(!helmReady())("hardware profiles derived from processRuns.families", () => {
  it("default profiles land on the cpu-default family", () => {
    const doc = profilesOf(render());
    expect(doc.profiles.map((p: any) => p.id)).toEqual(["standard", "cpu-large"]);
    for (const src of EKS_CPU.profiles) {
      const got = profile(doc, src.id);
      expect(got.backend.kubernetes, src.id).toEqual(cpuBlock("cpu-default"));
      const { backend: _b, ...rest } = got;
      const { backend: _s, ...want } = src;
      expect(rest).toEqual(want);
    }
  });

  it("the derived document still parses on both sides", () => {
    for (const values of [{}, gpuValues()]) {
      const doc = profilesOf(render(values));
      expect(hardwareProfileSetSchema.safeParse(doc).success).toBe(true);
      for (const p of doc.profiles) {
        expect(Object.keys(p.backend.kubernetes).sort()).toEqual(
          ["extended_resources", "node_selector", "queue", "runtime_class", "tolerations"],
        );
      }
    }
  });

  it("kind.json round-trips", () => {
    const kind = {
      config: { hardwareProfiles: KIND_JSON },
      processRuns: { families: { "cpu-default": { dedicatedNodes: false, quota: { cpu: 1, memoryGi: 4, gpus: 0 } } }, nodes: { enabled: false } },
    };
    expect(profilesOf(render(kind))).toEqual(KIND_JSON);
  });

  it("a hand-written backend.kubernetes must equal the derived one", () => {
    const doc = structuredClone(EKS_CPU);
    doc.profiles[0].backend = { kubernetes: { ...cpuBlock("cpu-default"), node_selector: {} } };
    fails({ config: { hardwareProfiles: doc } }, "config.hardwareProfiles", "standard");
    doc.profiles[0].backend = { kubernetes: cpuBlock("cpu-default"), docker: { note: "kept" } };
    const ok = profilesOf(render({ config: { hardwareProfiles: doc } }));
    expect(profile(ok, "standard").backend.docker).toEqual({ note: "kept" });
  });

  it("GPU profiles need a GPU family", () => {
    fails({ config: { hardwareProfiles: gpuDoc() }, processRuns: { families: { "gpu-l4": GPU_FAMILY } } }, "processRuns.profileFamilies", "gpu-l4");
    const doc = profilesOf(render(gpuValues()));
    expect(profile(doc, "gpu-l4").backend.kubernetes).toEqual({
      queue: "process-runs",
      node_selector: { "stac-higher.io/flavor": "gpu-l4" },
      tolerations: [RUNS_TOLERATION, GPU_TOLERATION],
      runtime_class: null,
      extended_resources: { "nvidia.com/gpu": "gpu_count" },
    });
    expect(profile(doc, "standard").backend.kubernetes).toEqual(cpuBlock("cpu-default"));
    fails(gpuValues({ profileFamilies: { "gpu-l4": "gpu-l4", standard: "gpu-l4" } }), "processRuns.profileFamilies.standard");
    fails(gpuValues({ profileFamilies: { "gpu-l4": "cpu-default" } }), "processRuns.profileFamilies.gpu-l4");
    fails(gpuValues({ profileFamilies: { "gpu-l4": "gpu-l4", nope: "cpu-default" } }), "processRuns.profileFamilies.nope");
    fails(gpuValues({ profileFamilies: { "gpu-l4": "missing" } }), "processRuns.profileFamilies.gpu-l4");
  });

  it("a toleration with a value is Equal, a runtime class is carried", () => {
    const fam = { ...GPU_FAMILY, extraTaints: [{ key: "nvidia.com/gpu", value: "present", effect: "NoSchedule" }], runtimeClass: "nvidia" };
    const doc = profilesOf(render(gpuValues({ families: { "gpu-l4": fam } })));
    const block = profile(doc, "gpu-l4").backend.kubernetes;
    expect(block.tolerations[1]).toEqual({ key: "nvidia.com/gpu", operator: "Equal", value: "present", effect: "NoSchedule" });
    expect(block.runtime_class).toBe("nvidia");
  });

  it("a profile that cannot fit its family's quota fails the render", () => {
    fails({ processRuns: { families: { "cpu-default": { quota: { cpu: 4, memoryGi: 64 } } } } }, "processRuns.families.cpu-default.quota.cpu", "cpu-large");
    fails({ processRuns: { families: { "cpu-default": { quota: { cpu: 16, memoryGi: 16 } } } } }, "processRuns.families.cpu-default.quota.memoryGi", "cpu-large");
    fails(
      {
        ...gpuValues({ families: { "gpu-l4": { ...GPU_FAMILY, quota: { cpu: 16, memoryGi: 64, gpus: 1 } } } }),
        config: { hardwareProfiles: gpuDoc({ ...GPU_PROFILE, gpu_count: { min: 1, max: 2, default: 1 } }) },
      },
      "processRuns.families.gpu-l4.quota.gpus",
      "gpu-l4",
    );
    const policy = JSON.parse(readFileSync(join(CHART_DIR, "files/image-policy/default.json"), "utf8"));
    policy.scan_limits.memory_mb = 70000;
    fails({ config: { imagePolicy: policy } }, "processRuns.families.cpu-default.quota.memoryGi", "config.imagePolicy");
  });

  it("the config checksum follows the families", () => {
    const sum = (docs: Doc[]) =>
      docs
        .filter((d) => d.kind === "Deployment" && ["stac-higher-app", "stac-higher-pipeline"].includes(d.metadata.name))
        .map((d) => d.spec.template.metadata.annotations["checksum/config"]);
    const a = sum(render());
    const b = sum(render({ processRuns: { nodes: { enabled: false }, families: { "cpu-default": { dedicatedNodes: false } } } }));
    expect(a).toHaveLength(2);
    expect(b[0]).not.toBe(a[0]);
    expect(b[1]).not.toBe(a[1]);
  });
});
