output "kms_key_arn" {
  description = "ARN of the platform KMS key."
  value       = module.kms.key_arn
}

output "bucket_name" {
  description = "Name of the platform object-storage bucket."
  value       = module.storage.bucket_name
}

output "secret_arns" {
  description = "ARNs of the platform secrets, keyed by secret name."
  value       = module.secrets.secret_arns
}

output "mirror_prefixes" {
  description = "ECR pull-through mirror prefixes, keyed by upstream registry host."
  value       = module.registry.mirror_prefixes
}
