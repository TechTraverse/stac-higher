// @vitest-environment node
// R-8 (#144): the process-runs plane in the runs namespace — values contract, namespace and RBAC,
// NetworkPolicies. Plan docs/superpowers/plans/2026-11-26-r8-chart-process-runs.md (and its addendum).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  REPO_DIR,
  helmReady,
  loadYamlDir,
  renderChart,
  scenario,
  stripChartMetadata,
} from "../../../../infra/helm/stac-higher/tests/render.mjs";

export const FIXTURE = JSON.parse(
  readFileSync(join(REPO_DIR, "tests/contract-fixtures/process-k8s-cluster.json"), "utf8"),
);

type Opts = Parameters<typeof renderChart>[0];
const base = (values: object, extra: Partial<NonNullable<Opts>> = {}): Opts => ({
  valuesFiles: scenario("base"),
  values,
  ...extra,
});

/** The render fails and the first error line starts with the key path. */
function failsNaming(opts: Opts, key: string) {
  const r = renderChart(opts);
  expect(r.ok, `expected a failure naming ${key}`).toBe(false);
  const msg = r.stderr.split("\n").find((l) => l.includes("error")) ?? r.stderr;
  expect(msg, r.stderr).toMatch(new RegExp(`(execution error at [^:]+: [^:]+: |: )${key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`));
}

