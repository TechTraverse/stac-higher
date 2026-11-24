variable "name_prefix" {
  description = "Prefix for every resource name (bucket, IAM roles, KMS alias, secrets, database identifier)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,30}[a-z0-9]$", var.name_prefix))
    error_message = "name_prefix must be 2-32 characters of lowercase letters, digits and hyphens, starting with a letter and not ending in a hyphen (it becomes part of bucket, IAM role, KMS alias and secret names)."
  }
}

variable "tags" {
  description = "Tags applied to every taggable resource."
  type        = map(string)
  default     = {}
}

variable "app_url" {
  description = "Public https origin of the app. Becomes the bucket CORS origin, the Cognito callback and logout base, and helm_values.publicUrls.app."
  type        = string

  validation {
    condition     = can(regex("^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$", var.app_url))
    error_message = "app_url must be https://host[:port] with no path or trailing slash; it becomes the bucket CORS origin and the Cognito callback base."
  }
}

# kms

variable "kms_deletion_window_in_days" {
  description = "Days AWS waits before deleting the key after it is scheduled for deletion (7-30)."
  type        = number
  default     = 30
}

# storage

variable "cors_allowed_origins" {
  description = "Browser origins allowed to GET, HEAD and PUT objects directly. Null means [app_url]; an explicit empty list means no CORS configuration."
  type        = list(string)
  default     = null
}

variable "bucket_name" {
  description = "Platform bucket suffix; the bucket is <name_prefix>-<bucket_name>."
  type        = string
  default     = "platform"
}

variable "bucket_force_destroy" {
  description = "Allow terraform destroy to delete the platform bucket even when it holds objects."
  type        = bool
  default     = false
}

# secrets

variable "secrets_recovery_window_in_days" {
  description = "Days a deleted secret stays recoverable; 0 deletes immediately (throwaway environments), otherwise 7-30. Also applied to the demo destination's credentials secret."
  type        = number
  default     = 7
}

# registry

variable "ghcr_credentials_secret_arn" {
  description = "ARN of the Secrets Manager secret holding the GHCR credentials (JSON username and accessToken), named ecr-pullthroughcache/... and created by the deployment."
  type        = string
}

variable "enable_dockerhub" {
  description = "Create the Docker Hub pull-through cache rule (requires dockerhub_credentials_secret_arn)."
  type        = bool
  default     = false
}

variable "dockerhub_credentials_secret_arn" {
  description = "ARN of the Docker Hub credentials secret (ecr-pullthroughcache/...); required when enable_dockerhub is true."
  type        = string
  default     = null
}

variable "enable_quay" {
  description = "Create the Quay pull-through cache rule."
  type        = bool
  default     = false
}

variable "quay_credentials_secret_arn" {
  description = "ARN of the Quay credentials secret (ecr-pullthroughcache/...); optional."
  type        = string
  default     = null
}

variable "ecr_prefixes" {
  description = "ECR repository prefix per upstream. Change one when the account already caches that upstream under another rule with the default prefix."
  type = object({
    ghcr      = string
    dockerhub = string
    quay      = string
  })
  default = {
    ghcr      = "ghcr"
    dockerhub = "docker-hub"
    quay      = "quay"
  }
}

variable "image_tag_mutability" {
  description = "Tag mutability of cached repositories (MUTABLE or IMMUTABLE)."
  type        = string
  default     = "MUTABLE"
}

# database

variable "vpc_id" {
  description = "VPC that holds the database security group."
  type        = string
}

variable "db_subnet_ids" {
  description = "Private subnets for the DB subnet group; at least two, in different availability zones."
  type        = list(string)
}

variable "db_allowed_security_group_ids" {
  description = "Security groups (typically the cluster nodes') allowed to reach the database on its port."
  type        = list(string)
}

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.medium"
}

variable "db_engine_version" {
  description = "PostgreSQL engine version; pin the major only (e.g. 17)."
  type        = string
  default     = "17"
}

variable "db_name" {
  description = "Initial database name."
  type        = string
  default     = "postgis"
}

variable "db_master_username" {
  description = "Master user name; its password is RDS-managed in Secrets Manager."
  type        = string
  default     = "stac_higher_master"
}

variable "db_allocated_storage" {
  description = "Initial allocated storage in GiB."
  type        = number
  default     = 50
}

variable "db_max_allocated_storage" {
  description = "Storage autoscaling ceiling in GiB."
  type        = number
  default     = 200
}

variable "db_multi_az" {
  description = "Run a Multi-AZ standby."
  type        = bool
  default     = false
}

