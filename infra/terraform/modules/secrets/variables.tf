variable "name_prefix" {
  description = "Prefix for every secret name; each secret is <name_prefix>/<group>/<name>."
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
  description = "Full ARN of the KMS key that encrypts the secrets (the kms module's key_arn)."
  type        = string

  validation {
    condition     = can(regex("^arn:[a-z-]+:kms:[a-z0-9-]+:[0-9]{12}:key/.+$", var.kms_key_arn))
    error_message = "kms_key_arn must be a full KMS key ARN (arn:<partition>:kms:<region>:<account>:key/<id>), not a key id or alias."
  }
}

variable "recovery_window_in_days" {
  description = "Days a deleted secret stays recoverable; 0 deletes immediately (throwaway environments), otherwise 7-30."
  type        = number
  default     = 7

  validation {
    condition     = var.recovery_window_in_days == 0 || (var.recovery_window_in_days >= 7 && var.recovery_window_in_days <= 30)
    error_message = "recovery_window_in_days must be 0 (immediate deletion) or between 7 and 30."
  }
}
