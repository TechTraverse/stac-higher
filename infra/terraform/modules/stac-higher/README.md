# stac-higher

The composition module: one call that stands up the AWS side of a stac-higher
deployment and emits the values the Helm chart needs.

## What it composes

Always: `kms`, `storage` (platform bucket), `secrets`, `registry`, `database`,
`identity`. Optional: `cognito` (`enable_cognito`) and `demo-destination`
(`enable_demo_destination`). Optional modules use `count`; their outputs are
`null` when off, and their `helm_values` sections are omitted (never null).

## `helm_values`

The `helm_values` output (and `helm_values_yaml`) is the contract with the
chart. Its keys are fixed; see `infra/terraform/README.md`. Service-account and
namespace names are passed through so the chart renders exactly the names the
Pod Identity associations (or IRSA trust policies) are bound to.

## `app_url` derivations

`app_url` must be `https://host[:port]` with no path or trailing slash. From it:

- bucket CORS origins default to `[app_url]` (`cors_allowed_origins = null`;
  an explicit `[]` means no CORS);
- Cognito callback URLs default to `["<app_url>/api/auth/callback"]` and
  logout URLs to `["<app_url>/"]`;
- `helm_values.publicUrls.app` is `app_url`.

## External Secrets policy

`external_secrets_policy_json` is an IAM policy that reads the ten platform
secrets, the RDS-managed master secret and (when enabled) the demo destination
secret, and decrypts them with the platform key through Secrets Manager only.
The deployment attaches it to the External Secrets Operator's role (this
module does not create that role).
