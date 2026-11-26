// @vitest-environment node
// R-8 (#144): the process-runs plane in the runs namespace — values contract, namespace and RBAC,
// NetworkPolicies. Plan docs/superpowers/plans/2026-11-26-r8-chart-process-runs.md (and its addendum).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  CHART_DIR,
  REPO_DIR,
  helmReady,
  ipBlockReaches,
  ipv4Contains,
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

/** The render fails with a `fail` message that starts with the key path (directly or through an include). */
function failsNaming(opts: Opts, key: string) {
  const r = renderChart(opts);
  expect(r.ok, `expected a failure naming ${key}`).toBe(false);
  const k = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  expect(r.stderr, key).toMatch(new RegExp(`(\\): |error calling fail: )${k}`));
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
      [{ processRuns: { network: { scannerEgressExcept: ["300.0.0.0/24"] } } }, "processRuns.network.scannerEgressExcept"],
    ];
    for (const [values, key, extra] of cases) failsNaming(base(values, extra), key);
  });

  it("a kube-dns style DNS peer is accepted; IPv6 fails closed", () => {
    const kubeDns = {
      namespaceSelector: { matchLabels: { "kubernetes.io/metadata.name": "kube-system" } },
      podSelector: { matchLabels: { "k8s-app": "kube-dns" } },
    };
    const r = renderChart(base({ processRuns: { network: { dns: [kubeDns] } } }));
    expect(r.ok, r.stderr).toBe(true);
    failsNaming(base({ processRuns: { network: { dns: [{ ipBlock: { cidr: "fd00::a/128" } }] } } }), "processRuns.network.dns");
  });

  it("private, link-local and over-wide CIDRs are refused where they are not named as private (lead ruling)", () => {
    const net = (n: object) => base({ processRuns: { network: n } });
    for (const [opts, key] of [
      [base({ aws: { s3PrefixListCidrs: ["10.1.0.0/16"] } }), "aws.s3PrefixListCidrs"],
      [base({ aws: { s3PrefixListCidrs: ["100.64.0.0/24"] } }), "aws.s3PrefixListCidrs"],
      [base({ aws: { s3PrefixListCidrs: ["52.0.0.0/8"] } }), "aws.s3PrefixListCidrs"],
      [base({ aws: { s3PrefixListCidrs: ["127.0.0.1/32"] } }), "aws.s3PrefixListCidrs"],
      [net({ objectStore: [{ ipBlock: { cidr: "10.0.0.0/16" } }] }), "processRuns.network.objectStore"],
      [net({ objectStore: [{ ipBlock: { cidr: "0.0.0.0/0" } }] }), "processRuns.network.objectStore"],
      [net({ scannerPrivateCidrs: ["10.0.0.0/16"] }), "processRuns.network.scannerPrivateCidrs"],
      [net({ scannerPrivateCidrs: ["169.254.169.0/24"] }), "processRuns.network.scannerPrivateCidrs"],
      [net({ scannerPrivateCidrs: ["300.0.0.0/24"] }), "processRuns.network.scannerPrivateCidrs"],
      [net({ dns: [{ ipBlock: { cidr: "169.254.169.254/32" } }] }), "processRuns.network.dns"],
    ] as const) {
      failsNaming(opts, key);
    }
    const schema = renderChart(net({ scannerPrivateCidrs: ["not-a-cidr"] }));
    expect(schema.ok).toBe(false);
    expect(schema.stderr).toContain("scannerPrivateCidrs");
    for (const n of [{ objectStore: [{ ipBlock: { cidr: "10.0.5.0/24" } }] }, { scannerPrivateCidrs: ["10.0.5.16/28"] }]) {
      const ok = renderChart(net(n));
      expect(ok.ok, ok.stderr).toBe(true);
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

/** A TS port of kubernetes_manifests.isolation_problem (K-5): null when the policy isolates every run pod. */
function isolationProblem(policy: Doc | undefined): string | null {
  if (!policy) return "does not exist";
  const spec = policy.spec;
  if (typeof spec !== "object" || spec === null || Array.isArray(spec)) return "has no spec";
  const sel = spec.podSelector;
  if (typeof sel !== "object" || sel === null || Array.isArray(sel) || Object.keys(sel).length !== 0) return "must select every pod";
  const types = Array.isArray(spec.policyTypes) ? spec.policyTypes : [];
  if (!types.includes("Ingress") || !types.includes("Egress")) return "must list Ingress and Egress";
  if ((spec.ingress?.length ?? 0) > 0 || (spec.egress?.length ?? 0) > 0) return "allows traffic";
  return null;
}

const runsPolicies = (docs: Doc[]) => inRuns(docs).filter((d) => d.kind === "NetworkPolicy");
const policy = (docs: Doc[], name: string) => runsPolicies(docs).find((d) => d.metadata.name === name)!;
const DNS_PORTS = [{ protocol: "UDP", port: 53 }, { protocol: "TCP", port: 53 }];
const LINK_LOCAL = ["169.254.169.254", "169.254.170.23"];
/** Fixed in the template (lead ruling): link-local, RFC 1918, CGNAT. */
const SCANNER_EXCEPT = ["169.254.0.0/16", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10"];
const NON_PUBLIC = SCANNER_EXCEPT;

describe.skipIf(!helmReady())("runs plane: NetworkPolicies", () => {
  it("the default-deny policy is exactly what the executor checks", () => {
    const deny = policy(render(), FIXTURE.defaultDenyPolicy);
    expect(isolationProblem(deny)).toBeNull();
    expect(Object.keys(deny.spec).sort()).toEqual(["podSelector", "policyTypes"]);
    // The port itself is checked against the executor's failure modes.
    expect(isolationProblem({ ...deny, spec: { ...deny.spec, podSelector: { matchLabels: { a: "b" } } } })).not.toBeNull();
    expect(isolationProblem({ ...deny, spec: { ...deny.spec, egress: [{}] } })).not.toBeNull();
  });

  it("no other policy can select run pods", () => {
    for (const values of [{}, { networkPolicy: { enabled: false } }]) {
      const pols = runsPolicies(render(values));
      expect(pols.map((p) => p.metadata.name).sort()).toEqual(
        [FIXTURE.defaultDenyPolicy, ...Object.keys(FIXTURE.allowPolicies)].sort(),
      );
      for (const [name, labels] of Object.entries(FIXTURE.allowPolicies)) {
        const p = pols.find((x) => x.metadata.name === name)!;
        expect(p.spec.podSelector).toEqual({ matchLabels: labels });
        expect(p.spec.policyTypes).toEqual(["Egress"]);
      }
      for (const p of pols) expect(p.spec.ingress, p.metadata.name).toBeUndefined();
    }
  });

  it("isolated runs reach DNS and the object store only", () => {
    const minio = { podSelector: { matchLabels: { app: "minio" } }, namespaceSelector: { matchLabels: { "kubernetes.io/metadata.name": "storage" } } };
    const docs = render({
      aws: { s3PrefixListCidrs: ["192.0.2.0/24", "198.51.100.0/24"] },
      processRuns: { network: { objectStore: [minio], objectStorePorts: [443, 9000] } },
    });
    const egress = policy(docs, "stac-higher-runs-process-isolated").spec.egress;
    expect(egress).toEqual([
      { to: [{ ipBlock: { cidr: "10.100.0.10/32" } }], ports: DNS_PORTS },
      {
        to: [{ ipBlock: { cidr: "192.0.2.0/24" } }, { ipBlock: { cidr: "198.51.100.0/24" } }, minio],
        ports: [{ protocol: "TCP", port: 443 }, { protocol: "TCP", port: 9000 }],
      },
    ]);
  });

  it("scans also get 443, never link-local, RFC 1918 or CGNAT", () => {
    const docs = render({ processRuns: { network: { scannerEgressExcept: ["203.0.113.0/24"] } } });
    const egress = policy(docs, "stac-higher-runs-image-scan").spec.egress;
    expect(egress.slice(0, 2)).toEqual(policy(docs, "stac-higher-runs-process-isolated").spec.egress);
    expect(egress[2]).toEqual({
      to: [{ ipBlock: { cidr: "0.0.0.0/0", except: [...SCANNER_EXCEPT, "203.0.113.0/24"] } }],
      ports: [{ protocol: "TCP", port: 443 }],
    });
    expect(egress).toHaveLength(3);
  });

  it("the scanner's except always holds the five ranges, whatever the optional lists say", () => {
    const docs = render({ processRuns: { network: { scannerEgressExcept: [], scannerPrivateCidrs: [] } } });
    const rule = policy(docs, "stac-higher-runs-image-scan").spec.egress[2];
    expect(rule.to).toEqual([{ ipBlock: { cidr: "0.0.0.0/0", except: SCANNER_EXCEPT } }]);
  });

  it("a named private destination renders as its own peer on 443", () => {
    const docs = render({ processRuns: { network: { scannerPrivateCidrs: ["10.0.5.16/28", "10.0.6.0/24"] } } });
    const rule = policy(docs, "stac-higher-runs-image-scan").spec.egress[2];
    expect(rule.to).toEqual([
      { ipBlock: { cidr: "0.0.0.0/0", except: SCANNER_EXCEPT } },
      { ipBlock: { cidr: "10.0.5.16/28" } },
      { ipBlock: { cidr: "10.0.6.0/24" } },
    ]);
    expect(rule.ports).toEqual([{ protocol: "TCP", port: 443 }]);
    expect(policy(docs, "stac-higher-runs-process-isolated").spec.egress.flatMap((r: any) => r.to)).not.toContainEqual({ ipBlock: { cidr: "10.0.5.16/28" } });
  });

  it.each([
    ["base", {}],
    ["r5", {}],
    ["example", {}],
  ])("no runs policy reaches link-local or unnamed private space (%s)", (name) => {
    const r =
      name === "example"
        ? renderChart({ valuesFiles: [join(CHART_DIR, "../../examples/aws-eks/values.yaml")] })
        : renderChart({ valuesFiles: name === "r5" ? [join(CHART_DIR, "tests/values/r5-helm-values.yaml"), join(CHART_DIR, "tests/values/r5-deployment.yaml")] : scenario(name) });
    expect(r.ok, r.stderr).toBe(true);
    const blocks = runsPolicies(r.docs as Doc[]).flatMap((p) => (p.spec.egress ?? []).flatMap((e: any) => e.to ?? []))
      .filter((peer: any) => peer.ipBlock)
      .map((peer: any) => peer.ipBlock);
    expect(blocks.length).toBeGreaterThan(0);
    for (const b of blocks) {
      expect(b.cidr, "IPv6 ipBlock").not.toContain(":");
      for (const ip of LINK_LOCAL) {
        const open = ipv4Contains(b.cidr, ip) && !(b.except ?? []).some((e: string) => ipv4Contains(e, ip));
        expect(open, `${b.cidr} reaches ${ip}`).toBe(false);
      }
    }
    // Without a named private peer, no non-DNS rule admits private space; DNS rules are single addresses on 53.
    for (const p of runsPolicies(r.docs as Doc[])) {
      for (const rule of p.spec.egress ?? []) {
        const isDns = rule.ports.every((x: any) => x.port === 53);
        for (const peer of rule.to ?? []) {
          if (!peer.ipBlock) continue;
          if (isDns) {
            expect(peer.ipBlock.cidr, `${p.metadata.name} DNS peer`).toMatch(/\/32$/);
            continue;
          }
          for (const range of NON_PUBLIC) {
            expect(ipBlockReaches(peer.ipBlock, range), `${p.metadata.name}: ${peer.ipBlock.cidr} reaches ${range}`).toBe(false);
          }
        }
      }
    }
  });

  it("a kube-dns style DNS peer renders unchanged", () => {
    const kubeDns = {
      namespaceSelector: { matchLabels: { "kubernetes.io/metadata.name": "kube-system" } },
      podSelector: { matchLabels: { "k8s-app": "kube-dns" } },
    };
    const docs = render({ processRuns: { network: { dns: [kubeDns] } } });
    expect(policy(docs, "stac-higher-runs-process-isolated").spec.egress[0]).toEqual({ to: [kubeDns], ports: DNS_PORTS });
  });
});
