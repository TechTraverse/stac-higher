variable "name_prefix" {
  description = "Prefix for every resource name; the key alias is alias/<name_prefix>."
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

variable "deletion_window_in_days" {
  description = "Days AWS waits before deleting the key after it is scheduled for deletion (7-30)."
  type        = number
  default     = 30

  validation {
    condition     = var.deletion_window_in_days >= 7 && var.deletion_window_in_days <= 30
    error_message = "deletion_window_in_days must be between 7 and 30."
  }
}
