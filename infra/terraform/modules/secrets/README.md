# secrets

Ten generated entries in AWS Secrets Manager, all encrypted with the platform KMS key. Terraform generates the values (`random_password`, `random_bytes`); the Helm chart's `secrets.mode: external-secrets` reads them by key in R-6. **The ten keys and name suffixes are a contract and never change.**

## Entries

Name is `<name_prefix>/<suffix>`.

| Key | Name suffix | Value | Read as (consumer) |
|---|---|---|---|
| `db_stac_higher_owner` | `db/stac_higher_owner` | JSON `{"username","password"}`, 48 alnum | `DATABASE_URL` (app, pipeline), migrate Jobs |
| `db_stac_api` | `db/stac_api` | JSON, username `stac_api` | stac-fastapi |
| `db_stac_reader` | `db/stac_reader` | JSON, username `stac_reader` | titiler, tipg |
| `db_stac_proxy_policy` | `db/stac_proxy_policy` | JSON, username `stac_proxy_policy` | auth-proxy `POLICY_DATABASE_URL` |
| `db_cube_server` | `db/cube_server` | JSON, username `cube_server` | cube-server `CUBE_DB_DSN` |
| `session_secret` | `platform/session_secret` | 64 alnum | `SESSION_SECRET` (app) |
| `catalog_bff_shared_secret` | `platform/catalog_bff_shared_secret` | 64 alnum | `CATALOG_BFF_SHARED_SECRET` (app, enforced auth-proxy) |
| `internal_api_token` | `platform/internal_api_token` | 64 alnum | `INTERNAL_API_TOKEN` (app; K-6 admission seam) |
| `credentials_master_key` | `platform/credentials_master_key` | standard base64 of 32 random bytes (44 chars) | `CREDENTIALS_MASTER_KEY` (app, pipeline) |
| `proxy_auth_token` | `platform/proxy_auth_token` | 64 alnum | `PROXY_AUTH_TOKEN` (app) |

## Inputs

| Name | Type | Default | Description |
|---|---|---|---|
| `name_prefix` | string | required | Prefix for secret names. 2-32 chars, lowercase letters, digits, hyphens. |
| `tags` | map(string) | `{}` | Tags applied to every secret. |
| `kms_key_arn` | string | required | Full KMS key ARN (the `kms` module's `key_arn`); a key id or alias is refused. |
| `recovery_window_in_days` | number | `7` | `0`, or 7-30. |

## Outputs

| Name | Description |
|---|---|
| `secret_arns` | Map of key to ARN. |
| `secret_names` | Map of key to Secrets Manager name. |
| `db_role_names` | `stac_higher_owner`, `stac_api`, `stac_reader`, `stac_proxy_policy`, `cube_server` (spec 7.2 order). |

Outputs are ARNs and names only. No secret value is ever output.

## Values live in Terraform state

The generated values are stored in Terraform state, so the deployment's backend must be encrypted and access-restricted. Values are stable across applies: there are no `keepers` and no `timestamp()`.

## Throwaway environments

Set `recovery_window_in_days = 0` for throwaway environments. With a window, a destroy followed by a re-apply inside the window fails because the name is still scheduled for deletion.

## Not here

- The RDS master password: R-5 (`manage_master_user_password`).
- The scanner's Docker Hub token and ECR upstream credentials: supplied by the deployment.

## PROXY_AUTH_TOKEN caveat

`proxy_auth_token` is generated and stored, but how the app and proxy consume it is R-6's call. Leave it unwired in the chart until that decision is made.
