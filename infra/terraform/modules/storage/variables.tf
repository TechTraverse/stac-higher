variable "name_prefix" {
  description = "Prefix for every resource name; the bucket is <name_prefix>-<name>."
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

variable "name" {
  description = "Bucket suffix; the bucket is <name_prefix>-<name>. Lets one deployment hold several buckets."
  type        = string
  default     = "platform"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{0,28}[a-z0-9]$", var.name))
    error_message = "name must be 2-30 characters of lowercase letters, digits and hyphens, not starting or ending with a hyphen."
  }
}

variable "kms_key_arn" {
  description = "Full ARN of the KMS key that encrypts the bucket (the kms module's key_arn)."
  type        = string

  validation {
    condition     = can(regex("^arn:[a-z-]+:kms:[a-z0-9-]+:[0-9]{12}:key/.+$", var.kms_key_arn))
    error_message = "kms_key_arn must be a full KMS key ARN (arn:<partition>:kms:<region>:<account>:key/<id>), not a key id or alias."
  }
}

variable "cors_allowed_origins" {
  description = "Browser origins allowed to GET, HEAD and PUT objects directly (presigned URLs). Empty means no CORS configuration."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for o in var.cors_allowed_origins : can(regex("^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$", o))])
    error_message = "Each cors_allowed_origins entry must be scheme://host[:port] only — no path, no trailing slash, no wildcard."
  }
}

variable "force_destroy" {
  description = "Allow terraform destroy to delete the bucket even when it holds objects."
  type        = bool
  default     = false
}