describe.skipIf(!helmReady())("runs plane: values contract", () => {
  it("the runs plane names its missing inputs", () => {
    failsNaming(base({ processRuns: { network: { dns: [] } } }), "processRuns.network.dns");
    failsNaming(base({ processRuns: { nodes: { nodeClass: { role: "" } } } }), "processRuns.nodes.nodeClass.role");
    failsNaming(
      base({ processRuns: { nodes: { nodeClass: { subnetSelectorTerms: [] } } } }),
      "processRuns.nodes.nodeClass.subnetSelectorTerms",
    );
    failsNaming(
      base({ processRuns: { nodes: { nodeClass: { securityGroupSelectorTerms: [] } } } }),
      "processRuns.nodes.nodeClass.securityGroupSelectorTerms",
    );
    const r = renderChart(base({ aws: { s3PrefixListCidrs: [] } }));
    expect(r.ok).toBe(false);
    expect(r.stderr).toContain("aws.s3PrefixListCidrs");
    expect(r.stderr).toContain("processRuns.network.objectStore");
    const minio = renderChart(
      base({ aws: { s3PrefixListCidrs: [] }, processRuns: { network: { objectStore: [{ podSelector: { matchLabels: { app: "minio" } } }] } } }),
    );
    expect(minio.ok, minio.stderr).toBe(true);
    const noClass = { role: "", subnetSelectorTerms: [], securityGroupSelectorTerms: [] };
    for (const nodes of [{ enabled: false, nodeClass: noClass }, { nodeClass: { ...noClass, create: false } }]) {
      const ok = renderChart(base({ processRuns: { nodes } }));
      expect(ok.ok, ok.stderr).toBe(true);
    }
    const off = renderChart(base({ processRuns: { clusterObjects: false, nodes: { nodeClass: noClass } } }));
    expect(off.ok, off.stderr).toBe(true);
  });

  it("cross-field rules name their key", () => {
    const gpu = {
      gpuResource: "nvidia.com/gpu",
      extraTaints: [{ key: "nvidia.com/gpu", effect: "NoSchedule" }],
      quota: { cpu: 16, memoryGi: 64, gpus: 2 },
      nodePool: { requirements: [{ key: "eks.amazonaws.com/instance-gpu-name", operator: "In", values: ["l4"] }] },
    };
    const fam = (f: object) => ({ processRuns: { families: f } });
    const cases: [object, string, Partial<NonNullable<Opts>>?][] = [
      [{ processRuns: { namespace: "stac-higher" } }, "processRuns.namespace"],
      [{ processRuns: { defaultFamily: "nope" } }, "processRuns.defaultFamily"],
      [{ processRuns: { defaultFamily: "gpu-l4", families: { "gpu-l4": gpu } } }, "processRuns.defaultFamily"],
      [{ processRuns: { families: { "cpu-default": { enabled: false } } } }, "processRuns.defaultFamily"],
      [
        { processRuns: { nodes: { enabled: false }, families: { "cpu-default": { dedicatedNodes: false }, other: { quota: { cpu: 1, memoryGi: 1 } } } } },
        "processRuns.families.cpu-default.dedicatedNodes",
      ],
      [fam({ "cpu-default": { dedicatedNodes: false } }), "processRuns.families.cpu-default.dedicatedNodes"],
      [fam({ other: { quota: { cpu: 1, memoryGi: 1 } } }), "processRuns.families.other.nodePool.requirements"],
      [fam({ other: { nodePool: { requirements: [] } } }), "processRuns.families.other.quota"],
      [{ processRuns: { nodes: { terminationGracePeriodHours: 24 } } }, "processRuns.nodes.terminationGracePeriodHours"],
      [{ processRuns: { nodes: { expireAfterHours: 480, terminationGracePeriodHours: 25 } } }, "processRuns.nodes.expireAfterHours"],
      [
        fam({ "cpu-default": { nodePool: { requirements: [{ key: "stac-higher.io/flavor", operator: "In", values: ["x"] }] } } }),
        "processRuns.families.cpu-default.nodePool.requirements",
      ],
      [
        fam({ "cpu-default": { nodePool: { requirements: [{ key: "karpenter.sh/nodepool", operator: "In", values: ["x"] }] } } }),
        "processRuns.families.cpu-default.nodePool.requirements",
      ],
      [fam({ "cpu-default": { extraTaints: [{ key: "stac-higher.io/runs", effect: "NoSchedule" }] } }), "processRuns.families.cpu-default.extraTaints"],
      [fam({ "cpu-default": { gpuResource: "nvidia.com/gpu" } }), "processRuns.defaultFamily"],
      [fam({ "cpu-default": { quota: { cpu: 16, memoryGi: 64, gpus: 2 } } }), "processRuns.families.cpu-default.quota.gpus"],
      [{ processRuns: { network: { dns: [{ ipBlock: { cidr: "10.100.0.0/16" } }] } } }, "processRuns.network.dns"],
      [{ processRuns: { network: { dns: [{ ipBlock: { cidr: "fd00::a/64" } }] } } }, "processRuns.network.dns"],
      [{ aws: { s3PrefixListCidrs: ["169.254.0.0/16"] } }, "aws.s3PrefixListCidrs"],
      [{ aws: { s3PrefixListCidrs: ["0.0.0.0/0"] } }, "aws.s3PrefixListCidrs"],
      [{ aws: { s3PrefixListCidrs: ["169.0.0.0/8"] } }, "aws.s3PrefixListCidrs"],
      [{ aws: { s3PrefixListCidrs: ["10.0.0.0/7"] } }, "aws.s3PrefixListCidrs"],
      [{ aws: { s3PrefixListCidrs: ["2600:1f18::/32"] } }, "aws.s3PrefixListCidrs"],
      [{ aws: { s3PrefixListCidrs: ["300.1.1.1/24"] } }, "aws.s3PrefixListCidrs"],
      [{ processRuns: { network: { objectStore: [{ ipBlock: { cidr: "169.254.169.254/32" } }] } } }, "processRuns.network.objectStore"],
      [{ processRuns: { network: { scannerEgressExcept: ["not-a-cidr"] } } }, "processRuns.network.scannerEgressExcept"],
    ];
    for (const [values, key, extra] of cases) failsNaming(base(values, extra), key);
  });

  it("a kube-dns style DNS peer and an IPv6 /128 are accepted", () => {
    const kubeDns = {
      namespaceSelector: { matchLabels: { "kubernetes.io/metadata.name": "kube-system" } },
      podSelector: { matchLabels: { "k8s-app": "kube-dns" } },
    };
    for (const dns of [[kubeDns], [{ ipBlock: { cidr: "fd00::a/128" } }]]) {
      const r = renderChart(base({ processRuns: { network: { dns } } }));
      expect(r.ok, r.stderr).toBe(true);
    }
  });
});

