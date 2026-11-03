output "kms_key_arn" {
  description = "ARN of the platform KMS key."
  value       = module.kms.key_arn
}

output "kms_alias_name" {
  description = "Alias of the platform KMS key."
  value       = module.kms.alias_name
}

output "bucket_name" {
  description = "Name of the platform bucket."
  value       = module.storage.bucket_name
}

output "bucket_arn" {
  description = "ARN of the platform bucket."
  value       = module.storage.bucket_arn
}

output "secret_arns" {
  description = "Secrets Manager ARNs of the generated platform secrets, keyed by logical name."
  value       = module.secrets.secret_arns
}

output "secret_names" {
  description = "Secrets Manager names of the generated platform secrets, keyed by logical name."
  value       = module.secrets.secret_names
}

output "db_role_names" {
  description = "Database login roles the chart's bootstrap Job creates, whose passwords the secrets module generates."
  value       = module.secrets.db_role_names
}

output "registry_host" {
  description = "Private ECR registry host."
  value       = module.registry.registry_host
}

output "mirror_prefixes" {
  description = "Upstream registry host to ECR mirror prefix."
  value       = module.registry.mirror_prefixes
}

output "repository_creation_role_arn" {
  description = "ARN of the role ECR assumes to create cached repositories."
  value       = module.registry.repository_creation_role_arn
}

output "db_address" {
  description = "Database endpoint hostname."
  value       = module.database.address
}

output "db_port" {
  description = "Database port."
  value       = module.database.port
}

output "db_name" {
  description = "Initial database name."
  value       = module.database.db_name
}

output "db_master_user_secret_arn" {
  description = "ARN of the RDS-managed master user secret (read only by the chart's bootstrap Job)."
  value       = module.database.master_user_secret_arn
}

output "db_security_group_id" {
  description = "ID of the database security group."
  value       = module.database.security_group_id
}

output "db_ca_cert_identifier" {
  description = "RDS CA certificate identifier of the instance."
  value       = module.database.ca_cert_identifier
}

output "role_arns" {
  description = "IAM role ARN per workload (snake_case keys: app, pipeline, titiler, cube_server)."
  value       = module.identity.role_arns
}

output "process_run_role_arn" {
  description = "ARN of the role the pipeline assumes to mint process-run credentials."
  value       = module.identity.process_run_role_arn
}

output "pod_identity_association_ids" {
  description = "EKS Pod Identity association IDs per workload (empty in irsa mode)."
  value       = module.identity.pod_identity_association_ids
}

output "cognito_user_pool_id" {
  description = "Cognito user pool ID; null when enable_cognito is false."
  value       = one(module.cognito[*].user_pool_id)
}

output "cognito_issuer_url" {
  description = "OIDC issuer URL; null when enable_cognito is false."
  value       = one(module.cognito[*].issuer_url)
}

output "cognito_client_id" {
  description = "App client ID; null when enable_cognito is false."
  value       = one(module.cognito[*].client_id)
}

output "cognito_hosted_domain" {
  description = "Hosted UI domain; null when enable_cognito is false."
  value       = one(module.cognito[*].hosted_domain)
}

output "cognito_claims_mapping" {
  description = "AUTH_CLAIMS_MAPPING object; null when enable_cognito is false."
  value       = one(module.cognito[*].claims_mapping)
}

output "cognito_callback_urls" {
  description = "Registered OAuth redirect URIs; null when enable_cognito is false."
  value       = one(module.cognito[*].callback_urls)
}

output "cognito_logout_urls" {
  description = "Registered sign-out URLs; null when enable_cognito is false."
  value       = one(module.cognito[*].logout_urls)
}

output "demo_destination_bucket_name" {
  description = "Demo destination bucket name; null when enable_demo_destination is false."
  value       = one(module.demo_destination[*].bucket_name)
}

output "demo_destination_user_name" {
  description = "Demo destination write-only IAM user name; null when enable_demo_destination is false."
  value       = one(module.demo_destination[*].user_name)
}

output "demo_destination_credentials_secret_arn" {
  description = "ARN of the demo destination credentials secret; null when enable_demo_destination is false."
  value       = one(module.demo_destination[*].credentials_secret_arn)
}

output "s3_prefix_list_cidrs" {
  description = "CIDRs of the regional S3 prefix list, sorted."
  value       = local.helm_base.aws.s3PrefixListCidrs
}

output "external_secrets_policy_json" {
  description = "IAM policy JSON that lets the External Secrets Operator read the platform, database-master and demo secrets and decrypt them through Secrets Manager. The deployment attaches it to the operator's role."
  value       = local.external_secrets_policy
}

output "helm_values" {
  description = "Values for the stac-higher chart (R-6), with fixed keys; auth and demoDestination are omitted when their module is off."
  value       = local.helm_values
}

output "helm_values_yaml" {
  description = "helm_values as YAML, ready for a values file."
  value       = yamlencode(local.helm_values)
}
