data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  partition  = data.aws_partition.current.partition
  dns_suffix = data.aws_partition.current.dns_suffix
  region     = data.aws_region.current.region
  account_id = data.aws_caller_identity.current.account_id

  key_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountRootAdministers"
        Effect    = "Allow"
        Principal = { AWS = "arn:${local.partition}:iam::${local.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "UseThroughPlatformServices"
        Effect    = "Allow"
        Principal = { AWS = "*" }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey",
          "kms:CreateGrant",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "kms:ViaService" = [
              "s3.${local.region}.${local.dns_suffix}",
              "secretsmanager.${local.region}.${local.dns_suffix}",
              "ecr.${local.region}.${local.dns_suffix}",
              "rds.${local.region}.${local.dns_suffix}",
            ]
            "kms:CallerAccount" = local.account_id
          }
        }
      },
      {
        Sid       = "CloudWatchLogsUse"
        Effect    = "Allow"
        Principal = { Service = "logs.${local.region}.${local.dns_suffix}" }
        Action = [
          "kms:Encrypt*",
          "kms:Decrypt*",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:Describe*",
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:${local.partition}:logs:${local.region}:${local.account_id}:*"
          }
        }
      },
    ]
  })
}

resource "aws_kms_key" "this" {
  description             = "stac-higher platform key (${var.name_prefix}): S3, Secrets Manager, ECR, RDS, CloudWatch Logs"
  enable_key_rotation     = true
  deletion_window_in_days = var.deletion_window_in_days
  policy                  = local.key_policy
  tags                    = var.tags
}

resource "aws_kms_alias" "this" {
  name          = "alias/${var.name_prefix}"
  target_key_id = aws_kms_key.this.key_id
}
