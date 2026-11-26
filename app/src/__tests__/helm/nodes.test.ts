// @vitest-environment node
// R-8 (#144): the EKS Auto Mode NodeClass and one NodePool per family (R8-R7, R8-R8, R8-R13).
import { describe, expect, it } from "vitest";
import { helmReady, renderChart, scenario } from "../../../../infra/helm/stac-higher/tests/render.mjs";
import { GPU_FAMILY, GPU_PROFILE } from "./runs-fixtures";

type Doc = any;
function render(values: object = {}, scen = "base"): Doc[] {
  const r = renderChart({ valuesFiles: scenario(scen), values });
  expect(r.ok, r.stderr).toBe(true);
  return r.docs;
}
const nodeClasses = (docs: Doc[]) => docs.filter((d) => d.kind === "NodeClass");
const nodePools = (docs: Doc[]) => docs.filter((d) => d.kind === "NodePool");
const pool = (docs: Doc[], family: string) => nodePools(docs).find((d) => d.metadata.name === `stac-higher-runs-${family}`);
const profilesOf = (docs: Doc[]) =>
  JSON.parse(docs.find((d) => d.kind === "ConfigMap" && d.metadata.name === "stac-higher-config-files").data["hardware-profiles.json"]);
const RUNS_TAINT = { key: "stac-higher.io/runs", value: "true", effect: "NoSchedule" };
const EKS_CPU_PROFILES = ["standard", "cpu-large"];
const gpuValues = () => ({
  config: {
    hardwareProfiles: {
      version: 1,
      profiles: [
        { id: "standard", label: "S", description: "d", tier: "cpu", accelerator: null, cpu: { min: 0.25, max: 4, default: 1 }, memory_mb: { min: 128, max: 16384, default: 512 }, gpu_count: null, max_queue_wait_seconds: 900, image: null, backend: {} },
        GPU_PROFILE,
      ],
    },
  },
  processRuns: { families: { "gpu-l4": GPU_FAMILY }, profileFamilies: { "gpu-l4": "gpu-l4" } },
});
/** A toleration tolerates a taint (Kubernetes' rule). */
const tolerates = (t: any, taint: any) =>
  (t.key === undefined || t.key === taint.key) &&
  (!t.effect || t.effect === taint.effect) &&
  ((t.operator ?? "Equal") === "Exists" || (t.value ?? "") === (taint.value ?? ""));

