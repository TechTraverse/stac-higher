# kms

One customer-managed KMS key (with rotation and an alias) shared by every platform service: S3, Secrets Manager, ECR, RDS and CloudWatch Logs. One key for every platform service, by lead decision 2026-11-03.

Partition, DNS suffix, region and account all come from data sources, so the module works unchanged in commercial and GovCloud partitions.

## Inputs

| Name | Type | Default | Description |
|---|---|---|---|
| `name_prefix` | string | required | Prefix for resource names; the alias is `alias/<name_prefix>`. 2-32 chars, lowercase letters, digits, hyphens. |
| `tags` | map(string) | `{}` | Tags applied to the key. |
| `deletion_window_in_days` | number | `30` | Deletion waiting period, 7-30. |

## Outputs

| Name | Description |
|---|---|
| `key_arn` | Full key ARN; pass it as `kms_key_arn` to the other modules. |
| `key_id` | Key id. |
| `alias_name` | `alias/<name_prefix>`. |
| `alias_arn` | Alias ARN. |

## Key policy

| Sid | Principal | Grants |
|---|---|---|
| `AccountRootAdministers` | the account root | `kms:*` (IAM policies in the account then delegate) |
| `UseThroughPlatformServices` | any principal, only via S3, Secrets Manager, ECR or RDS in this region, and only for callers from this account | encrypt, decrypt, re-encrypt, data keys, describe |
| `CreateGrantThroughPlatformServices` | the same principals and scope, and only when `kms:GrantIsForAWSResource` is true | create grant |
| `CloudWatchLogsUse` | the regional CloudWatch Logs service principal, for log groups in this account and region | encrypt, decrypt, re-encrypt, data keys, describe |

The four services are `kms:ViaService` conditions rather than named principals because each service calls KMS on behalf of whichever role or user touched the resource; the condition plus `kms:CallerAccount` keeps use inside this account without enumerating roles that live in the deployment repo.

`kms:CreateGrant` has its own statement so it can also carry `kms:GrantIsForAWSResource = true`, as AWS's default service-key policy does: a service may create the grant it needs for the resource it attaches the key to (an RDS instance, an ECR repository), but no caller can use the service path to grant the key to an arbitrary principal.