variable "db_deletion_protection" {
  description = "Refuse deletion of the instance while true."
  type        = bool
  default     = true
}

variable "db_backup_retention_period" {
  description = "Days of automated backups to keep."
  type        = number
  default     = 7
}

variable "db_backup_window" {
  description = "Preferred daily backup window (UTC, hh24:mi-hh24:mi); null lets RDS choose."
  type        = string
  default     = null
}

variable "db_maintenance_window" {
  description = "Preferred weekly maintenance window (ddd:hh24:mi-ddd:hh24:mi); null lets RDS choose."
  type        = string
  default     = null
}

variable "db_skip_final_snapshot" {
  description = "Skip the final snapshot on destroy (throwaway environments only)."
  type        = bool
  default     = false
}

variable "db_parameter_overrides" {
  description = "Parameter group overrides merged over the pgstac defaults; rds.force_ssl cannot be overridden."
  type        = map(string)
  default     = {}
}

variable "db_ca_cert_identifier" {
  description = "RDS CA certificate identifier; helm_values.database.caCertIdentifier tells the chart which regional bundle to hold."
  type        = string
  default     = "rds-ca-rsa2048-g1"
}

variable "db_performance_insights_enabled" {
  description = "Enable Performance Insights."
  type        = bool
  default     = true
}

variable "db_apply_immediately" {
  description = "Apply modifications immediately instead of in the next maintenance window."
  type        = bool
  default     = false
}

# identity

variable "workload_identity" {
  description = "How pods obtain the roles: \"pod_identity\" (EKS Pod Identity associations) or \"irsa\" (IAM roles for service accounts)."
  type        = string
  default     = "pod_identity"
}

variable "cluster_name" {
  description = "EKS cluster name; required when workload_identity is \"pod_identity\"."
  type        = string
  default     = null
}

variable "oidc_provider_arn" {
  description = "ARN of the cluster's IAM OIDC provider; required when workload_identity is \"irsa\"."
  type        = string
  default     = null
}

variable "namespace" {
  description = "Kubernetes namespace the chart installs into (surfaced as helm_values.identity.namespace)."
  type        = string
  default     = "stac-higher"
}

variable "service_accounts" {
  description = "ServiceAccount name per workload. Must match the names the chart renders; helm_values.identity carries them."
  type = object({
    app         = string
    pipeline    = string
    titiler     = string
    cube_server = string
  })
  default = {
    app         = "stac-higher-app"
    pipeline    = "stac-higher-pipeline"
    titiler     = "stac-higher-titiler"
    cube_server = "stac-higher-cube-server"
  }
}

# cognito

variable "enable_cognito" {
  description = "Create the Cognito user pool and app client, and emit helm_values.auth."
  type        = bool
  default     = false
}

variable "cognito_callback_urls" {
  description = "OAuth redirect URIs. Null means [\"<app_url>/api/auth/callback\"]."
  type        = list(string)
  default     = null
}

variable "cognito_logout_urls" {
  description = "OAuth sign-out URLs. Null means [\"<app_url>/\"]."
  type        = list(string)
  default     = null
}

variable "cognito_domain_prefix" {
  description = "Hosted UI domain prefix; null derives it from name_prefix."
  type        = string
  default     = null
}

variable "cognito_role_groups" {
  description = "Cognito group name to canonical app role (member, operator or admin)."
  type        = map(string)
  default = {
    "stac-admins"    = "admin"
    "stac-operators" = "operator"
    "stac-members"   = "member"
  }
}

variable "cognito_data_groups" {
  description = "Extra Cognito groups that carry data-access scopes only."
  type        = list(string)
  default     = []
}

variable "cognito_deletion_protection" {
  description = "Refuse deletion of the user pool while true."
  type        = bool
  default     = true
}

variable "cognito_access_token_validity_minutes" {
  description = "Access and ID token lifetime in minutes."
  type        = number
  default     = 60
}

variable "cognito_refresh_token_validity_days" {
  description = "Refresh token lifetime in days."
  type        = number
  default     = 1
}

# demo-destination

variable "enable_demo_destination" {
  description = "Create the demo destination bucket, its write-only IAM user and credentials secret, and emit helm_values.demoDestination."
  type        = bool
  default     = false
}

variable "demo_destination_bucket_name" {
  description = "Demo destination bucket suffix; the bucket is <name_prefix>-<demo_destination_bucket_name>."
  type        = string
  default     = "demo-destination"
}

variable "demo_destination_force_destroy" {
  description = "Allow terraform destroy to delete the demo destination bucket even when it holds objects."
  type        = bool
  default     = false
}
