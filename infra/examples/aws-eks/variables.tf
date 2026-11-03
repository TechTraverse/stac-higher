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

variable "app_url" {
  description = "Public https origin of the app, https://host[:port] with no path or trailing slash (placeholder)."
  type        = string
  default     = "https://app.example.com"
}

variable "ghcr_credentials_secret_name" {
  description = "Name of the Secrets Manager secret holding the GHCR pull-through credentials (created by the deployment, see the registry module README)."
  type        = string
  default     = "ecr-pullthroughcache/ghcr"
}

variable "vpc_id" {
  description = "VPC that holds the database (placeholder; owned by the cluster repo)."
  type        = string
  default     = "vpc-00000000000000000"
}

variable "db_subnet_ids" {
  description = "Private subnets for the database, in at least two availability zones (placeholders)."
  type        = list(string)
  default     = ["subnet-00000000000000000", "subnet-00000000000000001"]
}

variable "db_allowed_security_group_ids" {
  description = "Security groups allowed to reach the database, typically the cluster nodes' (placeholder)."
  type        = list(string)
  default     = ["sg-00000000000000000"]
}

variable "cluster_name" {
  description = "EKS cluster the Pod Identity associations bind to (placeholder)."
  type        = string
  default     = "example-cluster"
}

variable "enable_cognito" {
  description = "Create the Cognito user pool and emit helm_values.auth. Turn off to bring your own IdP."
  type        = bool
  default     = true
}

variable "enable_demo_destination" {
  description = "Create the demo destination bucket and its write-only IAM user (test environments)."
  type        = bool
  default     = true
}
