# R-5 · Terraform database, identity, cognito, demo-destination + `stac-higher` composition with `helm_values` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Tracking:** GitHub #141 (epic #136).

**Goal:** Add the remaining AWS modules (`database`, `identity`, `cognito`, `demo-destination`) and the `stac-higher` composition module. The composition calls all eight modules and emits `helm_values`, the AWS-derived half of the chart's values, which R-6 consumes. Then switch `infra/examples/aws-eks/` to one composition call.

**Architecture:** R-4's conventions hold unchanged. Each module is provider-less, reads partition, region and account from data sources, and builds ARNs from them. Policies are `jsonencode()`d locals, so mock-provider tests can decode and assert them. Every ARN a test needs at plan time is built in `locals` from the partition, account and name, the way `storage` builds `bucket_arn`, so `command = plan` can assert it. The composition wires child outputs into children and into one `helm_values` object. That object is the contract with R-6's chart. `helm_values_yaml` is `yamlencode(helm_values)`, so a deployment can pipe the output straight into `helm -f`.

**Tech Stack:** OpenTofu ≥ 1.10 / Terraform ≥ 1.10 (`.tftest.hcl` with `mock_provider`), `hashicorp/aws ~> 6.0` (schema checked against 6.x: `aws_eks_pod_identity_association`, `aws_db_instance.master_user_secret`, `aws_ec2_managed_prefix_list`), tflint + `tflint-ruleset-aws` (CI only), trivy `config`.

**Spec:** `docs/superpowers/specs/2026-11-02-deployment-artifacts-design.md` §4, §6.2, §6.4, §6.5, §7, §7.1, §7.2, §8, §9, §10, §13 (Q2, Q3), §14. Decision record: ADR 0024. Foundation: `docs/superpowers/plans/2026-11-03-r4-terraform-base.md` (Global Constraints and Rulings) and the modules as built under `infra/terraform/modules/`.

## Global Constraints

- **R-4's Global Constraints apply verbatim.** They are restated here only where R-5 adds to them:
  - `required_version = ">= 1.10"`; `aws ~> 6.0`; no `provider` or `backend` block in any module.
  - Partition, region and account come from data sources (`data.aws_region.current.region`, never `.name`). The literals `arn:aws:` and `amazonaws.com` never appear in `*.tf`, `*.tfvars` or `*.hcl` (`scripts/check-partition-arns.sh`).
  - Policies are `jsonencode()` locals.
  - Every variable and output has a `description`.
  - Shared validations, copied verbatim from R-4: `name_prefix`, `tags`, and `kms_key_arn` with its regex.
  - Every test file starts with R-4's mock block (`aws-us-gov`, `dns-suffix.example`, `000000000000`, `us-gov-west-1`). ARN-typed test inputs are well-formed gov-partition ARNs. `mock_resource`/`mock_data` defaults are needed wherever a computed value flows into an ARN-validated argument under `apply`. Prefer `command = plan`.
  - OpenTofu 1.13 rejects `run.<name>.<output>` inside assertions, so each run asserts on its own state only.
  - trivy: HIGH/CRITICAL clean, or `#trivy:ignore:AWS-NNNN -- <reason>` directly above the resource. A spike of this plan's resource shapes under trivy 0.69.3 found only `AWS-0143` (LOW, inline user policy) and `AWS-0176` (MEDIUM, RDS IAM auth off), both below the gate.
  - Never commit `.terraform/`, lock files, state or tfvars.
  - Commits: `R-5: <what>`, ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- **Layout.** New modules go in `infra/terraform/modules/{database,identity,cognito,demo-destination,stac-higher}/`, each with `versions.tf`, `variables.tf`, `main.tf`, `outputs.tf`, `README.md` and `tests/<module>.tftest.hcl`. `scripts/check-terraform.sh` globs `infra/terraform/modules/*/`, so it picks them up without edits.
