// @vitest-environment node
// R-8 (#144): processRuns.families as the one source of the profiles' backend.kubernetes, the Kueue objects and
// the NodePools. Plan docs/superpowers/plans/2026-11-26-r8-chart-process-runs.md (R8-R5, R8-R6, R8-R10; addendum).
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  CHART_DIR,
  REPO_DIR,
  helmReady,
  loadYamlDir,
  renderChart,
  scenario,
  stripChartMetadata,
} from "../../../../infra/helm/stac-higher/tests/render.mjs";
import { hardwareProfileSetSchema } from "@/lib/processes/hardware";
import { GPU_FAMILY, GPU_PROFILE } from "./runs-fixtures";

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

const FIXTURE = JSON.parse(readFileSync(join(REPO_DIR, "tests/contract-fixtures/process-k8s-cluster.json"), "utf8"));
const KUEUE_KINDS = ["LocalQueue", "WorkloadPriorityClass", "ResourceFlavor", "ClusterQueue"];
const VAP_KINDS = ["ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding"];
const ofKinds = (docs: Doc[], kinds: string[]) => docs.filter((d) => kinds.includes(d.kind));
const byKey = (docs: Doc[]) => Object.fromEntries(docs.map((d) => [`${d.kind}/${d.metadata.namespace ?? ""}/${d.metadata.name}`, d]));
const PLACEMENT = "stac-higher-runs-placement";
/** The objects reproduced from infra/kueue/common (the chart-only placement policy aside). */
const commonObjects = (docs: Doc[]) =>
  docs.filter((d) => ["LocalQueue", "WorkloadPriorityClass", ...VAP_KINDS].includes(d.kind) && d.metadata.name !== PLACEMENT);
const KIND_VALUES = {
  config: { hardwareProfiles: KIND_JSON },
  processRuns: { families: { "cpu-default": { dedicatedNodes: false, quota: { cpu: 1, memoryGi: 4, gpus: 0 } } }, nodes: { enabled: false } },
};
/** Every string anywhere in a value. */
const strings = (v: unknown): string[] =>
  typeof v === "string" ? [v] : Array.isArray(v) ? v.flatMap(strings) : v && typeof v === "object" ? Object.values(v).flatMap(strings) : [];

