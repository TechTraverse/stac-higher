# identity

Per-workload IAM roles (app, pipeline, titiler, cube-server), the process-run role, and the way pods obtain them: EKS Pod Identity associations or IRSA trust, behind one toggle. The composition (R-5) passes the role ARNs, namespace and ServiceAccount names to the chart through `helm_values.identity`.

## Roles and permissions

All S3 grants are on the platform bucket; each role with S3 access also gets the KMS actions it needs, conditioned on `kms:ViaService = s3.<region>.<dns_suffix>` (the bucket is SSE-KMS with the platform key).

| Role | S3 | KMS |
|---|---|---|
| `<prefix>-app` | `GetObject` on `bucket/*`; `PutObject` on `bucket/staging/*` and `bucket/assets/*` (presigning only; the app never streams bytes) | `Decrypt`, `GenerateDataKey` |
| `<prefix>-pipeline` | `GetObject`, `PutObject`, `DeleteObject`, `AbortMultipartUpload` on `bucket/*`; `ListBucket` on the bucket; `sts:AssumeRole` + `sts:TagSession` on the run role | `Decrypt`, `GenerateDataKey` |
| `<prefix>-titiler` | `GetObject` on `bucket/assets/*`; `ListBucket` with `s3:prefix` `assets/*` | `Decrypt` |
| `<prefix>-cube-server` | `GetObject` on `bucket/assets/*/_cube/*`; `ListBucket` with `s3:prefix` `assets/*/_cube/*` | `Decrypt` |
| `<prefix>-process-run` | see below | `Decrypt`, `GenerateDataKey` |

Role ARNs are built in locals (`arn:<partition>:iam::<account>:role/<name>`), so they are known at plan.

## Workload identity toggle

`workload_identity` is `"pod_identity"` (default) or `"irsa"`.

- `pod_identity` needs `cluster_name`. Trust is the documented EKS policy: principal `pods.eks.<dns_suffix>`, actions `sts:AssumeRole` and `sts:TagSession`, no extra conditions. One `aws_eks_pod_identity_association` per workload binds the role to `namespace` / `service_accounts`.
- `irsa` needs `oidc_provider_arn`. The issuer host is derived from it. Trust is `sts:AssumeRoleWithWebIdentity` with `StringEquals` on `<host>:sub` (`system:serviceaccount:<namespace>:<sa>`) and `<host>:aud` (`sts.<dns_suffix>`). No associations are created; the chart annotates each ServiceAccount with the role ARN.

A missing input fails the plan (preconditions on the roles), not the apply.

The namespace and ServiceAccount names must match the chart: pass them through `helm_values.identity`. A role bound to a name the chart never renders gives the pod no credentials, and the failure shows only as S3 AccessDenied.

The deploying principal needs `iam:PassRole` and `eks:CreatePodIdentityAssociation` to create the associations.

## Process-run role and the session policy

The run role's identity policy is the ceiling that the per-run session policy narrows. It mirrors `session_policy()` in `services/pipeline/src/pipeline/process/credentials.py` and the prefixes in `services/pipeline/src/pipeline/storage/keys.py`; change both together.

- `RunWritablePrefixes`: `PutObject`, `GetObject`, `DeleteObject` on `bucket/staging/runs/*` and `bucket/scans/*`.
- `SourceCollectionsRead`: `GetObject` on `bucket/assets/*`.
- `RunPrefixList`: `ListBucket` on the bucket, `s3:prefix` in `staging/runs/*`, `scans/*`, `assets/*`.
- `BucketKms`: `Decrypt`, `GenerateDataKey` via S3.

Trust names the pipeline role only (`sts:AssumeRole`, `sts:TagSession`; the latter is needed when a role chained from a Pod Identity session carries transitive tags). The role depends on the workload roles because IAM refuses a trust principal that does not exist yet.

KMS caveat: the session policy lists S3 actions only and grants no KMS action. Under the intersection, a run's SSE-KMS access works only if the key policy's `Principal "*"` + `ViaService` statement is honoured without the session policy's consent. That cannot be verified without AWS; see I-151 in `docs/ISSUES.md`.

The run role's `max_session_duration` is 3600 s, the STS role-chaining cap (spec section 8).

## Inputs

| Name | Type | Default | Description |
|---|---|---|---|
| `name_prefix` | string | required | Prefix for role names. 2-32 chars, lowercase letters, digits, hyphens. |
| `tags` | map(string) | `{}` | Tags applied to every taggable resource. |
| `kms_key_arn` | string | required | Full KMS key ARN (the `kms` module's `key_arn`). |
| `bucket_arn` | string | required | Platform bucket ARN (the `storage` module's `bucket_arn`). |
| `workload_identity` | string | `"pod_identity"` | `pod_identity` or `irsa`. |
| `cluster_name` | string | `null` | EKS cluster; required for `pod_identity`. |
| `oidc_provider_arn` | string | `null` | Cluster IAM OIDC provider ARN; required for `irsa`. |
| `namespace` | string | `"stac-higher"` | DNS-1123 label; must match the chart. |
| `service_accounts` | object | `stac-higher-{app,pipeline,titiler,cube-server}` | ServiceAccount per workload; DNS-1123 labels. |

## Outputs

| Name | Description |
|---|---|
| `role_arns` | Map keyed `app`, `pipeline`, `titiler`, `cube_server`; known at plan. |
| `role_names` | Same keys. |
| `process_run_role_arn` | Run role ARN. |
| `process_run_role_name` | Run role name. |
| `pod_identity_association_ids` | Association IDs by workload; empty under `irsa`. |
| `workload_identity` | Pass-through. |
| `namespace` | Pass-through. |
| `service_accounts` | Pass-through. |
