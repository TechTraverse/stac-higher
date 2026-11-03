# cognito

Optional Amazon Cognito user pool for the STAC Higher app: admin-created users, role and data groups, a public PKCE app client, a hosted domain, and a `claims_mapping` output that matches the app's `AUTH_CLAIMS_MAPPING`. The composition (R-5) turns it on with `count` and feeds `helm_values.auth`. With another IdP, leave it off and write `auth` in your own values file.

## What it creates

- `aws_cognito_user_pool`: email as username, email auto-verified, **admin-created users only** (no self sign-up), optional TOTP MFA, password minimum 12 characters, verified-email account recovery, deletion protection on by default.
- `aws_cognito_user_group`: one per `role_groups` entry (default `stac-admins` to `admin`, `stac-operators` to `operator`, `stac-members` to `member`) and one per `data_groups` entry.
- `aws_cognito_user_pool_client`: public (no secret), `code` flow, scopes `openid profile email`, only `ALLOW_REFRESH_TOKEN_AUTH`, token revocation on, user-existence errors hidden. Cognito accepts PKCE on a public client and has no flag to require it; the app always sends a challenge.
- `aws_cognito_user_pool_domain`: `<prefix>.auth.<region>.amazoncognito.com`.

## App settings this module feeds

Settings are described in `docs/auth.md`.

| Setting | Value |
|---|---|
| `OIDC_ISSUER` | `issuer_url` (`https://cognito-idp.<region>.<dns_suffix>/<pool id>`) |
| `OIDC_CLIENT_ID` | `client_id` |
| `OIDC_REDIRECT_URI` | one of `callback_urls` |
| `AUTH_BEARER_AUDIENCE_CLAIM` | `client_id` (Cognito access tokens carry `client_id`, not `aud`) |
| `AUTH_BEARER_AUDIENCES` | `<client id>` (the default `stac-higher` stays in force in `client_id` mode, so list the client id) |
| `AUTH_CLAIMS_MAPPING` | `jsonencode(claims_mapping)` |
| proxy `ALLOWED_JWT_AUDIENCES` | `<client id>`, same semantics as `AUTH_BEARER_AUDIENCES` |

`claims_mapping` is `{ groups = "cognito:groups", roles = "cognito:groups", roleMap = <role_groups>, name = ["name", "email", "username"] }`. `username` is the last name fallback because Cognito access tokens carry no `name` or `email`.

## Notes

- **Users are admin-created.** Create them and add them to groups in the console or CLI; nothing here creates users.
- **Logout ends only the app session.** The Cognito session stays signed in (I-153).
- **The hosted domain must be unique in the region**, and must not contain `aws`, `amazon` or `cognito` (a precondition checks `name_prefix` when `domain_prefix` is null). Its suffix is the commercial form, `amazoncognito.com`, Cognito's own and not the partition's: this module does not claim GovCloud hosted-UI parity (spec section 9). The app reads endpoints from OIDC discovery, so `hosted_domain` is informational.
- **No machine (client-credentials) clients are created.** Push clients that need them are out of scope here.

## Inputs

| Name | Type | Default | Description |
|---|---|---|---|
| `name_prefix` | string | required | Prefix for names. 2-32 chars, lowercase letters, digits, hyphens. |
| `tags` | map(string) | `{}` | Tags applied to every taggable resource. |
| `callback_urls` | list(string) | required | Redirect URIs, at least one; `https://` or `http://localhost`. |
| `logout_urls` | list(string) | `[]` | Sign-out redirect URIs, same rule. |
| `domain_prefix` | string | `null` | Hosted-domain prefix; null uses `name_prefix`. |
| `role_groups` | map(string) | `stac-admins`/`stac-operators`/`stac-members` | Group name to `member`, `operator` or `admin`. |
| `data_groups` | list(string) | `[]` | Extra groups without a role mapping. |
| `deletion_protection` | bool | `true` | Block deletion of the pool. |
| `access_token_validity_minutes` | number | `60` | Access and ID token lifetime, 5-1440. |
| `refresh_token_validity_days` | number | `1` | Refresh token lifetime, 1-3650. |

## Outputs

| Name | Description |
|---|---|
| `user_pool_id`, `user_pool_arn` | The pool. |
| `issuer_url` | OIDC issuer. |
| `client_id` | Public app client ID. |
| `hosted_domain` | Hosted-UI domain. |
| `claims_mapping` | Object for `AUTH_CLAIMS_MAPPING`. |
| `group_names` | Set of every group created. |
| `callback_urls`, `logout_urls` | As registered. |
