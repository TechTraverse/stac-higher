variable "region" {
  description = "AWS region the example targets (placeholder; a deployment sets its own)."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for every named resource (bucket, KMS alias, secrets, IAM role)."
  type        = string
  default     = "stac-higher-example"
}

variable "app_origin" {
  description = "Origin the app is served from; the only origin allowed by the bucket CORS rule."
  type        = string
  default     = "https://app.example.com"
}

variable "ghcr_credentials_secret_name" {
  description = "Name of the Secrets Manager secret holding the GHCR pull-through credentials (created by the deployment, see the registry module README)."
  type        = string
  default     = "ecr-pullthroughcache/ghcr"
}
