# storage

One S3 bucket per instance: `<name_prefix>-<name>`, encrypted with the platform KMS key, public access blocked, ACLs disabled, TLS-only, versioning off. R-5's `demo-destination` is expected to instantiate this module a second time for the demo bucket (give it a different `name`).

The bucket ARN is built from the current partition, so the module works unchanged in commercial and GovCloud partitions.

## Inputs

| Name | Type | Default | Description |
|---|---|---|---|
| `name_prefix` | string | required | Prefix for resource names. 2-32 chars, lowercase letters, digits, hyphens. |
| `tags` | map(string) | `{}` | Tags applied to the bucket. |
| `name` | string | `"platform"` | Bucket suffix; the bucket is `<name_prefix>-<name>`. 2-30 chars. |
| `kms_key_arn` | string | required | Full KMS key ARN (the `kms` module's `key_arn`); a key id or alias is refused. |
| `cors_allowed_origins` | list(string) | `[]` | Origins allowed to GET, HEAD and PUT directly. Empty means no CORS configuration. |
| `force_destroy` | bool | `false` | Allow destroy to delete a non-empty bucket. |

## Outputs

| Name | Description |
|---|---|
| `bucket_name` | Bucket name. |
| `bucket_arn` | Bucket ARN. |
| `bucket_regional_domain_name` | Regional domain name. |
| `kms_key_arn` | The key ARN passed in. |

## CORS origins

Each origin must be `scheme://host[:port]` only: no path, no trailing slash, no wildcard. That is the form browsers send in the `Origin` header, so anything else would never match. Allowed methods are GET, HEAD and PUT, headers are open, `ETag` is exposed and preflight results cache for 3000 seconds.

## Versioning is off

Under ADR 0011 the `asset_gc` queue is the only byte-deletion path and must actually remove bytes. Versioning would keep a noncurrent copy of every collected object, so garbage collection, retention and archive would free nothing and "deleted" data would stay readable. The trivy finding for it is suppressed in `main.tf` with this reason.

## No lifecycle expiry

The only lifecycle rule aborts incomplete multipart uploads after 7 days. Object deletion belongs to `asset_gc` and the pipeline's staging sweep, never to S3 lifecycle.

## No access logging

Access logging is not configured (trivy `AWS-0089`, LOW, is below the gate). A deployment that wants logs supplies its own log bucket.
