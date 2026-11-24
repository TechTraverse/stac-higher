variable "name_prefix" {
  description = "Prefix for every resource name; the pool is <name_prefix>-users, the client <name_prefix>-app, and the hosted domain defaults to <name_prefix>."
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

variable "callback_urls" {
  description = "OAuth redirect URIs registered on the app client (the app's OIDC_REDIRECT_URI). At least one; https, or http only for localhost."
  type        = list(string)

  validation {
    condition     = length(var.callback_urls) >= 1
    error_message = "callback_urls needs at least one URL."
  }

  validation {
    condition = alltrue([
      for u in var.callback_urls :
      can(regex("^https://[^\\s/]+(/[^\\s]*)?$", u)) || can(regex("^http://localhost(:[0-9]{1,5})?(/[^\\s]*)?$", u))
    ])
    error_message = "Each callback URL must be https://<host>[/path], or http://localhost[:port][/path] for local development."
  }
}

variable "logout_urls" {
  description = "Sign-out redirect URIs registered on the app client (the app's post_logout_redirect_uri). https, or http only for localhost."
  type        = list(string)
  default     = []

  validation {
    condition = alltrue([
      for u in var.logout_urls :
      can(regex("^https://[^\\s/]+(/[^\\s]*)?$", u)) || can(regex("^http://localhost(:[0-9]{1,5})?(/[^\\s]*)?$", u))
    ])
    error_message = "Each logout URL must be https://<host>[/path], or http://localhost[:port][/path] for local development."
  }
}

variable "domain_prefix" {
  description = "Prefix of the Cognito hosted domain (<prefix>.auth.<region>.amazoncognito.com); must be unique in the region and must not contain aws, amazon or cognito. null uses name_prefix."
  type        = string
  default     = null

  validation {
    condition = var.domain_prefix == null || (
      can(regex("^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", var.domain_prefix)) &&
      !can(regex("aws|amazon|cognito", var.domain_prefix))
    )
    error_message = "domain_prefix must be null or 1-63 lowercase letters, digits and hyphens (not starting or ending with a hyphen) that does not contain aws, amazon or cognito."
  }
}

variable "role_groups" {
  description = "Cognito group name to canonical app role (member, operator or admin). The groups are created, and the map becomes claims_mapping.roleMap."
  type        = map(string)
  default = {
    "stac-admins"    = "admin"
    "stac-operators" = "operator"
    "stac-members"   = "member"
  }

  validation {
    condition     = alltrue([for g, r in var.role_groups : contains(["member", "operator", "admin"], r)])
    error_message = "role_groups values must be one of member, operator or admin."
  }
}

variable "data_groups" {
  description = "Extra Cognito groups that carry data-group membership (the app's groups claim); created without a role mapping."
  type        = list(string)
  default     = []
}

variable "deletion_protection" {
  description = "Protect the user pool from deletion (a destroy fails until this is false)."
  type        = bool
  default     = true
}

variable "access_token_validity_minutes" {
  description = "Lifetime of access and ID tokens in minutes (5-1440)."
  type        = number
  default     = 60

  validation {
    condition     = var.access_token_validity_minutes >= 5 && var.access_token_validity_minutes <= 1440
    error_message = "access_token_validity_minutes must be between 5 and 1440."
  }
}

variable "refresh_token_validity_days" {
  description = "Lifetime of refresh tokens in days (1-3650)."
  type        = number
  default     = 1

  validation {
    condition     = var.refresh_token_validity_days >= 1 && var.refresh_token_validity_days <= 3650
    error_message = "refresh_token_validity_days must be between 1 and 3650."
  }
}
