variable "name_prefix" {
  description = "Prefix for every resource name; the instance is <name_prefix>-db."
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
  description = "Full ARN of the KMS key that encrypts storage, the managed master secret and Performance Insights (the kms module's key_arn)."
  type        = string

  validation {
    condition     = can(regex("^arn:[a-z-]+:kms:[a-z0-9-]+:[0-9]{12}:key/.+$", var.kms_key_arn))
    error_message = "kms_key_arn must be a full KMS key ARN (arn:<partition>:kms:<region>:<account>:key/<id>), not a key id or alias."
  }
}

variable "vpc_id" {
  description = "VPC that holds the database security group."
  type        = string

  validation {
    condition     = can(regex("^vpc-[0-9a-f]{8,17}$", var.vpc_id))
    error_message = "vpc_id must look like vpc-0123456789abcdef0."
  }
}

variable "subnet_ids" {
  description = "Private subnets for the DB subnet group; at least two, in different availability zones."
  type        = list(string)

  validation {
    condition     = length(var.subnet_ids) >= 2 && alltrue([for s in var.subnet_ids : can(regex("^subnet-[0-9a-f]{8,17}$", s))])
    error_message = "subnet_ids needs at least two entries, each like subnet-0123456789abcdef0."
  }
}

variable "allowed_security_group_ids" {
  description = "Security groups allowed to reach PostgreSQL on 5432 (on EKS Auto Mode, the cluster security group). CIDR ingress is not offered."
  type        = list(string)

  validation {
    condition     = length(var.allowed_security_group_ids) >= 1 && alltrue([for s in var.allowed_security_group_ids : can(regex("^sg-[0-9a-f]{8,17}$", s))])
    error_message = "allowed_security_group_ids needs at least one entry, each like sg-0123456789abcdef0."
  }
}

variable "instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.medium"

  validation {
    condition     = can(regex("^db\\.", var.instance_class))
    error_message = "instance_class must be an RDS instance class such as db.t4g.medium."
  }
}

variable "engine_version" {
  description = "PostgreSQL engine version; major 16 or later. Pin the major only (the default, 17): with auto_minor_version_upgrade on, a major.minor pin drifts as RDS patches and the next plan tries to downgrade. major.minor is accepted but not recommended. The parameter-group family follows the major."
  type        = string
  default     = "17"

  validation {
    condition     = can(regex("^[0-9]{2}(\\.[0-9]+)?$", var.engine_version)) && tonumber(split(".", var.engine_version)[0]) >= 16
    error_message = "engine_version must be a PostgreSQL major or major.minor such as 17 or 16.4, with a major of 16 or later (the role-ordering design needs PostgreSQL 16+)."
  }
}

variable "allow_major_version_upgrade" {
  description = "Permit RDS to apply a major engine_version bump. A major bump also needs the matching parameter-group family, which follows engine_version automatically."
  type        = bool
  default     = false
}

variable "db_name" {
  description = "Initial database name. The pgstac image and every chart DSN use postgis."
  type        = string
  default     = "postgis"

  validation {
    condition     = can(regex("^[a-z][a-z0-9_]{0,62}$", var.db_name))
    error_message = "db_name must start with a lowercase letter and hold lowercase letters, digits and underscores (at most 63 characters)."
  }
}

variable "master_username" {
  description = "Master user name; its password is RDS-managed and lives in a Secrets Manager secret."
  type        = string
  default     = "stac_higher_master"

  validation {
    condition     = can(regex("^[a-z][a-z0-9_]{0,62}$", var.master_username))
    error_message = "master_username must start with a lowercase letter and hold lowercase letters, digits and underscores (at most 63 characters)."
  }
}

variable "allocated_storage" {
  description = "Initial storage in GiB."
  type        = number
  default     = 50

  validation {
    condition     = var.allocated_storage >= 20
    error_message = "allocated_storage must be at least 20 GiB."
  }
}

variable "max_allocated_storage" {
  description = "Storage autoscaling ceiling in GiB; 0 disables autoscaling."
  type        = number
  default     = 200

  validation {
    condition     = var.max_allocated_storage == 0 || var.max_allocated_storage >= var.allocated_storage
    error_message = "max_allocated_storage must be 0 (autoscaling off) or at least allocated_storage."
  }
}

variable "multi_az" {
  description = "Run a synchronous standby in a second availability zone."
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Refuse deletion of the instance until turned off. Turn off for throwaway environments."
  type        = bool
  default     = true
}

variable "backup_retention_period" {
  description = "Days automated backups are kept."
  type        = number
  default     = 7

  validation {
    condition     = var.backup_retention_period >= 1 && var.backup_retention_period <= 35
    error_message = "backup_retention_period must be between 1 and 35 days."
  }
}

variable "backup_window" {
  description = "Daily backup window in UTC (hh:mm-hh:mm); null lets RDS choose."
  type        = string
  default     = null
}

variable "maintenance_window" {
  description = "Weekly maintenance window in UTC (ddd:hh:mm-ddd:hh:mm); null lets RDS choose."
  type        = string
  default     = null
}

variable "skip_final_snapshot" {
  description = "Skip the final snapshot on destroy. Turn on for throwaway environments."
  type        = bool
  default     = false
}

variable "parameter_overrides" {
  description = "Parameter-group entries merged over the pgstac defaults (all pending-reboot). rds.force_ssl cannot be overridden."
  type        = map(string)
  default     = {}

  validation {
    condition     = !contains(keys(var.parameter_overrides), "rds.force_ssl")
    error_message = "parameter_overrides must not contain rds.force_ssl: TLS to the database is always enforced."
  }
}

variable "ca_cert_identifier" {
  description = "RDS CA certificate identifier. The deployment must supply the matching region CA bundle as database.caBundle."
  type        = string
  default     = "rds-ca-rsa2048-g1"
}

variable "performance_insights_enabled" {
  description = "Enable Performance Insights, encrypted with kms_key_arn."
  type        = bool
  default     = true
}

variable "apply_immediately" {
  description = "Apply instance modifications now instead of in the next maintenance window."
  type        = bool
  default     = false
}
