variable "name_prefix" {
  description = "Prefix for resource names; the repository-creation role is <name_prefix>-ecr-repo-creation."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,30}[a-z0-9]$", var.name_prefix))
    error_message = "name_prefix must be 2-32 characters of lowercase letters, digits and hyphens, starting with a letter and not ending in a hyphen (it becomes part of bucket, IAM role, KMS alias and secret names)."
  }
}

variable "tags" {
  description = "Tags applied to every taggable resource (ECR creation templates use them as resource_tags)."
  type        = map(string)
  default     = {}
}

variable "kms_key_arn" {
  description = "Full ARN of the KMS key that encrypts cached repositories (the kms module's key_arn)."
  type        = string

  validation {
    condition     = can(regex("^arn:[a-z-]+:kms:[a-z0-9-]+:[0-9]{12}:key/.+$", var.kms_key_arn))
    error_message = "kms_key_arn must be a full KMS key ARN (arn:<partition>:kms:<region>:<account>:key/<id>), not a key id or alias."
  }
}

variable "ghcr_credentials_secret_arn" {
  description = "ARN of the Secrets Manager secret holding the GHCR credentials (JSON username and accessToken), created by the deployment."
  type        = string

  validation {
    condition     = can(regex("^arn:[a-z-]+:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:ecr-pullthroughcache/.+$", var.ghcr_credentials_secret_arn))
    error_message = "The secret name must start with ecr-pullthroughcache/ (ECR only accepts such secrets), use the default aws/secretsmanager key (not a CMK), and hold JSON with the keys username and accessToken."
  }
}

variable "enable_dockerhub" {
  description = "Create a pull-through cache rule for Docker Hub (registry-1.docker.io). Requires dockerhub_credentials_secret_arn."
  type        = bool
  default     = false
}

variable "dockerhub_credentials_secret_arn" {
  description = "ARN of the Secrets Manager secret holding the Docker Hub credentials (JSON username and accessToken). Required when enable_dockerhub is true."
  type        = string
  default     = null

  validation {
    condition     = var.dockerhub_credentials_secret_arn == null || can(regex("^arn:[a-z-]+:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:ecr-pullthroughcache/.+$", var.dockerhub_credentials_secret_arn))
    error_message = "The secret name must start with ecr-pullthroughcache/ (ECR only accepts such secrets), use the default aws/secretsmanager key (not a CMK), and hold JSON with the keys username and accessToken."
  }
}

variable "enable_quay" {
  description = "Create a pull-through cache rule for Quay (quay.io)."
  type        = bool
  default     = false
}

variable "quay_credentials_secret_arn" {
  description = "ARN of the Secrets Manager secret holding the Quay credentials (JSON username and accessToken). Optional: Quay needs none for public images."
  type        = string
  default     = null

  validation {
    condition     = var.quay_credentials_secret_arn == null || can(regex("^arn:[a-z-]+:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:ecr-pullthroughcache/.+$", var.quay_credentials_secret_arn))
    error_message = "The secret name must start with ecr-pullthroughcache/ (ECR only accepts such secrets), use the default aws/secretsmanager key (not a CMK), and hold JSON with the keys username and accessToken."
  }
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

  validation {
    condition = alltrue([
      for p in values(var.ecr_prefixes) :
      can(regex("^[a-z0-9]+(?:[._-][a-z0-9]+)*$", p)) && length(p) >= 2 && length(p) <= 30
    ])
    error_message = "Each ecr_prefixes value must be 2-30 characters: lowercase letters and digits, optionally separated by single '.', '_' or '-'."
  }
}

variable "image_tag_mutability" {
  description = "Tag mutability of cached repositories. MUTABLE is ECR's recommendation for pull-through so cached tags can refresh."
  type        = string
  default     = "MUTABLE"

  validation {
    condition     = contains(["MUTABLE", "IMMUTABLE"], var.image_tag_mutability)
    error_message = "image_tag_mutability must be MUTABLE or IMMUTABLE."
  }
}
