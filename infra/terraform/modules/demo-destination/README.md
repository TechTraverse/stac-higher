# demo-destination

Optional. A second bucket (a `storage` instance without CORS), a write-only IAM user and the user's access key in a Secrets Manager entry. It stands in for a customer's delivery target (spec §7.1, Q3).

The key is a user credential by design. R-10's seed seals it into an S3 connection exactly as a customer enters theirs; the deployment never hands it to a workload role.

## Permissions

The user can `s3:PutObject` and `s3:AbortMultipartUpload` on the bucket's objects, `s3:ListBucket` on the bucket (the connection test lists), and `kms:GenerateDataKey` + `kms:Decrypt` through S3. It has no Get and no Delete. Delivery's server-side copy needs `GetObject` on the platform bucket, so it fails and falls back to streaming. The bucket uses the platform key.

## State and scope

The access key and its secret sit in Terraform state (I-154). Protect the state backend accordingly. This module is for test environments, not production.

## Inputs and outputs

See `variables.tf` and `outputs.tf`. Outputs name the bucket, the user and the secret; the key itself is never an output. The secret value is `{"access_key_id","secret_access_key"}`.
