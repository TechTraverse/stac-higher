# database

RDS PostgreSQL 17 for the platform catalog: a DB subnet group, a security group that admits 5432 from the allowed security groups only, a pgstac parameter group with TLS forced, an RDS-managed master secret, and KMS encryption for storage, the secret and Performance Insights. The composition (R-5) passes the instance's address and the master secret ARN to the chart.

## Inputs

| Name | Type | Default | Description |
|---|---|---|---|
| `name_prefix` | string | required | Prefix for resource names; the instance is `<name_prefix>-db`. 2-32 chars, lowercase letters, digits, hyphens. |
| `tags` | map(string) | `{}` | Tags applied to every taggable resource. |
| `kms_key_arn` | string | required | Full KMS key ARN (the `kms` module's `key_arn`); a key id or alias is refused. |
| `vpc_id` | string | required | VPC that holds the security group. |
| `subnet_ids` | list(string) | required | At least two private subnets. |
| `allowed_security_group_ids` | list(string) | required | At least one security group allowed on 5432. No CIDR ingress is offered. |
| `instance_class` | string | `"db.t4g.medium"` | Must start with `db.`. |
| `engine_version` | string | `"17"` | Major or major.minor; major 16 or later. The parameter-group family is `postgres<major>`. |
| `db_name` | string | `"postgis"` | Initial database; the pgstac image and every chart DSN use it. |
| `master_username` | string | `"stac_higher_master"` | Master user; its password is RDS-managed. |
| `allocated_storage` | number | `50` | GiB, at least 20. |
| `max_allocated_storage` | number | `200` | Autoscaling ceiling in GiB; `0` disables it, otherwise at least `allocated_storage`. |
| `multi_az` | bool | `false` | Synchronous standby in a second zone. |
| `deletion_protection` | bool | `true` | Turn off for throwaway environments. |
| `backup_retention_period` | number | `7` | 1-35 days. |
| `backup_window` | string | `null` | UTC `hh:mm-hh:mm`; null lets RDS choose. |
| `maintenance_window` | string | `null` | UTC `ddd:hh:mm-ddd:hh:mm`; null lets RDS choose. |
| `skip_final_snapshot` | bool | `false` | Turn on for throwaway environments. Otherwise the final snapshot is `<name_prefix>-db-final`. |
| `parameter_overrides` | map(string) | `{}` | Merged over the pgstac defaults. `rds.force_ssl` is refused. |
| `ca_cert_identifier` | string | `"rds-ca-rsa2048-g1"` | RDS CA certificate identifier. |
| `performance_insights_enabled` | bool | `true` | Encrypted with `kms_key_arn`. |
| `apply_immediately` | bool | `false` | Apply modifications now instead of in the maintenance window. |

## Outputs

| Name | Description |
|---|---|
| `address` | Database host name. |
| `port` | Database port. |
| `db_name` | Initial database name. |
| `master_username` | Master user name. |
| `master_user_secret_arn` | RDS-managed master secret ARN. |
| `security_group_id` | Database security group ID. |
| `instance_arn` | RDS instance ARN. |
| `instance_identifier` | `<name_prefix>-db`. |
| `parameter_group_name` | pgstac parameter group name. |
| `ca_cert_identifier` | CA identifier in use. |

## pgstac tuning

Source: eoapi-cdk v11.8.1, `lib/database/index.ts`, `PgStacDatabase.getParameters()`. That construct looks up instance memory in a table; this module expresses the same ratios as RDS formulas, so they hold for every instance class. `DBInstanceClassMemory` is what RDS leaves after its own reserve, so the values come out slightly below eoapi's table values (the same basis the AWS defaults use).

| Parameter | Value | Derivation |
|---|---|---|
| `max_connections` | `LEAST({DBInstanceClassMemory/9531392},5000)` | eoapi's formula, also the RDS default |
| `shared_buffers` | `{DBInstanceClassMemory/32768}` | 25 % in 8 kB blocks, also the RDS default |
| `effective_cache_size` | `{DBInstanceClassMemory/10923}` | 75 % in 8 kB blocks |
| `maintenance_work_mem` | `{DBInstanceClassMemory/16384}` | 0.25 x 0.25 of bytes, in kB |
| `work_mem` | `2327` | 9531392 / 4096 kB, exact for every class below the 5000-connection cap (about 44 GiB); above it, use `parameter_overrides` |
| `max_locks_per_transaction` | `1024` | verbatim |
| `temp_buffers` | `16384` | 131072 / 8, verbatim |
| `seq_page_cost` / `random_page_cost` | `1` / `1.1` | verbatim |
| `rds.force_ssl` | `1` | spec 7.2, not from eoapi |

## TLS

`rds.force_ssl = "1"` is in the parameter group and no input can override it: a validation on `parameter_overrides` refuses the key, because every client verifies against the RDS CA. `ca_cert_identifier` names the CA the instance presents (`rds-ca-rsa2048-g1` by default). The deployment supplies the matching region CA bundle as `database.caBundle` in the chart values. `PGSSLMODE=verify-full` is the client side (`docs/backend.md`).

## Master credentials

The master password is RDS-managed (`manage_master_user_password = true`), stored in a Secrets Manager secret encrypted with `kms_key_arn`. Terraform never holds a master password. The chart's bootstrap Job is the only reader of `master_user_secret_arn`. Rotation is safe: the Job re-reads the secret each run (spec 7.2).

## Extensions

RDS ships `postgis`, `btree_gist` and `unaccent`. The chart's bootstrap Job creates them as master before `pypgstac migrate` (spec 7.2). The module installs nothing.

## Static parameters apply at reboot

Every parameter is `pending-reboot`: static parameters require it, and at creation the instance boots with the group anyway. Changing a parameter later takes effect at the next reboot. The group uses `name_prefix` and `create_before_destroy`, so a family change (a new major) swaps groups cleanly.

## Security group

One ingress rule per entry of `allowed_security_group_ids` (5432/tcp). There is no egress rule: RDS needs none, and Terraform removes AWS's default allow-all egress.