- **Module sources inside the repo are relative.** `demo-destination` calls `../storage`; `stac-higher` calls `../kms`, `../storage` and the rest. These resolve inside a `git::…//infra/terraform/modules/stac-higher?ref=` checkout because the whole repo is fetched.
- **Placeholder additions** (R-4's rule otherwise):
  - Example and tests use `vpc-00000000000000000`, `subnet-00000000000000000` / `subnet-00000000000000001`, `sg-00000000000000000`, cluster `example-cluster`, OIDC provider `arn:aws-us-gov:iam::000000000000:oidc-provider/oidc.eks.us-gov-west-1.dns-suffix.example/id/EXAMPLE`, Cognito pool id `us-gov-west-1_EXAMPLE`, and hostname `stac-test-db.example.com`.
  - The S3 prefix-list mock uses `192.0.2.0/24` (RFC 5737 documentation range), in test files only.
- **TLS to RDS stays enforced.** `rds.force_ssl = "1"` is in the parameter group, and no input can override it (spec §7.2, §14).
- **The master password is RDS-managed.** Set `manage_master_user_password = true` with `master_user_secret_kms_key_id = var.kms_key_arn`. No Terraform-generated master password exists. The master secret is read only by the chart's bootstrap Job (spec §7.2).
- **Workload identity toggle.** `workload_identity = "pod_identity" | "irsa"`, default `"pod_identity"`, on `identity` and `stac-higher`. `helm_values.identity.mode` spells it the spec §6.2 way: `"pod-identity"` or `"irsa"`.
- **`helm_values` is a contract** (pinned in Task 5). R-6 consumes it by these exact keys. Changing a key is a breaking change for the chart and needs a matching chart change.
- **No null leaves and no conditional `null` sub-objects in `helm_values`.** In Helm, a `null` in a later values file deletes the chart's default for that key. When an optional module is off, its whole key is omitted. Use the `merge(base, { for k, v in { auth = local.auth, demoDestination = local.demo } : k => v if v != null })` pattern. It was spiked on tofu 1.13.5 and terraform 1.17.1: both return the keys `[aws, database]` when off and add `auth` and `demoDestination` when on.
- **Docs carry no status.** No ADR. `docs/ISSUES.md` entries go under `## Deployment artifacts (R queue, epic #136)` and are numbered from **I-151**. Re-check the next free number with `grep -ohE 'I-[0-9]+' docs/ISSUES.md docs/ISSUES-ARCHIVE.md | sort -t- -k2 -n | tail -1`.
- **Gates.** The R-4 gate block (Task 6 Step 5) plus `npm run verify`. tflint is not installed locally: write "CI validates" for it. No pipeline change, so no pytest or ruff. No AWS account and no apply against AWS (R-11). No Docker, no dev server, no push.

## Review Focus

1. **`rds.force_ssl` switched off through `parameter_overrides`.** Every client verifies against the RDS CA (§7.2), so a deployment trying to unblock a client that cannot do TLS would weaken it for all clients. A validation refuses any override of `rds.force_ssl`. Test: Task 1 `rejects_force_ssl_override`.
2. **`app_url` with a trailing slash or a path** (`https://app.example.com/`). The composition derives the bucket CORS origin and the Cognito callback and logout URLs from it. A slash makes CORS never match the browser's `Origin`, and the callback becomes `//api/auth/callback`. Both fail only in the browser. The composition validation refuses anything except `https://host[:port]`. Test: Task 5 `rejects_app_url_with_path`.
3. **Service-account or namespace names that differ between Terraform and the chart.** A Pod Identity association bound to a name the chart never renders gives the pod no credentials, and the failure appears only as S3 AccessDenied at runtime. So `helm_values.identity` carries `namespace` and `serviceAccounts`, which the chart must use, and the names are validated as DNS-1123 labels. Tests: Task 2 `rejects_bad_service_account`, Task 5 `helm_values_values` (serviceAccounts).
4. **`irsa` without `oidc_provider_arn`, or `pod_identity` without `cluster_name`.** Each would plan a trust policy or association with a null principal and fail only at apply, after other roles exist. Preconditions fail the plan. Tests: Task 2 `irsa_requires_oidc_provider`, `pod_identity_requires_cluster_name`.
5. **Cognito or the demo destination turned off.** A `null` `auth` or `demoDestination` in `helm_values` would delete the chart's defaults for those keys. Each key is omitted instead. Tests: Task 5 `helm_values_top_level_keys` (absent when off) and `cognito_and_demo_on` (present when on).

---

## File Structure

| Path | Responsibility |
|---|---|
| `infra/terraform/modules/database/` | RDS PostgreSQL 17: subnet group, security group (5432 from allowed SGs only), pgstac parameter group with TLS forced, managed master secret, KMS, backups, toggles |
| `infra/terraform/modules/identity/` | Workload roles (app, pipeline, titiler, cube-server), the process-run role, Pod Identity associations or IRSA trust |
| `infra/terraform/modules/cognito/` | User pool, role + data groups, public PKCE app client, hosted domain, claims-mapping output |
| `infra/terraform/modules/demo-destination/` | A second `storage` instance (no CORS), a write-only IAM user, its access key in Secrets Manager |
| `infra/terraform/modules/stac-higher/` | Composition of all eight modules, S3 prefix-list lookup, ESO read-policy document, `helm_values` + `helm_values_yaml` |
| `infra/examples/aws-eks/` | Rewritten: one `stac-higher` call, the `helm_values`/`helm_values_yaml` outputs |
| `infra/terraform/README.md` | Module table rows, a `helm_values` contract section, updated consuming and prerequisites sections |
| `docs/FEATURES.md`, `docs/ISSUES.md` | R-5 row; entries I-151 to I-155 |

**Rulings this plan makes (where the spec, the issue and the code disagreed or were silent):**

- **pgstac tuning is ported from eoapi-cdk as RDS formulas, not as a lookup table.** Source: eoapi-cdk v11.8.1, `lib/database/index.ts`, `PgStacDatabase.getParameters()`. That construct looks up instance memory in `instance-memory.json` (about 20 classes) and computes the following. *mem* is the memory in KiB.
  - `max_connections = min(round(bytes/9531392), 5000)`
  - `shared_buffers = 0.25·mem` (as 8 kB blocks)
  - `effective_cache_size = 0.75·mem` (as 8 kB blocks)
  - `work_mem = floor(shared_buffers_kB / max_connections)`
  - `maintenance_work_mem = 0.25·shared_buffers` (kB)
  - `max_locks_per_transaction = 1024`
  - `temp_buffers = 131072/8` (8 kB blocks)
  - `seq_page_cost = 1`, `random_page_cost = 1.1`

  RDS evaluates `{DBInstanceClassMemory/N}` per class, so the same ratios hold for every instance class without a table:

  | Parameter | Value | Derivation |
  |---|---|---|
  | `max_connections` | `LEAST({DBInstanceClassMemory/9531392},5000)` | eoapi's formula, which is also the RDS default |
  | `shared_buffers` | `{DBInstanceClassMemory/32768}` | 25 % in 8 kB blocks, also the RDS default |
  | `effective_cache_size` | `{DBInstanceClassMemory/10923}` | 75 % in 8 kB blocks (8192 / 0.75) |
  | `maintenance_work_mem` | `{DBInstanceClassMemory/16384}` | 0.25 × 0.25 of bytes, in kB |
  | `work_mem` | `2327` | (bytes/4096) / (bytes/9531392) = 9531392/4096 = 2327 kB exactly, for every class below the 5000-connection cap (about 44 GiB). Above it, `parameter_overrides` |
  | `max_locks_per_transaction` | `1024` | verbatim |
  | `temp_buffers` | `16384` | 131072/8, verbatim |
  | `seq_page_cost` / `random_page_cost` | `1` / `1.1` | verbatim |
  | `rds.force_ssl` | `1` | spec §7.2, not from eoapi |

  `DBInstanceClassMemory` is what RDS leaves after its own reserve, so the values come out slightly below eoapi's table values. That is the same basis the AWS defaults use. Every parameter is `apply_method = "pending-reboot"`: static parameters require it, and at creation the instance boots with the group anyway.
- **Engine: PostgreSQL 17, database `postgis`, master user `stac_higher_master`.** The pgstac v0.9.11 image compose runs is built `FROM postgres:17` (`docker/pgstac/Dockerfile`, `PG_MAJOR=17`). Every compose DSN names database `postgis`, so the chart's DSNs need not differ. `engine_version` must be major 16 or later: spec §7.2's role-ordering design is written for PostgreSQL ≥ 16. The parameter-group family is `postgres<major>`.
- **The RDS security group has no egress rule.** RDS needs none. Terraform's `aws_security_group` removes AWS's default allow-all egress rule. Ingress is one `aws_vpc_security_group_ingress_rule` per allowed SG on 5432/tcp. CIDR ingress is not offered: on EKS Auto Mode, pods use the cluster security group, which the deployment passes in.
- **Every workload role with S3 access also gets the KMS actions that access needs,** conditioned on `kms:ViaService = s3.<region>.<dns_suffix>`. Spec §7.1 names KMS only for the pipeline. But the bucket is SSE-KMS with the platform CMK (R-4), so:
  - presigned PUTs need `kms:GenerateDataKey` for the presigning app role;
  - reads need `kms:Decrypt` for the app, titiler and cube-server roles.

  R-4's key policy already allows any same-account caller through S3 (`ViaService`). The explicit grant keeps each role correct if #159 narrows that policy.
- **The app role is narrower than "Get/Put on the bucket".**
  - It may `GetObject` on `bucket/*`. It presigns downloads of assets, run logs and scan findings.
  - It may `PutObject` on `bucket/staging/*` and `bucket/assets/*` only.

  `app/src/lib/storage/presign.ts` is the app's only S3 caller (`GetObjectCommand`, `PutObjectCommand`). Its PUT callers are `api/uploads` (staged: `stagingKey` → `staging/…`) and canonical uploads (`canonicalAssetKey` → `assets/…`). ADR 0005: the app never streams bytes.
- **The titiler role reads `assets/*` only** (`GetObject`, plus `ListBucket` conditioned on `s3:prefix` `assets/*`). Items' tiled assets are canonical `assets/…` keys. Reference-mode inputs are staged into the run prefix by the launcher (`process/inputs.py`), never read by titiler from another account.
- **The cube-server role reads `assets/*/_cube/*` only** (`GetObject`, plus `ListBucket` conditioned on `s3:prefix` `assets/*/_cube/*`), as the spec says. Its virtual-chunk sources are opened anonymously (`registry.py`: `s3_credentials(anonymous=True)`), so no other grant is needed.
- **The pipeline role:**
  - `GetObject`, `PutObject`, `DeleteObject` and `AbortMultipartUpload` on `bucket/*`;
  - `ListBucket` on the bucket;
  - `kms:Decrypt` and `kms:GenerateDataKey`;
  - `sts:AssumeRole` and `sts:TagSession` on the run role.

  External sources and destinations are reached with connection credentials or anonymously (`connections/adapters/s3.py`), never with the role.
- **The process-run role's identity policy is exactly the ceiling `session_policy()` intersects with,** read from `services/pipeline/src/pipeline/process/credentials.py` and `storage/keys.py`:
  - `RunWritablePrefixes`: `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject` on `bucket/staging/runs/*` (`run_staging_prefix`, which includes `inputs/`) and on `bucket/scans/*` (`image_scan_prefix`).
  - `SourceCollectionsRead`: `s3:GetObject` on `bucket/assets/*` (the `read_prefixes`).
  - `RunPrefixList`: `s3:ListBucket` on the bucket, `StringLike s3:prefix` `["staging/runs/*", "scans/*", "assets/*"]`.
  - `BucketKms`: `kms:Decrypt` and `kms:GenerateDataKey` via S3.

  The session policy narrows these per run to one `staging/runs/{id}/` or `scans/{image}/{scan}/` prefix plus that run's source collections. **The session policy grants no KMS action** (it lists S3 actions only). Under the intersection, a run's SSE-KMS read or write then works only if the key policy's `Principal "*"` + `ViaService` statement is honoured for the session principal without the session policy's consent. That cannot be checked without AWS. It goes to `docs/ISSUES.md` (I-151) for R-11 to settle with one PUT. The fix, if needed, is a KMS statement in `session_policy()` (with #158, which also fixes its `arn:aws:` literal).
- **Run-role trust:**
  - `Principal.AWS` is the pipeline role's ARN only; actions are `sts:AssumeRole` and `sts:TagSession`.
  - `sts:TagSession` is included because a role chained from a Pod Identity session (which carries EKS session tags) must allow it when tags are transitive. It permits no extra access.
  - `max_session_duration = 3600`, which matches the role-chaining cap and `helm_values.aws.processMaxCredentialSeconds`.
  - The run role `depends_on` the workload roles. Its trust policy names the pipeline role's ARN, and IAM rejects a trust principal that does not exist yet.
- **Pod Identity trust is AWS's documented policy with no extra conditions.** `Principal.Service = "pods.eks.<dns_suffix>"`, actions `sts:AssumeRole` and `sts:TagSession`. Scoping comes from the association, whose creation needs `eks:CreatePodIdentityAssociation` + `iam:PassRole`. An unverified `aws:SourceArn` condition could deny every pod. Recorded as I-155.
- **IRSA takes one input, `oidc_provider_arn`.** The issuer host is derived as `split("oidc-provider/", var.oidc_provider_arn)[1]`, so it cannot disagree with the ARN. Trust is `Federated = oidc_provider_arn`, `sts:AssumeRoleWithWebIdentity`, with `StringEquals { "<host>:sub" = "system:serviceaccount:<ns>:<sa>", "<host>:aud" = "sts.<dns_suffix>" }`.
- **Role ARNs and `role_arns` outputs are built in `locals`** (`arn:<partition>:iam::<account>:role/<name>`). That makes them known at plan, so policies and `helm_values` can be asserted under `command = plan`, and the pipeline policy can name the run role without a cycle. Pod Identity associations use `aws_iam_role.workload[k].arn` so they still depend on the role.
- **`identity` also takes the namespace and service-account names, and `helm_values` hands them to the chart.** Defaults: namespace `stac-higher` (spec §6.5); SAs `stac-higher-app`, `stac-higher-pipeline`, `stac-higher-titiler`, `stac-higher-cube-server`. R-6/R-7 must name their ServiceAccounts from `identity.serviceAccounts`.
- **`helm_values.identity.roleArns` is always emitted.** The chart uses it only in `irsa` mode, for the `eks.amazonaws.com/role-arn` SA annotation. Emitting it in both modes keeps the object's shape fixed.
- **Cognito:**
  - Admin-created users only (no self sign-up on a public test environment); email as username; optional TOTP MFA; password minimum 12 characters.
  - Groups are the role groups (default `stac-admins`→`admin`, `stac-operators`→`operator`, `stac-members`→`member`) plus `data_groups` (default `[]`).
  - The public client has no secret, the `code` flow, scopes `openid profile email` (what `api/auth/login.ts` requests), only `ALLOW_REFRESH_TOKEN_AUTH`, and token revocation. Cognito accepts PKCE on a public client and has no flag for it; the app always sends a challenge.
  - The issuer is built as `https://cognito-idp.<region>.<dns_suffix>/<pool id>`.
  - The hosted domain is `<prefix>.auth.<region>.amazoncognito.com`. That suffix is Cognito's own, not the partition's, and the README says the module does not claim GovCloud hosted-UI parity (spec §9). The app reads endpoints from discovery, so the hosted domain is informational.
  - A `claims_mapping` output carries `{"groups":"cognito:groups","roles":"cognito:groups","roleMap":<role_groups>,"name":["name","email","username"]}`. `username` is added because Cognito access tokens carry no `name` or `email`.
- **`helm_values.auth` is emitted only when Cognito is on.** With another IdP, the deployment writes `auth` in its own values file. `audienceClaim = "client_id"` and `audiences = [clientId]` follow from `docs/auth.md`: in `client_id` mode the default `stac-higher` audience stays in force, so the client id must be listed.
- **The demo-destination user is write-only.** It gets `s3:PutObject` and `s3:AbortMultipartUpload` on `bucket/*`, `s3:ListBucket` on the bucket, and `kms:GenerateDataKey` plus `kms:Decrypt` via S3. Get and Delete are absent.
  - `ListBucket` is kept because the connection test (`adapters/s3.py` `test()`) runs `head_bucket`, then `list_objects_v2`.
  - `kms:Decrypt` is kept because SSE-KMS multipart uploads need it.
  - Delivery only calls `put_atomic` / `copy_object_from` (`delivery/worker.py`). The server-side copy needs `GetObject` on the platform bucket, which this user lacks, so it fails and falls back to streaming, as the adapter documents.
- **The demo-destination secret stores `{"access_key_id","secret_access_key"}` only,** the S3 connection credential shape a customer would enter. Bucket and region travel in `helm_values.demoDestination`. The bucket is encrypted with the platform key, which the composition passes in.
- **The composition takes `app_url` and derives the rest:**
  - `cors_allowed_origins` defaults to `[app_url]`;
  - `cognito_callback_urls` defaults to `["<app_url>/api/auth/callback"]` (the app's `OIDC_REDIRECT_URI` path);
  - `cognito_logout_urls` defaults to `["<app_url>/"]` (`api/auth/logout.ts`'s `post_logout_redirect_uri`).

  Each default is `null` in the variable and resolved in a local. `helm_values.publicUrls.app = app_url`, so the deployment has one source for it.
- **Composition inputs repeat the children's defaults verbatim and do not re-validate,** except `name_prefix` (shared rule) and `app_url` (new). A child's validation still fails the plan, naming the child variable. Composition variable names are the child's name with a module prefix where ambiguous (`db_*`, `cognito_*`, `demo_destination_*`, `kms_*`, `secrets_*`, `bucket_*`).
- **`aws.s3PrefixListCidrs` comes from `data "aws_ec2_managed_prefix_list"`, looked up by name.** The name is the reversed `dns_suffix` plus `.<region>.s3`, which gives `com.amazonaws.us-east-1.s3` in commercial and `example.dns-suffix.us-gov-west-1.s3` under the test mock. The value is the sorted list of entry CIDRs.
- **The composition also outputs `external_secrets_policy_json`.** That is the IAM policy the cluster's External Secrets Operator role needs to read every secret `helm_values` names: `secretsmanager:GetSecretValue` and `DescribeSecret` on them, and `kms:Decrypt` via Secrets Manager. The ESO role belongs to the cluster (spec §4.1), so this is an unattached document: without it, the deployment would have to copy ARNs by hand.
- **`aws.partition` is in `helm_values`** so the chart can give the pipeline the partition when #158 adds that setting.
- **`processMaxCredentialSeconds` is a constant `3600`,** the STS role-chaining cap (spec §8). It is not an input.

---

### Task 1: `database` module

**Files:**
- Create: `infra/terraform/modules/database/{versions.tf,variables.tf,main.tf,outputs.tf,README.md}`
- Test: `infra/terraform/modules/database/tests/database.tftest.hcl`

**Interfaces:**
- Consumes: the `kms` module's `key_arn` (through the composition), validated by the shared `kms_key_arn` rule.
- Produces, inputs (name: type = default; validation):
  - `name_prefix`, `tags`, `kms_key_arn`: shared rules.
  - `vpc_id`: string; `^vpc-[0-9a-f]{8,17}$`.
  - `subnet_ids`: list(string); at least 2, each `^subnet-[0-9a-f]{8,17}$`.
  - `allowed_security_group_ids`: list(string); at least 1, each `^sg-[0-9a-f]{8,17}$`.
  - `instance_class`: string = `"db.t4g.medium"`; `^db\.`.
  - `engine_version`: string = `"17"`; `^[0-9]{2}(\.[0-9]+)?$` and major ≥ 16.
  - `db_name`: string = `"postgis"`; `^[a-z][a-z0-9_]{0,62}$`.
  - `master_username`: string = `"stac_higher_master"`; same regex.
  - `allocated_storage`: number = `50`; `>= 20`.
  - `max_allocated_storage`: number = `200`; `0`, or `>= allocated_storage`.
  - `multi_az`: bool = `false`.
  - `deletion_protection`: bool = `true`.
  - `backup_retention_period`: number = `7`; 1–35.
  - `backup_window`, `maintenance_window`: string = `null`.
  - `skip_final_snapshot`: bool = `false`.
  - `parameter_overrides`: map(string) = `{}`; must not contain the key `rds.force_ssl`.
  - `ca_cert_identifier`: string = `"rds-ca-rsa2048-g1"`.
  - `performance_insights_enabled`: bool = `true`.
  - `apply_immediately`: bool = `false`.
- Produces, outputs:
  - `address` (host), `port` (number), `db_name`, `master_username`.
  - `master_user_secret_arn` (`aws_db_instance.this.master_user_secret[0].secret_arn`).
  - `security_group_id`, `instance_arn`, `instance_identifier`, `parameter_group_name`, `ca_cert_identifier`.

- [ ] **Step 1: Write the failing tests.** Use the mock block plus `mock_resource "aws_db_instance" { defaults = { address = "stac-test-db.example.com", port = 5432, master_user_secret = [{ secret_arn = "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:rds!db-00000000-AbCdEf", kms_key_id = "<gov key ARN>", secret_status = "active" }] } }`. Set `variables { name_prefix = "stac-test", kms_key_arn = <gov key ARN>, vpc_id = "vpc-00000000000000000", subnet_ids = ["subnet-00000000000000000", "subnet-00000000000000001"], allowed_security_group_ids = ["sg-00000000000000000"] }`. Let `params = { for p in aws_db_parameter_group.this.parameter : p.name => p.value }`. Every run is `command = plan` except the last.
  - `instance_defaults` (with `tags = { project = "stac-higher" }`):
    - `identifier == "stac-test-db"`, `engine == "postgres"`, `engine_version == "17"`, `instance_class == "db.t4g.medium"`, `db_name == "postgis"`, `username == "stac_higher_master"`.
    - `publicly_accessible == false`, `storage_encrypted == true`, `kms_key_id == var.kms_key_arn`.
    - `storage_type == "gp3"`, `allocated_storage == 50`, `max_allocated_storage == 200`.
    - `copy_tags_to_snapshot`, `auto_minor_version_upgrade`, `ca_cert_identifier == "rds-ca-rsa2048-g1"`.
    - The tags map equals the input on the instance, the security group and the parameter group.
  - `managed_master_password`: `manage_master_user_password == true`, `master_user_secret_kms_key_id == var.kms_key_arn`, `password == null`.
  - `toggles_default`: `multi_az == false`, `deletion_protection == true`, `backup_retention_period == 7`, `skip_final_snapshot == false`, `final_snapshot_identifier == "stac-test-db-final"`.
  - `toggles_override` (`multi_az = true`, `deletion_protection = false`, `backup_retention_period = 14`, `skip_final_snapshot = true`): the first three as set, and `final_snapshot_identifier == null`.
  - `pgstac_parameters`:
    - `family == "postgres17"` and `name_prefix == "stac-test-pgstac-"`.
    - `params` equals exactly the ten-entry map in the Rulings table, with the values as strings.
    - `alltrue([for p in aws_db_parameter_group.this.parameter : p.apply_method == "pending-reboot"])`.
  - `parameter_override_merges` (`parameter_overrides = { work_mem = "8192", log_min_duration_statement = "1000" }`): `params["work_mem"] == "8192"`, `params["log_min_duration_statement"] == "1000"`, `params["shared_buffers"] == "{DBInstanceClassMemory/32768}"`, `params["rds.force_ssl"] == "1"`, `length(params) == 11`.
  - `rejects_force_ssl_override` (`{ "rds.force_ssl" = "0" }`): `expect_failures = [var.parameter_overrides]`.
  - `family_follows_major` (`engine_version = "16.4"`): `family == "postgres16"`.
  - `rejects_old_engine` (`"15"`): `expect_failures = [var.engine_version]`.
  - `security_group_ingress_per_allowed_sg` (two SGs, `sg-…0` and `sg-…1`):
    - `aws_security_group.this.vpc_id == var.vpc_id`.
    - `length(aws_vpc_security_group_ingress_rule.postgres) == 2`.
    - Each rule has `ip_protocol == "tcp"`, `from_port == 5432`, `to_port == 5432`, and `referenced_security_group_id` among the inputs.
  - `subnet_group_from_inputs`: `aws_db_subnet_group.this.name == "stac-test-db"` and `toset(subnet_ids) == toset(var.subnet_ids)`.
  - `performance_insights_encrypted`: `performance_insights_enabled == true` and `performance_insights_kms_key_id == var.kms_key_arn`.
  - `rejects_single_subnet`, `rejects_no_allowed_security_group` (`[]`), `rejects_kms_key_id`, `rejects_bad_name_prefix`: `expect_failures` on the variable.
  - `outputs_from_instance` (`command = apply`): `output.address == "stac-test-db.example.com"`, `output.port == 5432`, `output.master_user_secret_arn` equals the mock ARN, `output.db_name == "postgis"`.

- [ ] **Step 2: Run to verify they fail**

Run: `tofu -chdir=infra/terraform/modules/database init -backend=false -input=false && tofu -chdir=infra/terraform/modules/database test`
Expected: FAIL (no configuration / undeclared resources).

- [ ] **Step 3: Implement the module.**
  - Data source: `aws_partition` is not needed (no ARN is built), so declare none. tflint would flag an unused one.
  - Locals:
    - `engine_major = tonumber(split(".", var.engine_version)[0])`.
    - `pgstac_parameters`: the Rulings table map.
    - `parameters = merge(local.pgstac_parameters, var.parameter_overrides)`.
  - `aws_db_subnet_group.this`: `name = "${var.name_prefix}-db"`, `subnet_ids`, `tags`.
  - `aws_security_group.this`: `name = "${var.name_prefix}-db"`, `description = "stac-higher PostgreSQL: 5432 from the allowed security groups only"`, `vpc_id`, `tags`. No inline rules and no egress rule (see Rulings).
  - `aws_vpc_security_group_ingress_rule.postgres`: `for_each = toset(var.allowed_security_group_ids)`, tcp 5432, `referenced_security_group_id = each.value`, `description = "PostgreSQL from ${each.value}"`, `tags`.
  - `aws_db_parameter_group.this`:
    - `name_prefix = "${var.name_prefix}-pgstac-"`, `family = "postgres${local.engine_major}"`.
    - `description = "pgstac tuning from eoapi-cdk v11.8.1 PgStacDatabase; TLS enforced"`.
    - A dynamic `parameter` over `local.parameters` with `apply_method = "pending-reboot"`.
    - `lifecycle { create_before_destroy = true }`, `tags`.
  - `aws_db_instance.this`:
    - `identifier = "${var.name_prefix}-db"`, `engine = "postgres"`, plus every input above.
    - `storage_type = "gp3"`, `storage_encrypted = true`, `kms_key_id = var.kms_key_arn`.
    - `manage_master_user_password = true`, `master_user_secret_kms_key_id = var.kms_key_arn`.
    - `db_subnet_group_name`, `vpc_security_group_ids = [aws_security_group.this.id]`, `parameter_group_name = aws_db_parameter_group.this.name`.
    - `publicly_accessible = false`, `copy_tags_to_snapshot = true`, `auto_minor_version_upgrade = true`.
    - `final_snapshot_identifier = var.skip_final_snapshot ? null : "${var.name_prefix}-db-final"`.
    - `performance_insights_kms_key_id = var.performance_insights_enabled ? var.kms_key_arn : null`.
    - `iam_database_authentication_enabled` is left off (trivy AWS-0176 MEDIUM; the platform authenticates with per-role passwords, §7.2).
  - `README.md` sections:
    - inputs and outputs tables;
    - "pgstac tuning", with the Rulings table and the eoapi-cdk source path;
    - "TLS", covering `rds.force_ssl`, the CA identifier ↔ the region's CA bundle the deployment supplies as `database.caBundle`, and that `PGSSLMODE=verify-full` is the client side (`docs/backend.md`);
    - "Master credentials", covering the RDS-managed secret, that the bootstrap Job is its only reader, and that rotation is safe (§7.2);
    - "Extensions": RDS ships `postgis`, `btree_gist` and `unaccent`, and the chart's bootstrap Job creates them as master before `pypgstac migrate` (§7.2). The module installs nothing;
    - "Static parameters apply at reboot".

- [ ] **Step 4: Run to verify they pass**

Run: `scripts/check-terraform.sh tofu && scripts/check-terraform.sh terraform && tofu fmt -check -recursive -diff infra/terraform && scripts/check-partition-arns.sh && trivy config --exit-code 1 --severity HIGH,CRITICAL infra/terraform`
Expected:
- database `17 passed, 0 failed` on both engines; R-4's modules stay green.
- fmt is silent and the checker exits 0.
- trivy reports 0 HIGH/CRITICAL.

- [ ] **Step 5: Commit**

```bash
git add infra/terraform/modules/database
git commit -m "R-5: database module — RDS PostgreSQL 17, pgstac parameter group from eoapi-cdk, TLS forced, managed master secret"
```

---

### Task 2: `identity` module

**Files:**
- Create: `infra/terraform/modules/identity/{versions.tf,variables.tf,main.tf,outputs.tf,README.md}`
- Test: `infra/terraform/modules/identity/tests/identity.tftest.hcl`

**Interfaces:**
- Consumes: the `storage` module's `bucket_arn` and the `kms` module's `key_arn` (through the composition).
- Produces, inputs:
  - `name_prefix`, `tags`, `kms_key_arn`: shared rules.
  - `bucket_arn`: string; `^arn:[a-z-]+:s3:::[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$`.
  - `workload_identity`: string = `"pod_identity"`; must be `pod_identity` or `irsa`.
  - `cluster_name`: string = `null`; null, or `^[0-9A-Za-z][A-Za-z0-9_-]{0,99}$`.
  - `oidc_provider_arn`: string = `null`; null, or `^arn:[a-z-]+:iam::[0-9]{12}:oidc-provider/.+$`.
  - `namespace`: string = `"stac-higher"`; DNS-1123 label `^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$`.
  - `service_accounts`: `object({ app = string, pipeline = string, titiler = string, cube_server = string })` = `{ app = "stac-higher-app", pipeline = "stac-higher-pipeline", titiler = "stac-higher-titiler", cube_server = "stac-higher-cube-server" }`; every value a DNS-1123 label.
- Produces, outputs:
  - `role_arns`: map with keys `app`, `pipeline`, `titiler`, `cube_server`.
  - `role_names`: same keys.
  - `process_run_role_arn`, `process_run_role_name`.
  - `pod_identity_association_ids`: map, empty under `irsa`.
  - `workload_identity`, `namespace`, `service_accounts`: pass-through.
- Produces, resource addresses:
  - `aws_iam_role.workload["app"|"pipeline"|"titiler"|"cube_server"]` and `aws_iam_role_policy.workload[...]`;
  - `aws_iam_role.process_run` and `aws_iam_role_policy.process_run`;
  - `aws_eks_pod_identity_association.this[...]`.

- [ ] **Step 1: Write the failing tests.**
  - Set the mock block and `variables { name_prefix = "stac-test", kms_key_arn = <gov key ARN>, bucket_arn = "arn:aws-us-gov:s3:::stac-test-platform", cluster_name = "example-cluster" }`. All runs are `command = plan`.
  - Let `B = "arn:aws-us-gov:s3:::stac-test-platform"`, `K = <gov key ARN>`, and `sids(p) = { for s in jsondecode(p).Statement : s.Sid => s }` (inline it per assertion). Every statement must have a `Sid`.
  - `KMS_VIA = { StringEquals = { "kms:ViaService" = "s3.us-gov-west-1.dns-suffix.example" } }`.

  Runs:
  - `role_names`: the five role names are `stac-test-app`, `stac-test-pipeline`, `stac-test-titiler`, `stac-test-cube-server`, `stac-test-process-run`. `aws_iam_role.process_run.max_session_duration == 3600`.
  - `pod_identity_trust`: for each of the four workload roles, the trust has one statement with `Principal.Service == "pods.eks.dns-suffix.example"`, `toset(Action) == toset(["sts:AssumeRole", "sts:TagSession"])`, and no `Condition` key.
  - `pod_identity_associations`:
    - `length(aws_eks_pod_identity_association.this) == 4`.
    - Every association has `cluster_name == "example-cluster"` and `namespace == "stac-higher"`.
    - `aws_eks_pod_identity_association.this["cube_server"].service_account == "stac-higher-cube-server"`, and likewise for the other three defaults.
  - `app_policy`, with Sids `{PresignGet, PresignPut, BucketKms}` exactly:
    - `PresignGet`: `["s3:GetObject"]` on `["${B}/*"]`.
    - `PresignPut`: `["s3:PutObject"]` on `toset(["${B}/staging/*", "${B}/assets/*"])`.
    - `BucketKms`: `toset(["kms:Decrypt", "kms:GenerateDataKey"])` on `K` with `Condition == KMS_VIA`.
  - `pipeline_policy`, with Sids `{BucketObjects, BucketList, BucketKms, AssumeProcessRunRole}`:
    - `BucketObjects`: `toset(["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"])` on `["${B}/*"]`.
    - `BucketList`: `["s3:ListBucket"]` on `[B]`.
    - `BucketKms`: as for the app.
    - `AssumeProcessRunRole`: `toset(["sts:AssumeRole", "sts:TagSession"])` on `"arn:aws-us-gov:iam::000000000000:role/stac-test-process-run"`.
  - `titiler_policy`, with Sids `{AssetsRead, AssetsList, BucketKms}`:
    - `AssetsRead`: `s3:GetObject` on `["${B}/assets/*"]`.
    - `AssetsList`: `s3:ListBucket` on `[B]`, `Condition.StringLike["s3:prefix"] == ["assets/*"]`.
    - `BucketKms`: `["kms:Decrypt"]` only.
  - `cube_server_policy`, with Sids `{CubeRead, CubeList, BucketKms}`:
    - `CubeRead`: `s3:GetObject` on `["${B}/assets/*/_cube/*"]`.
    - `CubeList`: `s3:ListBucket` on `[B]`, with prefix `["assets/*/_cube/*"]`.
    - `BucketKms`: `["kms:Decrypt"]` only.
  - `process_run_trust`: one statement, `Principal.AWS == "arn:aws-us-gov:iam::000000000000:role/stac-test-pipeline"`, `toset(Action) == toset(["sts:AssumeRole", "sts:TagSession"])`.
  - `process_run_policy` (the `session_policy()` ceiling), with Sids `{RunWritablePrefixes, SourceCollectionsRead, RunPrefixList, BucketKms}`:
    - `RunWritablePrefixes`: `toset(["s3:PutObject", "s3:GetObject", "s3:DeleteObject"])` on `toset(["${B}/staging/runs/*", "${B}/scans/*"])`.
    - `SourceCollectionsRead`: `["s3:GetObject"]` on `["${B}/assets/*"]`.
    - `RunPrefixList`: `["s3:ListBucket"]` on `[B]`, with `toset(Condition.StringLike["s3:prefix"]) == toset(["staging/runs/*", "scans/*", "assets/*"])`.
    - `BucketKms`: Decrypt + GenerateDataKey.
  - `irsa_trust` (`workload_identity = "irsa"`, `oidc_provider_arn = <placeholder>`, `cluster_name = null`):
    - `length(aws_eks_pod_identity_association.this) == 0`.
    - The app trust has `Principal.Federated == var.oidc_provider_arn` and `Action == "sts:AssumeRoleWithWebIdentity"`.
    - `Condition.StringEquals["oidc.eks.us-gov-west-1.dns-suffix.example/id/EXAMPLE:sub"] == "system:serviceaccount:stac-higher:stac-higher-app"`.
    - `[...":aud"] == "sts.dns-suffix.example"`.
    - The pipeline trust's `sub` names `stac-higher-pipeline`.
  - `irsa_requires_oidc_provider` (`irsa`, no ARN) and `pod_identity_requires_cluster_name` (`cluster_name = null`): `expect_failures = [aws_iam_role.workload]`.
  - `rejects_unknown_mode` (`"kiam"`): `expect_failures = [var.workload_identity]`.
  - `rejects_bad_service_account` (`cube_server = "Cube_Server"`, the other three default): `expect_failures = [var.service_accounts]`.
  - `rejects_kms_key_id`, `rejects_bad_name_prefix`.
  - `outputs_known_at_plan`:
    - `output.role_arns == { app = "arn:aws-us-gov:iam::000000000000:role/stac-test-app", pipeline = "…-pipeline", titiler = "…-titiler", cube_server = "…-cube-server" }`, written out in full.
    - `output.process_run_role_arn == "arn:aws-us-gov:iam::000000000000:role/stac-test-process-run"`.

- [ ] **Step 2: Run to verify they fail**

Run: `tofu -chdir=infra/terraform/modules/identity init -backend=false -input=false && tofu -chdir=infra/terraform/modules/identity test`
Expected: FAIL.

- [ ] **Step 3: Implement the module.**
  - Data sources: `aws_partition`, `aws_caller_identity`, `aws_region`.
  - Locals:
    - `workloads = { app = { sa = var.service_accounts.app, policy = … }, pipeline = …, titiler = …, cube_server = … }`.
    - `role_name = { for k in keys(local.workloads) : k => "${var.name_prefix}-${replace(k, "_", "-")}" }`.
    - `role_arn`, built from the partition, the account and `role_name`; plus `process_run_role_name` and `process_run_role_arn`.
    - `oidc_host = var.oidc_provider_arn == null ? null : split("oidc-provider/", var.oidc_provider_arn)[1]`.
    - `kms_via_s3 = "s3.${region}.${dns_suffix}"`.
    - `trust[k]`, chosen by `var.workload_identity` per the Rulings.
  - Every policy is a `jsonencode` local with the Sids above. Add a comment above the process-run policy: "mirrors `session_policy()` in services/pipeline/src/pipeline/process/credentials.py and the prefixes in storage/keys.py; change both together".
  - `aws_iam_role.workload`:
    - `for_each = local.workloads`, `name`, `assume_role_policy`, `tags`.
    - `description = "stac-higher <workload> (${var.workload_identity})"`.
    - `lifecycle` preconditions:
      - `var.workload_identity != "irsa" || var.oidc_provider_arn != null`, message: `"workload_identity = \"irsa\" requires oidc_provider_arn (the cluster's IAM OIDC provider)."`
      - `var.workload_identity != "pod_identity" || var.cluster_name != null`, message: `"workload_identity = \"pod_identity\" requires cluster_name."`
  - `aws_iam_role_policy.workload` (`for_each`, `name = "s3"`).
  - `aws_iam_role.process_run`:
    - trust per the Rulings, `max_session_duration = 3600`, `depends_on = [aws_iam_role.workload]`.
    - Comment: IAM refuses a trust principal that does not exist yet.
  - `aws_iam_role_policy.process_run`.
  - `aws_eks_pod_identity_association.this`: `for_each = var.workload_identity == "pod_identity" ? local.workloads : {}`, with `cluster_name`, `namespace`, `service_account = each.value.sa`, `role_arn = aws_iam_role.workload[each.key].arn`, `tags`.
  - `README.md`:
    - the role and permission table (Rulings);
    - the toggle, with the inputs each mode needs;
    - "the namespace and SA names must match the chart: pass them through `helm_values.identity`";
    - the session-policy relationship, with the KMS caveat pointing to I-151;
    - the deploying principal needs `iam:PassRole` + `eks:CreatePodIdentityAssociation`;
    - the 1 h role-chaining cap (spec §8).

- [ ] **Step 4: Run to verify they pass.** Run the Task 1 Step 4 command. Expected: identity `17 passed, 0 failed` on both engines; fmt, checker and trivy clean.

- [ ] **Step 5: Commit**

```bash
git add infra/terraform/modules/identity
git commit -m "R-5: identity module — per-workload roles, process-run role (session-policy ceiling), Pod Identity or IRSA"
```

---

### Task 3: `cognito` module (optional)

**Files:**
- Create: `infra/terraform/modules/cognito/{versions.tf,variables.tf,main.tf,outputs.tf,README.md}`
- Test: `infra/terraform/modules/cognito/tests/cognito.tftest.hcl`

**Interfaces:**
- Produces, inputs:
  - `name_prefix`, `tags`.
  - `callback_urls`: list(string), required, at least 1. Each matches `^https://[^\s/]+(/[^\s]*)?$` or `^http://localhost(:[0-9]{1,5})?(/[^\s]*)?$`.
  - `logout_urls`: list(string) = `[]`, same rule.
  - `domain_prefix`: string = `null` (null means `name_prefix`). Null, or `^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$` not containing `aws`, `amazon` or `cognito`.
  - `role_groups`: map(string) = `{ "stac-admins" = "admin", "stac-operators" = "operator", "stac-members" = "member" }`; values in `member|operator|admin`.
  - `data_groups`: list(string) = `[]`.
  - `deletion_protection`: bool = `true`.
  - `access_token_validity_minutes`: number = `60`; 5–1440.
  - `refresh_token_validity_days`: number = `1`; 1–3650.
- Produces, outputs:
  - `user_pool_id`, `user_pool_arn`, `issuer_url`, `client_id`, `hosted_domain`.
  - `claims_mapping` (object), `group_names` (set), `callback_urls`, `logout_urls`.

- [ ] **Step 1: Write the failing tests.**
  - Set the mock block plus `mock_resource "aws_cognito_user_pool" { defaults = { id = "us-gov-west-1_EXAMPLE" } }`, and `variables { name_prefix = "stac-test", callback_urls = ["https://app.example.com/api/auth/callback"], logout_urls = ["https://app.example.com/"] }`.
  - Runs are `plan` unless noted.

  Runs:
  - `user_pool_defaults`:
    - `name == "stac-test-users"`, `one(admin_create_user_config).allow_admin_create_user_only == true`.
    - `username_attributes == ["email"]`, `auto_verified_attributes == ["email"]`.
    - `deletion_protection == "ACTIVE"`, `mfa_configuration == "OPTIONAL"`, `one(software_token_mfa_configuration).enabled == true`.
    - `one(password_policy).minimum_length == 12`.
  - `public_pkce_client`:
    - `generate_secret == false`, `allowed_oauth_flows == ["code"]`, `allowed_oauth_flows_user_pool_client == true`.
    - `toset(allowed_oauth_scopes) == toset(["openid", "profile", "email"])`, `supported_identity_providers == ["COGNITO"]`.
    - `callback_urls == var.callback_urls`, `logout_urls == var.logout_urls`.
    - `explicit_auth_flows == ["ALLOW_REFRESH_TOKEN_AUTH"]`, `prevent_user_existence_errors == "ENABLED"`, `enable_token_revocation == true`.
  - `token_validity`: `access_token_validity == 60`, `id_token_validity == 60`, `refresh_token_validity == 1`; `token_validity_units` is minutes / minutes / days.
  - `groups_from_role_and_data_groups` (`data_groups = ["earth-observation"]`): `toset(keys(aws_cognito_user_group.this)) == toset(["stac-admins", "stac-operators", "stac-members", "earth-observation"])`.
  - `domain_defaults_to_prefix`: `aws_cognito_user_pool_domain.this.domain == "stac-test"`.
  - `claims_mapping_output`: `output.claims_mapping == { groups = "cognito:groups", roles = "cognito:groups", roleMap = { "stac-admins" = "admin", "stac-operators" = "operator", "stac-members" = "member" }, name = ["name", "email", "username"] }`. Compare with `jsonencode` on both sides.
  - `issuer_and_hosted_domain` (`command = apply`): `output.issuer_url == "https://cognito-idp.us-gov-west-1.dns-suffix.example/us-gov-west-1_EXAMPLE"` and `output.hosted_domain == "stac-test.auth.us-gov-west-1.amazoncognito.com"`.
  - `allows_localhost_callback` (`["http://localhost:4321/api/auth/callback"]`): the plan succeeds and the client's `callback_urls` equal it.
  - `rejects_http_callback` (`["http://app.example.com/api/auth/callback"]`): `expect_failures = [var.callback_urls]`.
  - `rejects_reserved_domain_prefix` (`domain_prefix = "stac-cognito"`): `expect_failures = [var.domain_prefix]`.
  - `rejects_reserved_name_prefix_as_domain` (`name_prefix = "aws-test"`, `domain_prefix` null): `expect_failures = [aws_cognito_user_pool_domain.this]`.
  - `rejects_unknown_role` (`role_groups = { "stac-root" = "superuser" }`): `expect_failures = [var.role_groups]`.
  - `deletion_protection_off` (`false`): `deletion_protection == "INACTIVE"`.
  - `rejects_bad_name_prefix`.

- [ ] **Step 2: Run to verify they fail.** Use the Task 2 Step 2 command with `cognito`. Expected: FAIL.

- [ ] **Step 3: Implement the module.**
  - Data sources: `aws_partition`, `aws_region`.
  - Locals:
    - `domain_prefix = coalesce(var.domain_prefix, var.name_prefix)`.
    - `groups = merge({ for g, _ in var.role_groups : g => "Role group (${var.role_groups[g]})" }, { for g in var.data_groups : g => "Data group" })`.
    - `issuer_url = "https://cognito-idp.${region}.${dns_suffix}/${aws_cognito_user_pool.this.id}"`.
    - `hosted_domain = "${local.domain_prefix}.auth.${region}.amazoncognito.com"`.
  - Resources:
    - `aws_cognito_user_pool.this` per the tests, with `account_recovery_setting` verified email.
    - `aws_cognito_user_group.this` (`for_each = local.groups`).
    - `aws_cognito_user_pool_client.this` (`name = "${var.name_prefix}-app"`).
    - `aws_cognito_user_pool_domain.this`, with a precondition that `local.domain_prefix` contains none of `aws`, `amazon`, `cognito`.
  - `README.md` sections:
    - The app settings this module feeds: `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_REDIRECT_URI`, `AUTH_BEARER_AUDIENCE_CLAIM=client_id`, `AUTH_BEARER_AUDIENCES=<client id>`, `AUTH_CLAIMS_MAPPING`, and the proxy's `ALLOWED_JWT_AUDIENCES` (`docs/auth.md`).
    - Admin-created users only.
    - Logout ends only the app session (I-153).
    - The hosted domain must be unique in the region, and its suffix is commercial-form (spec §9: no GovCloud parity assumed).
    - Machine (client-credentials) clients are not created.

- [ ] **Step 4: Run to verify they pass.** Run the Task 1 Step 4 command. Expected: cognito `14 passed, 0 failed` on both engines; the rest stays clean.

- [ ] **Step 5: Commit**

```bash
git add infra/terraform/modules/cognito
git commit -m "R-5: cognito module — user pool, role + data groups, public PKCE client, hosted domain, claims mapping"
```

---

### Task 4: `demo-destination` module (optional)

**Files:**
- Create: `infra/terraform/modules/demo-destination/{versions.tf,variables.tf,main.tf,outputs.tf,README.md}`
- Test: `infra/terraform/modules/demo-destination/tests/demo-destination.tftest.hcl`

**Interfaces:**
- Consumes: `../storage` (R-4 interface: `name_prefix`, `tags`, `name`, `kms_key_arn`, `cors_allowed_origins`, `force_destroy`; outputs `bucket_name`, `bucket_arn`, `kms_key_arn`).
- Produces, inputs:
  - `name_prefix`, `tags`, `kms_key_arn`: shared rules.
  - `name`: string = `"demo-destination"`; storage's `name` rule.
  - `force_destroy`: bool = `false`.
  - `recovery_window_in_days`: number = `7`; the secrets module's rule.
- Produces, outputs: `bucket_name`, `bucket_arn`, `user_name`, `user_arn`, `credentials_secret_arn`, `credentials_secret_name`. Never the key itself.

- [ ] **Step 1: Write the failing tests.**
  - Set the mock block and `variables { name_prefix = "stac-test", kms_key_arn = <gov key ARN> }`.
  - Let `D = "arn:aws-us-gov:s3:::stac-test-demo-destination"`.

  Runs:
  - `bucket_uses_storage_with_platform_key` (plan): `module.bucket.kms_key_arn == var.kms_key_arn`.
  - `user_named` (plan): `aws_iam_user.this.name == "stac-test-demo-destination"` and `path == "/stac-higher/"`.
  - `user_policy_write_only` (plan). Sids are exactly `{DestinationWrite, DestinationList, DestinationKms}`:
    - `DestinationWrite`: `toset(["s3:PutObject", "s3:AbortMultipartUpload"])` on `["${D}/*"]`.
    - `DestinationList`: `["s3:ListBucket"]` on `[D]`.
    - `DestinationKms`: `toset(["kms:GenerateDataKey", "kms:Decrypt"])` on the key, with `kms:ViaService == "s3.us-gov-west-1.dns-suffix.example"`.
    - No action anywhere is `s3:GetObject`, `s3:DeleteObject` or contains `*`.
  - `access_key_for_user` (plan): `aws_iam_access_key.this.user == "stac-test-demo-destination"`.
  - `credentials_secret` (plan): `name == "stac-test/demo/destination_credentials"`, `kms_key_id == var.kms_key_arn`, `recovery_window_in_days == 7`.
  - `credentials_secret_shape` (apply): `toset(keys(jsondecode(aws_secretsmanager_secret_version.this.secret_string))) == toset(["access_key_id", "secret_access_key"])`.
  - `custom_name` (plan, `name = "delivery-test"`): `DestinationWrite` names `arn:aws-us-gov:s3:::stac-test-delivery-test/*`.
  - `rejects_kms_key_id`, `rejects_bad_name_prefix`.

- [ ] **Step 2: Run to verify they fail.** Use the Task 2 Step 2 command with `demo-destination`. Expected: FAIL.

- [ ] **Step 3: Implement the module.**
  - `module "bucket" { source = "../storage", name_prefix, tags, name = var.name, kms_key_arn, cors_allowed_origins = [], force_destroy }`.
  - Local `bucket_arn`, built from the partition and `"${var.name_prefix}-${var.name}"`, the same way storage builds it. This keeps the policy known at plan.
  - `aws_iam_user.this` (`path = "/stac-higher/"`), `aws_iam_user_policy.this` (`name = "write-demo-destination"`) and `aws_iam_access_key.this`. trivy `AWS-0143` (LOW) needs no ignore.
  - `aws_secretsmanager_secret.this` (`kms_key_id`, `recovery_window_in_days`, `tags`) and `aws_secretsmanager_secret_version.this` with `secret_string = jsonencode({ access_key_id = aws_iam_access_key.this.id, secret_access_key = aws_iam_access_key.this.secret })`.
  - `README.md`:
    - It stands in for a customer's delivery target (spec §7.1, Q3).
    - The key is a user credential by design, sealed into an S3 connection by R-10's seed, exactly as a customer enters theirs.
    - The key and its secret sit in state (I-154).
    - Write-only permissions: the server-side copy falls back to streaming.
    - It is not for production.

- [ ] **Step 4: Run to verify they pass.** Run the Task 1 Step 4 command. Expected: demo-destination `9 passed, 0 failed` on both engines; the rest stays clean.

- [ ] **Step 5: Commit**

```bash
git add infra/terraform/modules/demo-destination
git commit -m "R-5: demo-destination module — second bucket, write-only IAM user, key in Secrets Manager"
```

---

### Task 5: `stac-higher` composition + `helm_values`

**Files:**
- Create: `infra/terraform/modules/stac-higher/{versions.tf,variables.tf,main.tf,outputs.tf,README.md}`
- Test: `infra/terraform/modules/stac-higher/tests/stac-higher.tftest.hcl`

**Interfaces:**
- Consumes every child interface: R-4's `kms`, `storage`, `secrets`, `registry`, and Tasks 1–4.
- Produces, inputs. Defaults equal the child's unless stated.
  - Common: `name_prefix` (shared validation), `tags`.
  - kms: `kms_deletion_window_in_days`.
  - storage:
    - `app_url`: string, required; `^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$`. Message: "app_url must be https://host[:port] with no path or trailing slash; it becomes the bucket CORS origin and the Cognito callback base."
    - `cors_allowed_origins` = `null` (null means `[app_url]`).
    - `bucket_name` (storage `name`), `bucket_force_destroy`.
  - secrets: `secrets_recovery_window_in_days`. Also passed to demo-destination.
  - registry: `ghcr_credentials_secret_arn` (required), `enable_dockerhub`, `dockerhub_credentials_secret_arn`, `enable_quay`, `quay_credentials_secret_arn`, `ecr_prefixes`, `image_tag_mutability`.
  - database:
    - `vpc_id`, `db_subnet_ids` and `db_allowed_security_group_ids` (required);
    - `db_instance_class`, `db_engine_version`, `db_name`, `db_master_username`;
    - `db_allocated_storage`, `db_max_allocated_storage`;
    - `db_multi_az`, `db_deletion_protection`, `db_backup_retention_period`, `db_backup_window`, `db_maintenance_window`, `db_skip_final_snapshot`;
    - `db_parameter_overrides`, `db_ca_cert_identifier`, `db_performance_insights_enabled`, `db_apply_immediately`.
  - identity: `workload_identity`, `cluster_name`, `oidc_provider_arn`, `namespace`, `service_accounts`.
  - cognito:
    - `enable_cognito` = `false`;
    - `cognito_callback_urls` = `null` (null means `["${app_url}/api/auth/callback"]`);
    - `cognito_logout_urls` = `null` (null means `["${app_url}/"]`);
    - `cognito_domain_prefix`, `cognito_role_groups`, `cognito_data_groups`, `cognito_deletion_protection`, `cognito_access_token_validity_minutes`, `cognito_refresh_token_validity_days`.
  - demo-destination: `enable_demo_destination` = `false`, `demo_destination_bucket_name` = `"demo-destination"`, `demo_destination_force_destroy`.
- Produces, child calls: `module.kms`, `module.storage`, `module.secrets`, `module.registry`, `module.database`, `module.identity`, `module.cognito` (`count = var.enable_cognito ? 1 : 0`) and `module.demo_destination` (`count`).
- Produces, outputs:
  - `kms_key_arn`, `kms_alias_name`.
  - `bucket_name`, `bucket_arn`.
  - `secret_arns`, `secret_names`, `db_role_names`.
  - `registry_host`, `mirror_prefixes`, `repository_creation_role_arn`.
  - `db_address`, `db_port`, `db_name`, `db_master_user_secret_arn`, `db_security_group_id`, `db_ca_cert_identifier`.
  - `role_arns`, `process_run_role_arn`, `pod_identity_association_ids`.
  - `cognito_user_pool_id`, `cognito_issuer_url`, `cognito_client_id`, `cognito_hosted_domain`, `cognito_claims_mapping`, `cognito_callback_urls`, `cognito_logout_urls`. Each is `one(module.cognito[*].<x>)`, so null when off.
  - `demo_destination_bucket_name`, `demo_destination_user_name`, `demo_destination_credentials_secret_arn` (null when off).
  - `s3_prefix_list_cidrs`, `external_secrets_policy_json`.
  - **`helm_values`** (object) and **`helm_values_yaml`** (`yamlencode(local.helm_values)`).
- **The `helm_values` contract** (exact keys; R-6 consumes these):

```yaml
global:
  imageRegistry: <registry.ghcr_mirror_prefix>          # <acct>.dkr.ecr.<region>.<dns>/ghcr
publicUrls:
  app: <var.app_url>
  objectStore: https://s3.<region>.<dns_suffix>          # app S3_ENDPOINT, virtual-hosted
aws:
  partition: <partition>
  region: <region>
  bucket: <storage.bucket_name>
  kmsKeyArn: <kms.key_arn>
  processRunRoleArn: <identity.process_run_role_arn>     # PROCESS_STS_ROLE_ARN
  processMaxCredentialSeconds: 3600                      # PROCESS_MAX_CREDENTIAL_SECONDS, app + pipeline
  s3PrefixListCidrs: [<sorted CIDRs>]                    # runs NetworkPolicy (§6.5)
registry:
  host: <registry.registry_host>
  mirrorPrefixes: <registry.mirror_prefixes>             # {"ghcr.io": …, ["docker.io", "quay.io"]}; R-8 executor
database:
  host: <database.address>
  port: <database.port>
  name: <database.db_name>
  masterSecretArn: <database.master_user_secret_arn>     # bootstrap Job only (§7.2)
  caCertIdentifier: <database.ca_cert_identifier>        # which regional CA bundle database.caBundle must hold
identity:
  mode: pod-identity | irsa
  namespace: <identity.namespace>
  serviceAccounts: { app, pipeline, titiler, cubeServer }
  roleArns:        { app, pipeline, titiler, cubeServer }  # SA annotation in irsa mode
secrets:
  mode: external-secrets
  arns: { db_stac_higher_owner, db_stac_api, db_stac_reader, db_stac_proxy_policy, db_cube_server,
          session_secret, catalog_bff_shared_secret, internal_api_token, proxy_auth_token,
          credentials_master_key }                       # the secrets module's ten keys, verbatim
auth:                                                    # only when enable_cognito
  mode: oidc
  issuer: <cognito.issuer_url>
  clientId: <cognito.client_id>
  audienceClaim: client_id
  audiences: [<cognito.client_id>]
  claimsMapping: <cognito.claims_mapping>
  hostedDomain: <cognito.hosted_domain>
demoDestination:                                         # only when enable_demo_destination
  bucket: <demo_destination.bucket_name>
  region: <region>
  credentialsSecretArn: <demo_destination.credentials_secret_arn>
```

- [ ] **Step 1: Write the failing tests.**
  - Mock block plus these defaults:
    - `mock_resource "aws_kms_key"` (gov key ARN);
    - `mock_resource "aws_s3_bucket"` (`id = "stac-test-platform"`, `arn = "arn:aws-us-gov:s3:::stac-test-platform"`). Required: `module.storage.bucket_arn` flows into `identity`'s validated `bucket_arn` under `apply`, and a random mock ARN would fail that validation;
    - `mock_resource "aws_iam_role"` (`arn:aws-us-gov:iam::000000000000:role/mock`);
    - `mock_resource "aws_db_instance"` (Task 1's defaults);
    - `mock_resource "aws_cognito_user_pool"` (`id = "us-gov-west-1_EXAMPLE"`);
    - `mock_resource "aws_secretsmanager_secret"` (`arn = "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:stac-test/mock-AbCdEf"`);
    - `mock_data "aws_ec2_managed_prefix_list"` (`entries = [{ cidr = "192.0.2.0/24", description = "placeholder (RFC 5737)" }]`).
  - `variables`: `name_prefix = "stac-test"`, `app_url = "https://app.example.com"`, `ghcr_credentials_secret_arn` (R-4's gov placeholder), `vpc_id`, `db_subnet_ids`, `db_allowed_security_group_ids` (Global Constraints placeholders) and `cluster_name = "example-cluster"`.
  - Let `hv = output.helm_values`.

  Runs:
  - `helm_values_top_level_keys` (plan): `keys(hv) == ["aws", "database", "global", "identity", "publicUrls", "registry", "secrets"]`.
  - `helm_values_nested_keys` (plan):
    - `keys(hv.aws) == ["bucket", "kmsKeyArn", "partition", "processMaxCredentialSeconds", "processRunRoleArn", "region", "s3PrefixListCidrs"]`;
    - `keys(hv.database) == ["caCertIdentifier", "host", "masterSecretArn", "name", "port"]`;
    - `keys(hv.identity) == ["mode", "namespace", "roleArns", "serviceAccounts"]`, and `keys(hv.identity.roleArns) == ["app", "cubeServer", "pipeline", "titiler"]`, the same for `serviceAccounts`;
    - `keys(hv.secrets) == ["arns", "mode"]`, `keys(hv.registry) == ["host", "mirrorPrefixes"]`, `keys(hv.global) == ["imageRegistry"]`, `keys(hv.publicUrls) == ["app", "objectStore"]`.
  - `helm_values_values` (apply):
    - `hv.aws.partition == "aws-us-gov"`, `hv.aws.region == "us-gov-west-1"`.
    - `hv.publicUrls.objectStore == "https://s3.us-gov-west-1.dns-suffix.example"`, `hv.publicUrls.app == "https://app.example.com"`.
    - `hv.aws.processMaxCredentialSeconds == 3600`, `hv.aws.s3PrefixListCidrs == ["192.0.2.0/24"]`.
    - `hv.aws.processRunRoleArn == "arn:aws-us-gov:iam::000000000000:role/stac-test-process-run"`, `hv.identity.roleArns.cubeServer == "arn:aws-us-gov:iam::000000000000:role/stac-test-cube-server"`.
    - `hv.identity.mode == "pod-identity"`, `hv.identity.namespace == "stac-higher"`.
    - `hv.identity.serviceAccounts == { app = "stac-higher-app", cubeServer = "stac-higher-cube-server", pipeline = "stac-higher-pipeline", titiler = "stac-higher-titiler" }`. Compare with `jsonencode`.
    - `hv.global.imageRegistry == "000000000000.dkr.ecr.us-gov-west-1.dns-suffix.example/ghcr"`, `keys(hv.registry.mirrorPrefixes) == ["ghcr.io"]`.
    - `hv.secrets.mode == "external-secrets"`, and `keys(hv.secrets.arns)` equals the ten keys, sorted.
    - `hv.database.host == "stac-test-db.example.com"`, `hv.database.port == 5432`, `hv.database.name == "postgis"`, `hv.database.caCertIdentifier == "rds-ca-rsa2048-g1"`, `hv.database.masterSecretArn` equals the mock master secret ARN.
  - `helm_values_yaml_roundtrip` (apply): `jsonencode(yamldecode(output.helm_values_yaml)) == jsonencode(output.helm_values)`.
  - `s3_prefix_list_lookup` (plan): `data.aws_ec2_managed_prefix_list.s3.name == "example.dns-suffix.us-gov-west-1.s3"`.
  - `optional_modules_off` (plan): `output.cognito_client_id == null`, `output.demo_destination_bucket_name == null`, and `!contains(keys(hv), "auth") && !contains(keys(hv), "demoDestination")`.
  - `cognito_and_demo_on` (apply, `enable_cognito = true`, `enable_demo_destination = true`):
    - `keys(hv) == ["auth", "aws", "database", "demoDestination", "global", "identity", "publicUrls", "registry", "secrets"]`.
    - `keys(hv.auth) == ["audienceClaim", "audiences", "claimsMapping", "clientId", "hostedDomain", "issuer", "mode"]`, `hv.auth.mode == "oidc"`, `hv.auth.audienceClaim == "client_id"`, `hv.auth.audiences == [hv.auth.clientId]`.
    - `hv.auth.issuer == "https://cognito-idp.us-gov-west-1.dns-suffix.example/us-gov-west-1_EXAMPLE"`.
    - `keys(hv.demoDestination) == ["bucket", "credentialsSecretArn", "region"]`, `hv.demoDestination.region == "us-gov-west-1"`.
    - `output.cognito_callback_urls == ["https://app.example.com/api/auth/callback"]`, `output.cognito_logout_urls == ["https://app.example.com/"]`.
  - `irsa_mode` (plan, `workload_identity = "irsa"`, `oidc_provider_arn` placeholder, `cluster_name = null`): `hv.identity.mode == "irsa"`.
  - `external_secrets_policy` (apply, `enable_demo_destination = true`). With `p = jsondecode(output.external_secrets_policy_json)`, the Sids are `{ReadPlatformSecrets, DecryptPlatformSecrets}`:
    - `ReadPlatformSecrets`: `toset(Action) == toset(["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"])`, and `Resource` contains the mock master secret ARN and the mock secret ARN.
    - `DecryptPlatformSecrets`: `Action == ["kms:Decrypt"]` on the key, with `kms:ViaService == "secretsmanager.us-gov-west-1.dns-suffix.example"`.
  - `rejects_app_url_with_path` (`"https://app.example.com/"`): `expect_failures = [var.app_url]`.
  - `rejects_bad_name_prefix`.

- [ ] **Step 2: Run to verify they fail.** Use the Task 2 Step 2 command with `stac-higher`. Expected: FAIL.

- [ ] **Step 3: Implement the module.**
  - `versions.tf` adds `random ~> 3.6`, because the `secrets` child needs it.
  - Data sources: `aws_partition`, `aws_region`, and `aws_ec2_managed_prefix_list.s3` with `name = "${join(".", reverse(split(".", local.dns_suffix)))}.${local.region}.s3"`.
  - Locals:
    - `cors_allowed_origins = var.cors_allowed_origins == null ? [var.app_url] : var.cors_allowed_origins`, and the two Cognito URL defaults written the same way (Rulings). Do not use `coalesce`: an explicit `[]` must stay `[]`, meaning no CORS.
    - `sa_helm = { app = …, pipeline = …, titiler = …, cubeServer = var.service_accounts.cube_server }`, and the same for `roleArns` from `module.identity.role_arns`.
    - `auth` and `demo` are an object when on and `null` when off. Read with `module.cognito[0].…` inside the true branch only.
    - `helm_values = merge(local.helm_base, { for k, v in { auth = local.auth, demoDestination = local.demo } : k => v if v != null })`, where `local.helm_base` is the contract above minus `auth`/`demoDestination`. `s3PrefixListCidrs = sort([for e in data.aws_ec2_managed_prefix_list.s3.entries : e.cidr])`.
    - `external_secrets_policy`: `jsonencode`; the Resource list is `concat(values(module.secrets.secret_arns), [module.database.master_user_secret_arn], module.demo_destination[*].credentials_secret_arn)`.
  - Module calls pass every input through. `identity` gets `bucket_arn = module.storage.bucket_arn` and `kms_key_arn = module.kms.key_arn`. `database`, `secrets`, `registry`, `storage` and `demo_destination` get the key ARN.
  - Comment above `local.helm_values`: "Contract with the chart (R-6): keys are fixed; omit, never null (Helm deletes defaults on null). See infra/terraform/README.md."
  - `README.md`:
    - what it composes;
    - a pointer to the `helm_values` contract in `infra/terraform/README.md`;
    - the `app_url` derivations;
    - the ESO policy output and who attaches it.

- [ ] **Step 4: Run to verify they pass.** Run the Task 1 Step 4 command. Expected: stac-higher `11 passed, 0 failed` on both engines; every other module stays green, and trivy is clean.

- [ ] **Step 5: Commit**

```bash
git add infra/terraform/modules/stac-higher
git commit -m "R-5: stac-higher composition — all eight modules, S3 prefix list, ESO read policy, helm_values contract"
```

---

### Task 6: Example root on the composition, infra README, docs, full gates

**Files:**
- Modify: `infra/examples/aws-eks/{main.tf,variables.tf,outputs.tf,README.md}`, `infra/examples/aws-eks/tests/example.tftest.hcl`
- Modify: `infra/terraform/README.md`, `docs/FEATURES.md`, `docs/ISSUES.md`

**Interfaces:**
- Consumes: Task 5's composition inputs, outputs and the `helm_values` contract.
- Produces, example variables (all defaulted placeholders):
  - `region = "us-east-1"`, `name_prefix = "stac-higher-example"`, `app_url = "https://app.example.com"`, `ghcr_credentials_secret_name = "ecr-pullthroughcache/ghcr"`;
  - `vpc_id`, `db_subnet_ids` and `db_allowed_security_group_ids` (placeholders), `cluster_name = "example-cluster"`;
  - `enable_cognito = true`, `enable_demo_destination = true`.
- Produces, example outputs: `helm_values`, `helm_values_yaml`, `external_secrets_policy_json`, `kms_key_arn`, `bucket_name`, `secret_arns`, `mirror_prefixes`.

- [ ] **Step 1: Rewrite the example test.**
  - Mock block: R-4's (keep the `aws_secretsmanager_secret` data default; the `aws_s3_bucket` default gains `arn = "arn:aws-us-gov:s3:::stac-higher-example-platform"` for the reason given in Task 5), plus Task 5's other resource and data defaults.
  - Run `wiring` (apply), keeping R-4's checks against the composition:
    - `length(output.secret_arns) == 10`;
    - `keys(output.mirror_prefixes) == ["ghcr.io"]`;
    - `module.stac_higher.secret_names["session_secret"] == "stac-higher-example/platform/session_secret"`.
    - Drop the tautological bucket-name assertion (#159).
  - Run `helm_values` (apply):
    - `keys(output.helm_values) == ["auth", "aws", "database", "demoDestination", "global", "identity", "publicUrls", "registry", "secrets"]`;
    - `output.helm_values.identity.mode == "pod-identity"`;
    - `output.helm_values.publicUrls.app == "https://app.example.com"`;
    - `jsonencode(yamldecode(output.helm_values_yaml)) == jsonencode(output.helm_values)`.

  Run `tofu -chdir=infra/examples/aws-eks init -backend=false -input=false && tofu -chdir=infra/examples/aws-eks test`. Expected: FAIL (`module.stac_higher` not declared).

- [ ] **Step 2: Rewrite the example.**
  - `main.tf` keeps `provider "aws" { region = var.region }` and the `data "aws_secretsmanager_secret" "ghcr"` lookup, and replaces the four calls with one `module "stac_higher" { source = "../../terraform/modules/stac-higher", … }`.
  - Header comment: copy this file, replace local sources with `git::…?ref=vX.Y.Z`, add a backend, set real values.
  - `README.md`:
    - The render recipe: `tofu output -raw helm_values_yaml > values-aws.yaml`, then `helm upgrade --install stac-higher <chart> -f values-aws.yaml -f values.yaml`. The deployment's own file goes last, so it wins.
    - Attach `external_secrets_policy_json` to the cluster's ESO role.
    - Replace "R-5 replaces…" with the current description.

  Run the Step 1 command. Expected: `2 passed, 0 failed`.

- [ ] **Step 3: `infra/terraform/README.md`.**
  - Module table: add rows for `database`, `identity`, `cognito`, `demo-destination` and `stac-higher` (creates / key inputs / outputs, in the existing column style). Delete "arrive in R-5" and "The composition module arrives in R-5. Until then…".
  - New section `## helm_values contract`:
    - the YAML from Task 5, with one table row per key: source module output → the chart setting or env var it feeds (`S3_ENDPOINT`, `S3_REGION`/`STAGING_S3_REGION`, `S3_BUCKET`/`STAGING_BUCKET`/`PLATFORM_ASSET_BUCKET`, `PROCESS_STS_ROLE_ARN`, `PROCESS_MAX_CREDENTIAL_SECONDS`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `AUTH_BEARER_AUDIENCE_CLAIM`, `AUTH_BEARER_AUDIENCES`, `ALLOWED_JWT_AUDIENCES`, `AUTH_CLAIMS_MAPPING`, the runs NetworkPolicy, the R-8 mirror map, ExternalSecrets);
    - the omit-never-null rule;
    - "keys are a contract with the chart; changing one needs the matching chart change";
    - **on AWS, leave `STAGING_S3_ENDPOINT` blank for the pipeline** (I-152);
    - the chart names its ServiceAccounts from `identity.serviceAccounts`.
  - Prerequisites: add
    - the VPC, private subnets and the cluster security group (cluster repo);
    - `iam:PassRole` + `eks:CreatePodIdentityAssociation` for the deploying principal;
    - the IAM OIDC provider for `irsa`;
    - the ESO role that `external_secrets_policy_json` is attached to;
    - the RDS CA bundle matching `caCertIdentifier`.

- [ ] **Step 4: Docs.**
  - `docs/FEATURES.md`, `### Deployment artifacts (R queue)` table, add:
    `| R-5 · Terraform environment modules + helm_values | ✅ | database (RDS PostgreSQL 17, pgstac parameter group from eoapi-cdk, rds.force_ssl, managed master secret), identity (app/pipeline/titiler/cube-server roles + process-run role; Pod Identity or IRSA), optional cognito and demo-destination, and the stac-higher composition with the helm_values / helm_values_yaml contract (infra/terraform/README.md); example infra/examples/aws-eks/ calls the composition |`
  - `docs/ISSUES.md`, under `## Deployment artifacts (R queue, epic #136)` after I-150. Every entry is 🟡 and has no tracking line unless stated:
    - **I-151 · Run credentials carry no KMS permission.** `session_policy()` lists S3 actions only. On the SSE-KMS bucket, a run's PUT and GET then depend on the key policy's `Principal "*"` + `kms:ViaService` statement applying to the session principal past the session policy. AWS documents that only for policies naming the session ARN. R-11 settles it with one PUT under run credentials. If it is denied, `session_policy()` gains `kms:GenerateDataKey`/`kms:Decrypt` on the platform key (with #158). The run role already grants both. Tracked in: GitHub #158.
    - **I-152 · The pipeline's STS client reuses the S3 endpoint.** `build_sts_client` passes `STAGING_S3_ENDPOINT` as the STS endpoint, so on AWS that variable must stay blank (botocore's defaults). Set to the regional S3 URL, every run fails `RunCredentialsError`. The chart (R-6) leaves it unset on AWS. A separate STS endpoint setting is the fix if a deployment ever needs one.
    - **I-153 · Cognito residuals.**
      - Cognito's discovery document has no `end_session_endpoint`, so `/api/auth/logout` ends only the app session.
      - `cognito:groups` feeds both `groups` and `roles`, so role groups (`stac-operators`) also appear as data groups.
      - Access tokens carry no `email`, so identities show `username`.
    - **I-154 · The demo-destination key is a long-lived IAM user key in state.** Accepted for the test environment (spec §7.1, lead decision 2026-11-02). It is the user-credential path by design, not the platform's own access. Destroy the module with the environment.
    - **I-155 · The Pod Identity trust is not scoped to the cluster.** The roles trust `pods.eks.<dns_suffix>` without `aws:SourceArn`. Any association in the account may bind them, and creating one needs `iam:PassRole`. Adding the cluster-ARN condition is a hardening step to verify at R-11.

- [ ] **Step 5: Full gate run.** From the worktree root (`npm install` first if `node_modules/` is missing):

```bash
tofu fmt -check -recursive -diff infra/terraform && tofu fmt -check -recursive -diff infra/examples
scripts/check-partition-arns.test.sh && scripts/check-partition-arns.sh
scripts/check-terraform.sh tofu && scripts/check-terraform.sh terraform
tflint --init --config "$(pwd)/infra/terraform/.tflint.hcl" && (cd infra && tflint --recursive --config "$(pwd)/terraform/.tflint.hcl")
trivy config --exit-code 1 --severity HIGH,CRITICAL infra/terraform && trivy config --exit-code 1 --severity HIGH,CRITICAL infra/examples
git status --porcelain | grep -E '\.terraform|tfstate|lock\.hcl' || echo "no Terraform artifacts staged"
npm run verify
```

Expected:
- fmt is silent, `partition check self-test: 3/3 passed`, and the checker exits 0.
- Ten directories are green on both engines, with test totals:
  - cognito 14, database 17, demo-destination 9, identity 17, kms 8;
  - registry 10, secrets 11, stac-higher 11, storage 13, example 2.
- tflint: "CI validates" (not installed locally).
- Both trivy scans report 0 HIGH/CRITICAL.
- `no Terraform artifacts staged`, and `npm run verify` passes.

- [ ] **Step 6: Commit**

```bash
git add infra/examples/aws-eks infra/terraform/README.md docs/FEATURES.md docs/ISSUES.md
git commit -m "R-5: aws-eks example on the stac-higher composition, helm_values contract docs, I-151..I-155"
```

**PR notes for the lead:**
- The body starts `Closes #141` and lists the gates run.
- Lead-only steps: none. Nothing is applied until R-11.
- R-11 checks:
  - one PUT under run credentials (I-151);
  - the RDS formula parameters accepted at create;
  - the Cognito hosted domain is unique.
- R-6 must consume `helm_values` by the keys above. The issue's acceptance cross-check ("renders through R-6's chart") happens in whichever slice lands second.
- Deviations from spec §7.1, all in Rulings:
  - KMS grants on every S3 role;
  - the app PUT scope narrowed to `staging/` + `assets/`;
  - `external_secrets_policy_json` added;
  - SA names in `helm_values`;
  - `aws.partition` added.
