output "helm_values" {
  description = "Values for the stac-higher chart, with fixed keys (see infra/terraform/README.md)."
  value       = module.stac_higher.helm_values
}

output "helm_values_yaml" {
  description = "helm_values as YAML, ready for a values file."
  value       = module.stac_higher.helm_values_yaml
}

output "external_secrets_policy_json" {
  description = "IAM policy JSON to attach to the External Secrets Operator's role."
  value       = module.stac_higher.external_secrets_policy_json
}

output "kms_key_arn" {
  description = "ARN of the platform KMS key."
  value       = module.stac_higher.kms_key_arn
}

output "bucket_name" {
  description = "Name of the platform object-storage bucket."
  value       = module.stac_higher.bucket_name
}

output "secret_arns" {
  description = "ARNs of the platform secrets, keyed by secret name."
  value       = module.stac_higher.secret_arns
}

output "mirror_prefixes" {
  description = "ECR pull-through mirror prefixes, keyed by upstream registry host."
  value       = module.stac_higher.mirror_prefixes
}