describe.skipIf(!helmReady())("Kueue objects and admission policies", () => {
  it("the chart's copies of infra/kueue/common are byte-identical (addendum A-R2)", () => {
    const src = join(REPO_DIR, "infra/kueue/common");
    const copy = join(CHART_DIR, "files/kueue/common");
    expect(readdirSync(copy).sort()).toEqual(readdirSync(src).sort());
    for (const f of readdirSync(src)) {
      expect(readFileSync(join(copy, f)).equals(readFileSync(join(src, f))), f).toBe(true);
    }
  });

  it("common objects equal infra/kueue/common", () => {
    const want = loadYamlDir(join(REPO_DIR, "infra/kueue/common"));
    const got = commonObjects(render()).map(stripChartMetadata);
    expect(byKey(got)).toEqual(byKey(want));
    expect(got).toHaveLength(want.length);
  });

  it("the common objects follow the namespace and the pipeline identity", () => {
    const docs = render({ processRuns: { namespace: "other-runs" }, identity: { serviceAccounts: { pipeline: "p2" } } });
    const lq = docs.find((d) => d.kind === "LocalQueue");
    expect(lq.metadata.namespace).toBe("other-runs");
    for (const b of docs.filter((d) => d.kind === "ValidatingAdmissionPolicyBinding")) {
      expect(b.spec.matchResources.namespaceSelector.matchLabels, b.metadata.name).toEqual({ "kubernetes.io/metadata.name": "other-runs" });
    }
    expect(docs.find((d) => d.kind === "ClusterQueue").spec.namespaceSelector.matchLabels).toEqual({ "kubernetes.io/metadata.name": "other-runs" });
    const upd = docs.find((d) => d.kind === "ValidatingAdmissionPolicy" && d.metadata.name === "stac-higher-runs-pod-updates");
    expect(upd.spec.matchConditions[0].expression).toBe("request.userInfo.username == 'system:serviceaccount:stac-higher:p2'");
    // (The NodeClass is also named stac-higher-runs by default; that is a name, not the namespace.)
    for (const d of ofKinds(docs, [...KUEUE_KINDS, ...VAP_KINDS])) {
      expect(strings(d), `${d.kind}/${d.metadata.name}`).not.toContain(FIXTURE.namespace);
    }
    expect(strings(docs).some((x) => x.includes("stac-higher:stac-higher-pipeline"))).toBe(false);
  });

  it("kind-equivalent values reproduce infra/kueue/kind", () => {
    const want = loadYamlDir(join(REPO_DIR, "infra/kueue/kind"));
    const got = ofKinds(render(KIND_VALUES), ["ResourceFlavor", "ClusterQueue"]).map(stripChartMetadata);
    expect(byKey(got)).toEqual(byKey(want));
  });

  it("names and priorities follow the fixture", () => {
    const docs = render();
    const lq = docs.find((d) => d.kind === "LocalQueue");
    const cq = docs.find((d) => d.kind === "ClusterQueue");
    expect([lq.metadata.name, lq.metadata.namespace, cq.metadata.name]).toEqual([FIXTURE.localQueue, FIXTURE.namespace, FIXTURE.clusterQueue]);
    expect(lq.spec.clusterQueue).toBe(cq.metadata.name);
    const wpcs = docs.filter((d) => d.kind === "WorkloadPriorityClass");
    expect(Object.fromEntries(wpcs.map((w) => [w.metadata.name, w.value]))).toEqual(FIXTURE.priorityClasses);
    for (const d of ofKinds(docs, KUEUE_KINDS)) expect(d.apiVersion, d.kind).toBe(FIXTURE.kueueApiVersion);
    const vaps = docs.filter((d) => d.kind === "ValidatingAdmissionPolicy").map((d) => d.metadata.name);
    expect(vaps).toEqual([...FIXTURE.admissionPolicies, PLACEMENT]);
    const bindings = docs.filter((d) => d.kind === "ValidatingAdmissionPolicyBinding");
    expect(bindings.map((b) => [b.metadata.name, b.spec.policyName])).toEqual(vaps.map((n) => [n, n]));
  });

  it("profiles, flavors and quotas come from the same family", () => {
    for (const values of [{}, gpuValues()]) {
      const docs = render(values);
      const flavors = Object.fromEntries(docs.filter((d) => d.kind === "ResourceFlavor").map((f) => [f.metadata.name, f]));
      const cq = docs.find((d) => d.kind === "ClusterQueue");
      const fams = { "cpu-default": { quota: { cpu: 16, memoryGi: 64, gpus: 0 } }, ...((values as any).processRuns?.families ?? {}) };
      const group = cq.spec.resourceGroups[0];
      expect(cq.spec.resourceGroups).toHaveLength(1);
      expect(group.flavors.map((f: any) => f.name)).toEqual(Object.keys(fams));
      for (const f of group.flavors) {
        const q = fams[f.name].quota;
        const want: any[] = [{ name: "cpu", nominalQuota: String(q.cpu) }, { name: "memory", nominalQuota: `${q.memoryGi}Gi` }];
        for (const r of group.coveredResources.slice(2)) want.push({ name: r, nominalQuota: String(fams[f.name].gpuResource === r ? q.gpus : 0) });
        expect(f.resources, f.name).toEqual(want);
      }
      for (const p of profilesOf(docs).profiles) {
        const block = p.backend.kubernetes;
        const family = block.node_selector["stac-higher.io/flavor"];
        const flavor = flavors[family];
        expect(flavor.spec.nodeLabels, p.id).toEqual(block.node_selector);
        for (const t of flavor.spec.nodeTaints ?? []) {
          expect(block.tolerations.some((x: any) => x.key === t.key && x.effect === t.effect), `${p.id} tolerates ${t.key}`).toBe(true);
        }
      }
    }
  });

  it("the gpu family adds its resource to the covered set", () => {
    const docs = render(gpuValues());
    const cq = docs.find((d) => d.kind === "ClusterQueue");
    expect(cq.spec.resourceGroups[0].coveredResources).toEqual(["cpu", "memory", "nvidia.com/gpu"]);
    expect(docs.find((d) => d.kind === "ResourceFlavor" && d.metadata.name === "gpu-l4").spec).toEqual({
      nodeLabels: { "stac-higher.io/flavor": "gpu-l4" },
      tolerations: [RUNS_TOLERATION],
      nodeTaints: [{ key: "nvidia.com/gpu", effect: "NoSchedule" }],
    });
  });

  it("a profile-less workload can be assigned the default flavor", () => {
    for (const values of [{}, gpuValues()]) {
      const docs = render(values);
      const cq = docs.find((d) => d.kind === "ClusterQueue");
      const first = cq.spec.resourceGroups[0].flavors[0].name;
      expect(first).toBe("cpu-default");
      const flavor = docs.find((d) => d.kind === "ResourceFlavor" && d.metadata.name === first);
      expect(flavor.spec.nodeTaints).toBeUndefined();
      expect(flavor.spec.tolerations).toEqual([RUNS_TOLERATION]);
      for (const f of docs.filter((d) => d.kind === "ResourceFlavor" && d.spec.nodeTaints)) {
        expect(f.metadata.name).toBe("gpu-l4");
      }
    }
  });

  it("clusterObjects: false keeps only the LocalQueue", () => {
    const docs = render({ processRuns: { clusterObjects: false } });
    expect(ofKinds(docs, [...KUEUE_KINDS, ...VAP_KINDS]).map((d) => d.kind)).toEqual(["LocalQueue"]);
    expect(profilesOf(docs).profiles[0].backend.kubernetes).toEqual(cpuBlock("cpu-default"));
  });
});

