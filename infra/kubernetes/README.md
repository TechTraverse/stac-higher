# Runs namespace: RBAC for the Kubernetes executor (K-5)

The objects the pipeline's Kubernetes executor (`PROCESS_EXECUTOR=kubernetes`,
`services/pipeline/src/pipeline/process/kubernetes_executor.py`) needs in the
namespace where every process run and image scan runs as a Job. Process-compute
spec §6.3; tracking issue #13.

| File | Object |
|---|---|
| `namespace.yaml` | Namespace `stac-higher-runs`, Pod Security `restricted` enforced |
| `run-serviceaccount.yaml` | ServiceAccount `stac-higher-run`: every run pod's identity; no token, no permissions |
| `pipeline-role.yaml` | Role `stac-higher-pipeline`: exactly the verbs the executor calls (the file's comment lists why each exists) |
| `pipeline-rolebinding.yaml` | Binds the Role to the chart's pipeline ServiceAccount `stac-higher-pipeline` in `stac-higher` |
| `runs-default-deny.yaml` | NetworkPolicy `stac-higher-runs-default-deny`: every run pod, no ingress, no egress (K-6; the executor's isolation precondition) |

`kubectl apply -f infra/kubernetes/` creates those five objects and nothing
else: no allow policy, LocalQueue, Kueue object or admission policy. The
Kueue objects and the admission policy are K-6's (`infra/kueue/`); the allow
policies are R-8's (#144); what each must provide is below. K-6's kind leg
(`.github/workflows/kind.yml`) applies this directory to a kind cluster
as-is.

## Rules

- **The Role is proven both ways.** The pipeline's unit tests run against a
  fake API server that refuses anything `pipeline-role.yaml` does not grant
  (sufficiency), and `tests/test_kubernetes_rbac.py` fails if one executor
  lifecycle leaves a grant unused (necessity). Change the Role and the code
  together.
- **The namespace holds nothing else.** No Secrets but the runs' own (the
  executor creates and deletes them), no other ServiceAccount, and no
  RoleBinding granting anything in it except the pipeline's. The pipeline's
  `jobs create` could otherwise reach them (`docs/ISSUES.md` I-184).
- **The run ServiceAccount never gets a Pod Identity association or an IRSA
  (`eks.amazonaws.com/role-arn`) annotation.** A run's only cloud identity is
  its run-scoped STS credential (ADR 0013).
- **No run starts without the default-deny policy.** The executor reads the
  NetworkPolicy `stac-higher-runs-default-deny` (the Role's only
  `networkpolicies` grant is `get`) and refuses to start runs unless its
  `podSelector` is exactly `{}`, it lists `Ingress` and `Egress`, and it
  allows nothing. That proves the object, not that the CNI enforces it, and
  it cannot see a second policy that allows more (I-183).
- **The settings must agree:** `PROCESS_K8S_NAMESPACE` (default
  `stac-higher-runs`) and `PROCESS_K8S_RUN_SERVICE_ACCOUNT` (default
  `stac-higher-run`). For a release in another namespace, edit the
  RoleBinding's subject.

## What R-8 (#144) renders

- **These five objects**, from the chart's templates (Helm cannot read files
  outside the chart), with a drift test against these files.
- **The default-deny policy**, `runs-default-deny.yaml`, exactly (K-6
  ships the file; `test_kueue_manifests.py` proves it passes the executor's
  own check).

- **The label contract.** Every run pod carries
  `stac-higher.run-kind` ∈ {`process`, `image_scan`} and
  `stac-higher.network-level` ∈ {`isolated`, `scanner`} (a process run:
  its revision's level, of which the executor runs only `isolated`; a scan:
  `scanner`). Allow policies are additive and select on BOTH labels:
  `run-kind=process` + `network-level=isolated` → DNS and the object store
  (`aws.s3PrefixListCidrs`); `run-kind=image_scan` + `network-level=scanner`
  → DNS, the object store and 443. A drift test pins that every allow policy
  in the runs namespace is one of those two, selecting on both labels.
