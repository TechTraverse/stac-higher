# Terraform / OpenTofu modules

Tracking: epic #136. Spec:
[`../../docs/superpowers/specs/2026-11-02-deployment-artifacts-design.md`](../../docs/superpowers/specs/2026-11-02-deployment-artifacts-design.md)
§7; ADR [0024](../../docs/decisions/0024-deployment-artifacts.md).

The modules a deployment repo consumes to stand up the AWS side of STAC
Higher. This repo publishes modules only; it holds no account, state or
deployment.

## Rules

- No account ids, ARNs, hostnames, CIDRs or secrets anywhere in this repo.
  Placeholders are `000000000000` and `example.com`.
- Modules never configure a provider, backend or account.
- Every ARN comes from the partition (`data.aws_partition`), and service
  principals and hostnames from its `dns_suffix`. CI refuses a literal
  `arn:aws:` or `amazonaws.com` in Terraform files
  (`scripts/check-partition-arns.sh`).
- OpenTofu >= 1.10 or Terraform >= 1.10.

## Modules

| Module | Creates | Key inputs | Outputs |
|---|---|---|---|
| [`kms`](modules/kms/README.md) | The platform KMS key and alias | `name_prefix`, `deletion_window_in_days` | `key_arn`, `key_id`, `alias_name`, `alias_arn` |
| [`storage`](modules/storage/README.md) | The platform bucket: SSE-KMS, public access blocked, versioning off, TLS-only, CORS | `name_prefix`, `kms_key_arn`, `cors_allowed_origins`, `name`, `force_destroy` | `bucket_name`, `bucket_arn`, `bucket_regional_domain_name`, `kms_key_arn` |
| [`secrets`](modules/secrets/README.md) | Per-role DB passwords and platform secrets in Secrets Manager (KMS) | `name_prefix`, `kms_key_arn`, `recovery_window_in_days` | `secret_arns`, `secret_names`, `db_role_names` |
| [`registry`](modules/registry/README.md) | ECR pull-through cache rules, repository creation templates, mirror prefixes | `name_prefix`, `kms_key_arn`, `ghcr_credentials_secret_arn`, `enable_dockerhub`, `enable_quay`, `ecr_prefixes`, `image_tag_mutability` | `registry_host`, `mirror_prefixes`, `ghcr_mirror_prefix`, `repository_creation_role_arn` |
| [`database`](modules/database/README.md) | RDS PostgreSQL 17: subnet group, security group (5432 from allowed groups only), pgstac parameter group with `rds.force_ssl`, RDS-managed master secret | `name_prefix`, `kms_key_arn`, `vpc_id`, `subnet_ids`, `allowed_security_group_ids`, `instance_class`, `parameter_overrides`, `ca_cert_identifier`, `apply_immediately` | `address`, `port`, `db_name`, `master_user_secret_arn`, `security_group_id`, `ca_cert_identifier` |
| [`identity`](modules/identity/README.md) | App, pipeline, titiler and cube-server roles, the process-run role, and Pod Identity associations or IRSA trust | `name_prefix`, `kms_key_arn`, `bucket_arn`, `workload_identity`, `cluster_name`, `oidc_provider_arn`, `namespace`, `service_accounts` | `role_arns`, `process_run_role_arn`, `pod_identity_association_ids`, `namespace`, `service_accounts` |
| [`cognito`](modules/cognito/README.md) | Optional user pool (admin-created users), role and data groups, public PKCE client, hosted domain | `name_prefix`, `callback_urls`, `logout_urls`, `domain_prefix`, `role_groups`, `data_groups` | `issuer_url`, `client_id`, `hosted_domain`, `claims_mapping`, `user_pool_id` |
| [`demo-destination`](modules/demo-destination/README.md) | Optional second bucket, write-only IAM user, its access key in Secrets Manager | `name_prefix`, `kms_key_arn`, `name`, `force_destroy`, `recovery_window_in_days` | `bucket_name`, `user_name`, `credentials_secret_arn` |
| [`stac-higher`](modules/stac-higher/README.md) | The composition: all of the above (cognito and demo-destination optional), the S3 prefix list lookup and the External Secrets read policy | `name_prefix`, `app_url`, `ghcr_credentials_secret_arn`, `vpc_id`, `db_subnet_ids`, `db_allowed_security_group_ids`, `cluster_name` or `oidc_provider_arn`, `enable_cognito`, `enable_demo_destination` | `helm_values`, `helm_values_yaml`, `external_secrets_policy_json`, plus every module output |

Every module also takes `name_prefix` and `tags`.

## Consuming by git ref

```hcl
module "stac_higher" {
  source = "git::https://github.com/<owner>/<repo>.git//infra/terraform/modules/stac-higher?ref=vX.Y.Z"
  # ...
}
```

A deployment calls the composition once, as
[`../examples/aws-eks/`](../examples/aws-eks/README.md) does with a local path.
The composition's own calls to the other modules are relative, and resolve
because the whole repo is fetched at the ref.