/** The pod-template placement each allowed entry describes (the CEL literal), as the executor would build it. */
const placementList = (docs: Doc[]) => {
  const vap = docs.find((d) => d.kind === "ValidatingAdmissionPolicy" && d.metadata.name === PLACEMENT);
  return JSON.parse(vap.spec.variables.find((v: any) => v.name === "allowed").expression);
};

describe.skipIf(!helmReady())("the placement policy (addendum A-R3)", () => {
  it("binds the runs namespace, Job CREATE only, fails closed", () => {
    const docs = render();
    const vap = docs.find((d) => d.kind === "ValidatingAdmissionPolicy" && d.metadata.name === PLACEMENT);
    expect(vap.spec.failurePolicy).toBe("Fail");
    expect(vap.spec.matchConstraints.resourceRules).toEqual([
      { apiGroups: ["batch"], apiVersions: ["v1"], operations: ["CREATE"], resources: ["jobs"] },
    ]);
    expect(vap.spec.paramKind).toBeUndefined();
    expect(vap.spec.validations).toHaveLength(1);
    expect(vap.spec.validations[0].reason).toBe("Invalid");
    expect(vap.spec.validations[0].message).toMatch(/^\[placement-profile\] /);
    const b = docs.find((d) => d.kind === "ValidatingAdmissionPolicyBinding" && d.metadata.name === PLACEMENT);
    expect(b.spec).toEqual({
      policyName: PLACEMENT,
      validationActions: ["Deny"],
      matchResources: { namespaceSelector: { matchLabels: { "kubernetes.io/metadata.name": FIXTURE.namespace } } },
    });
  });

  it("allows exactly no placement and each family's derived placement", () => {
    const toPlacement = (block: any) => ({
      nodeSelector: block.node_selector,
      tolerations: block.tolerations.map((t: any) => ({ key: t.key ?? "", operator: t.operator ?? "Equal", value: t.value ?? "", effect: t.effect ?? "" })),
      runtimeClassName: block.runtime_class ?? "",
    });
    const none = { nodeSelector: {}, tolerations: [], runtimeClassName: "" };
    for (const values of [{}, gpuValues()]) {
      const docs = render(values);
      const blocks = profilesOf(docs).profiles.map((p: any) => JSON.stringify(toPlacement(p.backend.kubernetes)));
      const want = [none, ...new Set(blocks)].map((x) => (typeof x === "string" ? JSON.parse(x) : x));
      expect(placementList(docs)).toEqual(want);
    }
    expect(placementList(render(KIND_VALUES))).toEqual([none]);
  });
});