- **IMDS.** Block `169.254.169.254` from run pods (no allow policy may cover
  it) and set the nodes' instance-metadata hop limit to 1.
- **Node egress to user registries.** User images are pulled from their own
  registry, never through the mirror (`PROCESS_K8S_REGISTRY_MIRRORS` covers
  platform images only); nodes need egress to those registries, or user
  images are not supported on that cluster (I-183).
- **Node PID limit.** Set the kubelet's `podPidsLimit`; runs have no per-pod
  PID limit of their own (I-180).

## The admission policy K-6 (#14) renders

A ValidatingAdmissionPolicy (and binding) for the runs namespace, bounding
what the pipeline's identity can create (I-184). `tests/test_kubernetes_rbac.py`
encodes the same rules and checks every Job the executor builds against them.

- **Job CREATE:** the `kueue.x-k8s.io/queue-name` label is present; the
  `kueue.x-k8s.io/priority-class` label is `interactive` or `triggered`; the
  pod template's `serviceAccountName` is `stac-higher-run` and
  `automountServiceAccountToken` is `false`; every volume is an `emptyDir`;
  `imagePullSecrets` name only `<job name>-pull`; no `initContainers` or
  `ephemeralContainers`; no container `env`; `envFrom` is only a `secretRef`
  naming the Job's own name.
- **Pod UPDATE by the pipeline's ServiceAccount:** only
  `spec.activeDeadlineSeconds` may change, and only downwards.
- A refusal answers 422 (`Invalid`), which the executor turns into a dead
  run naming the reason; so does an admission webhook's 403 denial (its
  message says "admission webhook … denied the request"). An RBAC 403 is a
  deployment fault and requeues.

## What a real cluster must prove (K-6's kind leg; R-11 on EKS)

No real API server has run the executor; these come first, in order
(`docs/ISSUES.md` I-178).

1. TLS to the API server with the mounted CA and a
   `kubectl create token stac-higher-pipeline -n stac-higher` bearer; the Role
   under the real authorizer (one full run lifecycle).
2. **Kueue preemption leaves the Job un-failed.** A `backoffLimit: 0` Job
   with `podFailurePolicy: FailJob on DisruptionTarget`, suspended by Kueue:
   the pods the Job controller deletes must not count as failures nor carry
   `DisruptionTarget`, or every preemption fails the Job or reads `lost`.
   Also: the Workload's `Evicted` condition is cleared (status `False`) on
   readmission.
3. **A deadline kill is not a disruption.** A pod killed by
   `activeDeadlineSeconds` must end `Failed` / `DeadlineExceeded` without a
   `DisruptionTarget` condition, or every timeout and cancel becomes a free,
   endless requeue.
4. **The log survives the stop.** After `cancel()`'s patch (deadline 1 s) the
   pod ends within about 10 s and `pods/log` still serves its output; the
   same for a run past its timeout. If not, the fallback is to read logs
   before the stop and replace `KubernetesExecutor._stop_keeping_the_log`
   with a foreground Job delete.
5. **The HTTP status of a Kueue job-webhook denial** (403 `Denied()` or 422
   field errors), and that the executor's mapping makes it a dead run.
6. **`waitForPodsReady`'s timeout stays above `START_FAILURE_GRACE_SECONDS`
   (300 s)**, or Kueue evicts a pod still pulling before the executor would
   have failed it.
7. `pods/log` with `Accept: application/json` serves `text/plain`.
8. A foreground delete leaves `deletionTimestamp` on the Job for a while
   (read as `lost`); Kueue creates the Workload asynchronously (read as
   waiting).
9. A watch survives `timeoutSeconds`, receives bookmarks, and a 410 after
   compaction triggers a RESYNC.
10. Pod Security `restricted` and the admission policy admit every Job the
    executor builds; `interactive` preempting `triggered` reads
    `pending_capacity` with no attempt spent.
11. **Network policy is enforced, not just present.** First prove the CNI
    enforces NetworkPolicy (kind's kindnet does only in recent versions; on
    EKS the VPC CNI's network policy must be enabled), then prove a run
    pod's connections to `169.254.169.254` and to an external host fail
    while the object store answers.
