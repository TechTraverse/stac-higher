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

Every module also takes `name_prefix` and `tags`. `database`, `identity`,
`cognito`, `demo-destination` and the `stac-higher` composition module arrive
in R-5.

## Consuming by git ref

```hcl
module "stac_higher" {
  source = "git::https://github.com/<owner>/<repo>.git//infra/terraform/modules/stac-higher?ref=vX.Y.Z"
  # ...
}
```

The composition module arrives in R-5. Until then a deployment calls the four
modules the same way (`//infra/terraform/modules/kms?ref=vX.Y.Z`, and so on),
as [`../examples/aws-eks/`](../examples/aws-eks/README.md) does with local
paths.

## Deployment prerequisites

- The upstream credential secrets for the registry module (see its
  [README](modules/registry/README.md)).
- An encrypted state backend, because generated secrets live in state
  (`docs/ISSUES.md`, Deployment artifacts).
- The node-role ECR permissions (cluster repo).

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
