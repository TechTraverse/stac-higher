# Kueue objects for process runs (K-6)

What every Kubernetes deployment of the platform needs from Kueue, and the
kind CI cluster's capacity. Process-compute spec §7; ADR 0019; tracking
issue #14. The runs namespace's own objects (namespace, run ServiceAccount,
the pipeline's Role and binding, the default-deny NetworkPolicy) are K-5's,
in `infra/kubernetes/`.

| Path | What |
|---|---|
| `kueue.env` | The Kueue release (`KUEUE_VERSION`) and the sha256 of its `manifests.yaml`: the one pin. |
| `config/production.yaml` | The controller configuration every cluster installs: Kueue's default plus `waitForPodsReady` (10 m, three requeues, then deactivation), `integrations.frameworks: [batch/job]`, `manageJobsWithoutQueueName: false`. |
| `config/kind.yaml` | The kind CI variant: identical except `waitForPodsReady` timing (30 s, one requeue, 5–10 s backoff). Never install it elsewhere. |
| `common/workloadpriorityclasses.yaml` | `interactive` (1000) and `triggered` (100). |
| `common/localqueue.yaml` | LocalQueue `process-runs` in `stac-higher-runs`, feeding ClusterQueue `process-runs`. |
| `common/admission-policy.yaml` | Two ValidatingAdmissionPolicies (and bindings) on the runs namespace: what a Job must look like, and that the pipeline may change a pod only by lowering its deadline (below). |
| `kind/resourceflavors.yaml` | The kind cluster's one flavor, `cpu-default` (no node labels). |
| `kind/clusterqueue.yaml` | The kind cluster's ClusterQueue: 1 CPU, 4 Gi, only the runs namespace, `LowerPriority` preemption, `BestEffortFIFO`. |

`common/` is deployment-independent except the runs namespace and, in the
admission policy, the run ServiceAccount (`stac-higher-run`) and the
pipeline's identity (`system:serviceaccount:stac-higher:stac-higher-pipeline`,
written once, in the pod-update policy's match condition). One object per
YAML document; object names are stable. `kind/` holds only the kind cluster's
capacity.

## Installing on a cluster

1. Kueue: `manifests.yaml` for `KUEUE_VERSION`, checked against
   `KUEUE_MANIFESTS_SHA256`, by `kubectl apply --server-side`.
2. Its configuration: ConfigMap `kueue-manager-config` in `kueue-system`,
   key `controller_manager_config.yaml` = `config/production.yaml`, then
   `kubectl -n kueue-system rollout restart deploy/kueue-controller-manager`.
3. `infra/kubernetes/` (the namespace first).
4. `common/` (retry while Kueue's webhook comes up).
5. The deployment's ResourceFlavors and ClusterQueue: quota equal to the node
   pool's ceiling (spec §7.3, I-92); a GPU flavor's `nodeLabels` equal its
   profiles' `backend.kubernetes.node_selector` (spec §3.4). A flavor's
   `tolerations` are injected into the pods Kueue admits on it (the kind leg
   proves it), which is how a tainted runs node takes image scans that carry
   no toleration of their own.

`scripts/kind-up.sh` is that order on kind, with `config/kind.yaml` and
`kind/`.

## The admission policy

`stac-higher-runs-jobs` (Job CREATE; the two label rules also on UPDATE): the
queue label; priority `interactive` or `triggered`; the pod template's
`stac-higher.run-kind` / `stac-higher.network-level` pair `(process, isolated)`,
or `(image_scan, scanner)` for a `stac-scan-` Job, repeated from the Job's
own labels with its run id; ServiceAccount `stac-higher-run` without a token;
`emptyDir` volumes only; pull Secrets named `<job>-pull` only; no init or
ephemeral containers; no `env`; `envFrom` only the Secret named after the
Job. `stac-higher-runs-pod-updates` (Pod UPDATE by the pipeline only):
`activeDeadlineSeconds` set or lowered, never raised or removed; labels,
annotations, owners, finalizers and every mutable spec field unchanged.

Every refusal is `reason: Invalid` (HTTP 422): the executor turns it into a
dead run naming the refusal, never a silent requeue. Every message starts
with a rule id; `services/pipeline/tests/_admission_rules.py` maps each to
K-5's pure twin of the rule (`tests/test_kubernetes_rbac.py`) and to a
violation, `test_kueue_manifests.py` fails when the two drift, and the kind
leg commits each violation against a real API server. The policy narrows
what a compromised pipeline identity can create; it cannot authenticate
intent (`docs/ISSUES.md` I-184).

## How R-8 (#144) reuses this

Helm cannot read files outside the chart, so R-8 renders: `common/` as
templates with only the namespace and the two identities parameterised, and
a drift test that the default render equals these files object for object;
ResourceFlavors and the ClusterQueue from values (quota = NodePool limits),
with a drift test that kind-equivalent values reproduce `kind/`. Kueue
itself is a cluster prerequisite pinned by `kueue.env` and
`config/production.yaml`. `scripts/check-kueue.sh --schemas-only <dir>`
writes the pinned release's v1beta2 JSON schemas for kubeconform.

## Kueue's pod webhook

Kueue registers `mpod.kb.io` (failurePolicy `Fail`, every namespace but
`kube-system` and `kueue-system`) even with the pod integration disabled (it
answers no-op), so no pod anywhere can be created while Kueue's webhook is
down (`docs/ISSUES.md`).

## Bumping Kueue

Change both lines of `kueue.env` together (re-hash the new
`manifests.yaml`), read the release's "Actions Required", diff its default
configuration against `config/production.yaml`, run
`scripts/check-kueue.sh`, and let the kind leg (`.github/workflows/kind.yml`)
run on the PR. A new minor is a plan change, not a bump (K6-R1).