## helm_values contract

The composition's `helm_values` output (and `helm_values_yaml`, the same
object as YAML) is what the Helm chart (R-6) consumes verbatim. Its keys are
fixed; the chart's values schema sets `additionalProperties: false` on `registry`,
`aws`, `publicUrls`, `database`, `identity` (including `roleArns` and
`serviceAccounts`), `demoDestination` and `secrets.arns`, while `global`,
`auth` and `secrets` themselves are open. **Changing a key is a breaking
change and needs the matching chart change.**

```yaml
global:
  imageRegistry: <account>.dkr.ecr.<region>.<dns_suffix>/ghcr
publicUrls:
  app: https://app.example.com
  objectStore: https://s3.<region>.<dns_suffix>
aws:
  partition: aws
  region: <region>
  bucket: <name_prefix>-platform
  kmsKeyArn: <platform key arn>
  processRunRoleArn: <run role arn>
  processMaxCredentialSeconds: 3600
  s3PrefixListCidrs: [<cidr>, ...]
registry:
  host: <account>.dkr.ecr.<region>.<dns_suffix>
  mirrorPrefixes: { ghcr.io: <prefix> }
database:
  host: <db address>
  port: 5432
  name: postgis
  masterSecretArn: <rds-managed secret arn>
  caCertIdentifier: rds-ca-rsa2048-g1
identity:
  mode: pod-identity            # or irsa
  namespace: stac-higher
  serviceAccounts: { app: ..., pipeline: ..., titiler: ..., cubeServer: ... }
  roleArns:        { app: ..., pipeline: ..., titiler: ..., cubeServer: ... }
secrets:
  mode: external-secrets
  arns: { <secret name>: <arn>, ... }   # ten platform secrets
auth:                           # only when enable_cognito
  mode: oidc
  issuer: <issuer url>
  clientId: <client id>
  audienceClaim: client_id
  audiences: [<client id>]
  claimsMapping: { groups: ..., roles: ..., roleMap: ..., name: [...] }
  hostedDomain: <prefix>.auth.<region>.amazoncognito.com
demoDestination:                # only when enable_demo_destination
  bucket: <bucket name>
  region: <region>
  credentialsSecretArn: <arn>
```

Status is what the R-6 chart renders today; where this table and the chart
differ, the chart wins.

