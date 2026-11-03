variable "name_prefix" {
  description = "Prefix for every resource name; roles are <name_prefix>-app, -pipeline, -titiler, -cube-server and -process-run."
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

variable "kms_key_arn" {
  description = "Full ARN of the KMS key that encrypts the platform bucket (the kms module's key_arn)."
  type        = string

  validation {
    condition     = can(regex("^arn:[a-z-]+:kms:[a-z0-9-]+:[0-9]{12}:key/.+$", var.kms_key_arn))
    error_message = "kms_key_arn must be a full KMS key ARN (arn:<partition>:kms:<region>:<account>:key/<id>), not a key id or alias."
  }
}

variable "bucket_arn" {
  description = "ARN of the platform bucket the roles read and write (the storage module's bucket_arn)."
  type        = string

  validation {
    condition     = can(regex("^arn:[a-z-]+:s3:::[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.bucket_arn))
    error_message = "bucket_arn must be an S3 bucket ARN (arn:<partition>:s3:::<bucket>), without an object path."
  }
}

variable "workload_identity" {
  description = "How pods obtain the roles: \"pod_identity\" (EKS Pod Identity associations) or \"irsa\" (IAM roles for service accounts through the cluster's OIDC provider)."
  type        = string
  default     = "pod_identity"

  validation {
    condition     = contains(["pod_identity", "irsa"], var.workload_identity)
    error_message = "workload_identity must be \"pod_identity\" or \"irsa\"."
  }
}

variable "cluster_name" {
  description = "EKS cluster name the Pod Identity associations are created in. Required when workload_identity is \"pod_identity\"; unused under \"irsa\"."
  type        = string
  default     = null

  validation {
    condition     = var.cluster_name == null || can(regex("^[0-9A-Za-z][A-Za-z0-9_-]{0,99}$", var.cluster_name))
    error_message = "cluster_name must be null or a valid EKS cluster name (1-100 characters of letters, digits, hyphens and underscores, starting with a letter or digit)."
  }
}

variable "oidc_provider_arn" {
  description = "ARN of the cluster's IAM OIDC provider. Required when workload_identity is \"irsa\"; the issuer host is derived from it."
  type        = string
  default     = null

  validation {
    condition     = var.oidc_provider_arn == null || can(regex("^arn:[a-z-]+:iam::[0-9]{12}:oidc-provider/.+$", var.oidc_provider_arn))
    error_message = "oidc_provider_arn must be null or an IAM OIDC provider ARN (arn:<partition>:iam::<account>:oidc-provider/<issuer host and path>)."
  }
}

variable "namespace" {
  description = "Kubernetes namespace the chart installs into. Must match the chart (pass helm_values.identity through)."
  type        = string
  default     = "stac-higher"

  validation {
    condition     = can(regex("^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$", var.namespace))
    error_message = "namespace must be a DNS-1123 label (lowercase letters, digits and hyphens, at most 63 characters, starting and ending with an alphanumeric)."
  }
}

variable "service_accounts" {
  description = "ServiceAccount name per workload. Must match the names the chart renders (pass helm_values.identity through)."
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

  validation {
    condition     = alltrue([for v in values(var.service_accounts) : can(regex("^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$", v))])
    error_message = "every service_accounts value must be a DNS-1123 label (lowercase letters, digits and hyphens, at most 63 characters, starting and ending with an alphanumeric)."
  }
}
