# registry

ECR pull-through cache for the upstream registries the platform's images come from: one cache rule and one repository creation template per enabled upstream, plus the IAM role ECR uses to create the cached repositories. GHCR is always on; Docker Hub and Quay are opt-in. Cached repositories are encrypted with the platform KMS key. Hostnames, ARNs and service principals come from the current partition, region and account, so the module works unchanged in commercial and GovCloud partitions.

## Inputs

| Name | Type | Default | Description |
|---|---|---|---|
| `name_prefix` | string | required | Prefix for resource names. 2-32 chars, lowercase letters, digits, hyphens. |
| `tags` | map(string) | `{}` | Tags applied to the role and, as `resource_tags`, to the creation templates. |
| `kms_key_arn` | string | required | Full KMS key ARN (the `kms` module's `key_arn`); a key id or alias is refused. |
| `ghcr_credentials_secret_arn` | string | required | Secret holding the GHCR credentials. |
| `enable_dockerhub` | bool | `false` | Cache Docker Hub (`registry-1.docker.io`). Requires the credential below. |
| `dockerhub_credentials_secret_arn` | string | `null` | Secret holding the Docker Hub credentials. |
| `enable_quay` | bool | `false` | Cache Quay (`quay.io`). |
| `quay_credentials_secret_arn` | string | `null` | Optional: Quay needs none for public images. |
| `ecr_prefixes` | object | `{ ghcr = "ghcr", dockerhub = "docker-hub", quay = "quay" }` | ECR repository prefix per upstream, 2-30 chars each. Change one when the account already caches that upstream under the default prefix. |
| `image_tag_mutability` | string | `"MUTABLE"` | `MUTABLE` or `IMMUTABLE`. MUTABLE is ECR's recommendation for pull-through, so cached tags can refresh. |

## Outputs

| Name | Description |
|---|---|
| `registry_host` | `<account>.dkr.ecr.<region>.<dns_suffix>`. |
| `mirror_prefixes` | Map of reference host (`ghcr.io`, `docker.io`, `quay.io`) to `<registry_host>/<prefix>`, enabled upstreams only. |
| `ghcr_mirror_prefix` | `mirror_prefixes["ghcr.io"]`. |
| `repository_creation_role_arn` | Role ECR assumes to create cached repositories. |

`mirror_prefixes` is the mirror map the Kubernetes process executor (R-8) consumes.

## Deployment prerequisites

Create each upstream secret before applying. The deployment owns them; this module only references their ARNs.

- Name: `ecr-pullthroughcache/<name>` (ECR accepts no other prefix).
- Value: JSON `{"username": "...", "accessToken": "..."}`.
- Encryption: the **default `aws/secretsmanager` key**. ECR rejects a customer managed key here.
- Same account and region as the cache rules.

## Cluster prerequisite

The node role that pulls images needs `ecr:BatchImportUpstreamImage` and `ecr:CreateRepository` for the first, uncached pull. That belongs to the cluster repo; R-8 tests a by-digest uncached pull (spec section 5).

## Docker Hub official images

Official Docker Hub images go through the cache with the `library/` path segment (`<registry_host>/docker-hub/library/<image>`). R-8's mirror map accounts for it.

## No scanning configuration

The module sets no account-level ECR scanning configuration; that is a registry-wide setting, not part of the cache (see the epic's rulings).