| Key | Source (module output) | Feeds in the chart | Status |
|---|---|---|---|
| `global.imageRegistry` | `registry.ghcr_mirror_prefix` | the prefix that replaces the leading `ghcr.io` of pulled images | consumed today (`pullRef`) |
| `registry.mirrorPrefixes` | `registry.mirror_prefixes` | pulled images are rewritten through it per upstream host; it is the fallback for `ghcr.io` when `global.imageRegistry` is blank | consumed today (`pullRef`) |
| `registry.host` | `registry.registry_host` | nothing | informational |
| `publicUrls.app` | `app_url` | `OIDC_REDIRECT_URI` (`<app>/api/auth/callback`) | consumed today |
| `publicUrls.objectStore` | partition `dns_suffix`, region | the app's `S3_ENDPOINT` only | consumed today |
| `aws.region` | `data.aws_region` | `AWS_REGION`, `AWS_DEFAULT_REGION`, `S3_REGION`, `STAGING_S3_REGION` | consumed today |
| `aws.bucket` | `storage.bucket_name` | `S3_BUCKET` (app), `STAGING_BUCKET` (pipeline) | consumed today |
| `aws.processRunRoleArn` | `identity.process_run_role_arn` | `PROCESS_STS_ROLE_ARN` | consumed today |
| `aws.processMaxCredentialSeconds` | fixed 3600 (STS role-chaining cap) | `PROCESS_MAX_CREDENTIAL_SECONDS` | consumed today |
| `aws.kmsKeyArn` | `kms.key_arn` | nothing: the bucket's default SSE-KMS applies without a client header | informational |
| `aws.partition` | `data.aws_partition` | nothing: no template builds ARNs from it | informational |
| `aws.s3PrefixListCidrs` | regional S3 managed prefix list | the runs NetworkPolicy egress | consumed by R-8 (not yet rendered) |
| `database.host`, `port`, `name` | `database.address`, `port`, `db_name` | `DATABASE_URL` | consumed today |
| `database.masterSecretArn` | `database.master_user_secret_arn` | the bootstrap Job's master ExternalSecret (`hooks/db-resources.yaml`) | consumed today |
| `database.caCertIdentifier` | `database.ca_cert_identifier` | nothing: names the CA bundle the deployment must supply | informational |
| `identity.mode` | `workload_identity` | `eks.amazonaws.com/role-arn` annotation on app and pipeline ServiceAccounts under `irsa` | consumed today |
| `identity.namespace` | `namespace` | install-time check that the release namespace equals it | consumed today |
| `identity.serviceAccounts.app`, `.pipeline` | `service_accounts` | the ServiceAccount names | consumed today |
| `identity.roleArns.app`, `.pipeline` | `identity.role_arns` | IRSA annotation (`irsa` only) | consumed today |
| `identity.serviceAccounts.titiler`, `.cubeServer`, `identity.roleArns.titiler`, `.cubeServer` | `identity` outputs | the titiler and cube-server ServiceAccounts | consumed by R-7 (not yet rendered) |
| `secrets.mode` | fixed `external-secrets` | the ExternalSecrets mode | consumed today |
| `secrets.arns` | `secrets.secret_arns` | one ExternalSecret `remoteRef.key` per platform secret (`externalsecrets.yaml`); `proxy_auth_token` is accepted and ignored (R-6's ISSUES entry I-159) | consumed today |
| `auth.mode` | fixed `oidc` | `AUTH_MODE` | consumed today |
| `auth.issuer` | `cognito.issuer_url` | `OIDC_ISSUER` | consumed today |
| `auth.clientId` | `cognito.client_id` | `OIDC_CLIENT_ID` | consumed today |
| `auth.audienceClaim` | fixed `client_id` | `AUTH_BEARER_AUDIENCE_CLAIM` | consumed today |
| `auth.audiences` | `[cognito.client_id]` | `AUTH_BEARER_AUDIENCES` (comma-joined) | consumed today |
| `auth.audiences` (proxy side) | same | the auth proxy's `ALLOWED_JWT_AUDIENCES` | consumed by R-7 (catalog plane, not yet rendered) |
| `auth.claimsMapping` | `cognito.claims_mapping` | `AUTH_CLAIMS_MAPPING` (compact JSON) | consumed today |
| `auth.hostedDomain` | `cognito.hosted_domain` | nothing: no app env reads it | informational |
| `demoDestination.bucket`, `.region`, `.credentialsSecretArn` | `demo_destination` outputs | the demo destination; `credentialsSecretArn` is not wired to an ExternalSecret yet | consumed by R-10 (not yet rendered) |

Rules:

- **Omit, never null.** When `enable_cognito` or `enable_demo_destination` is
  off, `auth` or `demoDestination` is left out of the object entirely. A `null`
  in a later Helm values file deletes the chart's default for that key.
- **The chart names its ServiceAccounts from `identity.serviceAccounts`** and
  uses `identity.namespace`. A Pod Identity association bound to a name the
  chart never renders gives the pod no credentials, and the failure shows only
  as S3 AccessDenied.
- **On AWS, leave `STAGING_S3_ENDPOINT` blank for the pipeline** (I-152): the
  pipeline's STS client reuses it, so a regional S3 URL there makes every run
  fail.
- **Deployment-supplied, not emitted by R-5:** `publicUrls.{catalog,titiler,tipg,cubeServer}`
  and `database.caBundle` (spec section 6.2). Hostnames and the CA bundle are
  deployment choices; the deployment puts them in its own values file, passed
  last (see the example README).
- **`secrets.dockerHubArn` is not covered by `external_secrets_policy_json`.**
  The chart's optional `secrets.dockerHubArn` is deployment-supplied, so a
  deployment that sets it must add that ARN to the ESO role's read policy.
- The module READMEs list the inputs behind each value; the composition's
  tests pin the keys.

## Deployment prerequisites

- The upstream credential secrets for the registry module (see its
  [README](modules/registry/README.md)).
- An encrypted state backend, because generated secrets live in state
  (`docs/ISSUES.md`, Deployment artifacts).
- The node-role ECR permissions (cluster repo).
- The VPC, private subnets and the cluster security group (cluster repo).
- `iam:PassRole` and `eks:CreatePodIdentityAssociation` for the deploying
  principal (Pod Identity mode).
- The cluster's IAM OIDC provider (`irsa` mode).
- The External Secrets Operator's role, which `external_secrets_policy_json`
  is attached to.
- The RDS CA bundle matching `caCertIdentifier`, supplied in the
  deployment's values file.

## Validating locally

From the repo root:

```bash
tofu fmt -check -recursive -diff infra/terraform && tofu fmt -check -recursive -diff infra/examples
scripts/check-partition-arns.test.sh && scripts/check-partition-arns.sh
scripts/check-terraform.sh tofu && scripts/check-terraform.sh terraform
tflint --init --config "$(pwd)/infra/terraform/.tflint.hcl" && (cd infra && tflint --recursive --config "$(pwd)/terraform/.tflint.hcl")
trivy config --exit-code 1 --severity HIGH,CRITICAL infra/terraform && trivy config --exit-code 1 --severity HIGH,CRITICAL infra/examples
```

CI (`.github/workflows/infra.yml`) runs the same checks on OpenTofu and
Terraform at 1.10 and current. Nothing here needs an AWS account: the tests
use a mock provider.
