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

`kubectl apply -f infra/kubernetes/` creates those four objects and nothing
else: no NetworkPolicy, LocalQueue or Kueue object. Those are R-8's (#144) and
K-6's (#14).

## Rules

- **The Role is proven both ways.** The pipeline's unit tests run against a
  fake API server that refuses anything `pipeline-role.yaml` does not grant
  (sufficiency), and `tests/test_kubernetes_rbac.py` fails if one executor
  lifecycle leaves a grant unused (necessity). Change the Role and the code
  together.
- **The run ServiceAccount never gets a Pod Identity association or an IRSA
  (`eks.amazonaws.com/role-arn`) annotation.** A run's only cloud identity is
  its run-scoped STS credential (ADR 0013).
- **No run starts without isolation.** The executor reads the NetworkPolicy
  `stac-higher-runs-default-deny` in this namespace (the Role's only
  `networkpolicies` grant is `get`) and refuses to start runs unless it selects
  every run pod, lists `Ingress` and `Egress`, and allows nothing. R-8 renders
  it with the allow policies.
- **The settings must agree:** `PROCESS_K8S_NAMESPACE` (default
  `stac-higher-runs`) and `PROCESS_K8S_RUN_SERVICE_ACCOUNT` (default
  `stac-higher-run`). For a release in another namespace, edit the
  RoleBinding's subject.

## Who uses these files

- **R-8 (#144)** renders the same four objects from the chart's templates
  (Helm cannot read files outside the chart) and keeps a drift test against
  these files.
- **K-6 (#14)** applies this directory to kind as-is and mints the test token
  with `kubectl create token stac-higher-pipeline -n stac-higher`.