type Doc = { apiVersion: string; kind: string; metadata: { name: string; namespace?: string; labels?: Record<string, string>; annotations?: Record<string, string> }; [k: string]: any };
const render = (values: object = {}, name = "base"): Doc[] => {
  const r = renderChart({ valuesFiles: scenario(name), values });
  expect(r.ok, r.stderr).toBe(true);
  return r.docs as Doc[];
};
const inRuns = (docs: Doc[], ns = FIXTURE.namespace) => docs.filter((d) => d.metadata.namespace === ns);
const key = (d: Doc) => `${d.kind}/${d.metadata.namespace ?? ""}/${d.metadata.name}`;
/** The rendered counterparts of infra/kubernetes/*.yaml (A-R1). */
const K8S_KINDS = ["Namespace", "ServiceAccount", "Role", "RoleBinding", "NetworkPolicy"];
const isRunsK8sObject = (d: Doc) =>
  (d.kind === "Namespace" && d.metadata.name === FIXTURE.namespace) ||
  (K8S_KINDS.includes(d.kind) && d.metadata.namespace === FIXTURE.namespace &&
    (d.kind !== "NetworkPolicy" || d.metadata.name === FIXTURE.defaultDenyPolicy));

describe.skipIf(!helmReady())("runs plane: namespace and RBAC", () => {
  it("RBAC and isolation equal infra/kubernetes", () => {
    const want = loadYamlDir(join(REPO_DIR, "infra/kubernetes")) as Doc[];
    const got = render().filter(isRunsK8sObject).map(stripChartMetadata) as Doc[];
    const byKey = (docs: Doc[]) => Object.fromEntries(docs.map((d) => [key(d), d]));
    expect(byKey(got)).toEqual(byKey(want));
    expect(got).toHaveLength(want.length);
  });

  it("the runs namespace carries K-5's Pod Security labels and the chart's", () => {
    const ns = render().find((d) => d.kind === "Namespace")!;
    expect(ns.metadata.labels).toMatchObject({
      "pod-security.kubernetes.io/enforce": "restricted",
      "pod-security.kubernetes.io/enforce-version": "latest",
      "app.kubernetes.io/component": "process-runs",
    });
    expect(ns.metadata.annotations).toBeUndefined();
  });

  it.each(["base", "irsa"])("the run ServiceAccount never gets a cloud identity (%s)", (name) => {
    const sas = inRuns(render({}, name)).filter((d) => d.kind === "ServiceAccount");
    expect(sas.map((d) => d.metadata.name)).toEqual([FIXTURE.runServiceAccount]);
    expect(sas[0].metadata.annotations).toBeUndefined();
    expect(sas[0].automountServiceAccountToken).toBe(false);
  });

  it("the Role is namespaced and grants nothing cluster-wide", () => {
    const docs = render();
    expect(docs.filter((d) => d.kind === "ClusterRole" || d.kind === "ClusterRoleBinding")).toEqual([]);
    const roles = docs.filter((d) => d.kind === "Role" || d.kind === "RoleBinding");
    expect(roles.map((d) => [d.kind, d.metadata.name, d.metadata.namespace])).toEqual([
      ["Role", FIXTURE.pipelineRole, FIXTURE.namespace],
      ["RoleBinding", FIXTURE.pipelineRole, FIXTURE.namespace],
    ]);
  });

  it("names follow the fixture and the values", () => {
    const docs = render({ processRuns: { namespace: "other-runs" }, identity: { serviceAccounts: { pipeline: "p2" } } });
    expect(docs.find((d) => d.kind === "Namespace")!.metadata.name).toBe("other-runs");
    for (const kind of ["ServiceAccount", "Role", "RoleBinding"]) {
      const runs = docs.filter((d) => d.kind === kind && d.metadata.namespace === "other-runs");
      expect(runs.length, kind).toBeGreaterThan(0);
    }
    expect(docs.some((d) => d.metadata.namespace === FIXTURE.namespace)).toBe(false);
    const rb = docs.find((d) => d.kind === "RoleBinding")!;
    expect(rb.subjects).toEqual([{ kind: "ServiceAccount", name: "p2", namespace: "stac-higher" }]);
    expect(rb.roleRef).toEqual({ apiGroup: "rbac.authorization.k8s.io", kind: "Role", name: FIXTURE.pipelineRole });
  });

  it("createNamespace: false renders no Namespace", () => {
    const docs = render({ processRuns: { createNamespace: false } });
    expect(docs.filter((d) => d.kind === "Namespace")).toEqual([]);
    expect(inRuns(docs).some((d) => d.kind === "Role")).toBe(true);
  });
});
