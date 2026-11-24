output "registry_host" {
  description = "Private ECR registry host of this account and region (<account>.dkr.ecr.<region>.<dns_suffix>)."
  value       = local.registry_host
}

output "mirror_prefixes" {
  description = "Map of reference registry host (ghcr.io, docker.io, quay.io) to its ECR pull-through prefix (<registry_host>/<prefix>), enabled upstreams only. The process executor's mirror map."
  value       = local.mirror_prefixes
}

output "ghcr_mirror_prefix" {
  description = "ECR pull-through prefix for ghcr.io images."
  value       = local.mirror_prefixes["ghcr.io"]
}

output "repository_creation_role_arn" {
  description = "ARN of the IAM role ECR uses to create cached repositories."
  value       = aws_iam_role.repository_creation.arn
}