describe.skipIf(!helmReady())("runs nodes (EKS Auto Mode)", () => {
  it("the runs NodeClass", () => {
    const [nc] = nodeClasses(render());
    expect(nc.apiVersion).toBe("eks.amazonaws.com/v1");
    expect(nc.metadata.name).toBe("stac-higher-runs");
    expect(nc.metadata.namespace).toBeUndefined();
    expect(nc.spec).toEqual({
      role: "stac-higher-example-runs-node",
      subnetSelectorTerms: [{ tags: { "kubernetes.io/role/internal-elb": "1" } }],
      securityGroupSelectorTerms: [{ tags: { Name: "stac-higher-example-runs-nodes" } }],
      networkPolicy: "DefaultDeny",
      networkPolicyEventLogs: "Disabled",
      advancedCompute: { kubelet: { podPidsLimit: 4096 } },
    });
    const set = nodeClasses(render({ processRuns: { nodes: { nodeClass: { ephemeralStorage: { size: "80Gi" }, tags: { team: "x" }, podPidsLimit: -1 } } } }))[0];
    expect(set.spec.ephemeralStorage).toEqual({ size: "80Gi" });
    expect(set.spec.tags).toEqual({ team: "x" });
    expect(set.spec.advancedCompute.kubelet.podPidsLimit).toBe(-1);
  });

  it("one NodePool per enabled family", () => {
    const docs = render();
    expect(nodePools(docs).map((p) => p.metadata.name)).toEqual(["stac-higher-runs-cpu-default"]);
    const p = pool(docs, "cpu-default");
    expect(p.apiVersion).toBe("karpenter.sh/v1");
    expect(p.spec.template.metadata).toEqual({ labels: { "stac-higher.io/flavor": "cpu-default" } });
    expect(p.spec.template.spec.nodeClassRef).toEqual({ group: "eks.amazonaws.com", kind: "NodeClass", name: "stac-higher-runs" });
    expect(p.spec.template.spec.taints).toEqual([RUNS_TAINT]);
    expect(p.spec.template.spec.requirements).toEqual([
      { key: "eks.amazonaws.com/instance-category", operator: "In", values: ["c", "m", "r"] },
      { key: "kubernetes.io/arch", operator: "In", values: ["amd64"] },
      { key: "karpenter.sh/capacity-type", operator: "In", values: ["on-demand"] },
    ]);
    const gpu = render(gpuValues());
    expect(nodePools(gpu).map((x) => x.metadata.name).sort()).toEqual(["stac-higher-runs-cpu-default", "stac-higher-runs-gpu-l4"]);
    expect(pool(gpu, "gpu-l4").spec.template.spec.taints).toEqual([RUNS_TAINT, { key: "nvidia.com/gpu", effect: "NoSchedule" }]);
    const off = render({ processRuns: { families: { "gpu-l4": { ...GPU_FAMILY, enabled: false } } } });
    expect(nodePools(off).map((x) => x.metadata.name)).toEqual(["stac-higher-runs-cpu-default"]);
    const ext = render({ processRuns: { nodes: { nodeClass: { create: false, name: "default" } } } });
    expect(nodeClasses(ext)).toEqual([]);
    expect(pool(ext, "cpu-default").spec.template.spec.nodeClassRef.name).toBe("default");
  });

  it("NodePool limits are quota plus headroom", () => {
    expect(pool(render(), "cpu-default").spec.limits).toEqual({ cpu: "20", memory: "80Gi" });
    const gpu = render(gpuValues());
    expect(pool(gpu, "gpu-l4").spec.limits).toEqual({ cpu: "20", memory: "80Gi", "nvidia.com/gpu": "2" });
    const noHead = render({ ...gpuValues(), processRuns: { ...gpuValues().processRuns, families: { "gpu-l4": { ...GPU_FAMILY, nodePool: { requirements: GPU_FAMILY.nodePool.requirements } } } } });
    expect(pool(noHead, "gpu-l4").spec.limits).toEqual({ cpu: "16", memory: "64Gi", "nvidia.com/gpu": "2" });
    // The same family drives the ClusterQueue: limits - headroom = nominal quota.
    for (const docs of [render(), gpu]) {
      const cq = docs.find((d: Doc) => d.kind === "ClusterQueue");
      for (const f of cq.spec.resourceGroups[0].flavors) {
        const lim = pool(docs, f.name).spec.limits;
        const q = Object.fromEntries(f.resources.map((r: any) => [r.name, r.nominalQuota]));
        expect(Number(lim.cpu) - 4).toBe(Number(q.cpu));
        expect(parseInt(lim.memory) - 16).toBe(parseInt(q.memory));
      }
    }
  });

  it("the pool's nodes are exactly what the profiles select", () => {
    for (const values of [{}, gpuValues()]) {
      const docs = render(values);
      for (const p of profilesOf(docs).profiles) {
        const block = p.backend.kubernetes;
        const np = pool(docs, block.node_selector["stac-higher.io/flavor"]);
        expect(np.spec.template.metadata.labels, p.id).toMatchObject(block.node_selector);
        for (const taint of np.spec.template.spec.taints) {
          expect(block.tolerations.some((t: any) => tolerates(t, taint)), `${p.id} tolerates ${taint.key}`).toBe(true);
        }
      }
    }
    expect(EKS_CPU_PROFILES).toHaveLength(2);
  });

  it("nodes never consolidate under a run", () => {
    const p = pool(render(), "cpu-default");
    expect(p.spec.disruption).toEqual({ consolidationPolicy: "WhenEmpty", consolidateAfter: "5m" });
    expect(p.spec.template.spec.expireAfter).toBe("336h");
    expect(p.spec.template.spec.terminationGracePeriod).toBe("25h");
  });

  it("nodes.enabled: false or clusterObjects: false renders no NodeClass or NodePool", () => {
    for (const values of [{ processRuns: { nodes: { enabled: false } } }, { processRuns: { clusterObjects: false } }]) {
      const docs = render(values);
      expect(nodeClasses(docs)).toEqual([]);
      expect(nodePools(docs)).toEqual([]);
    }
  });
});
