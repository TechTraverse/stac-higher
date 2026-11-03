data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  partition  = data.aws_partition.current.partition
  dns_suffix = data.aws_partition.current.dns_suffix
  region     = data.aws_region.current.region
  account_id = data.aws_caller_identity.current.account_id

  registry_host = "${local.account_id}.dkr.ecr.${local.region}.${local.dns_suffix}"

  all_upstreams = {
    ghcr = {
      enabled               = true
      upstream_registry_url = "ghcr.io"
      reference_host        = "ghcr.io"
      prefix                = var.ecr_prefixes.ghcr
      credential_arn        = var.ghcr_credentials_secret_arn
    }
    dockerhub = {
      enabled               = var.enable_dockerhub
      upstream_registry_url = "registry-1.docker.io"
      reference_host        = "docker.io"
      prefix                = var.ecr_prefixes.dockerhub
      credential_arn        = var.dockerhub_credentials_secret_arn
    }
    quay = {
      enabled               = var.enable_quay
      upstream_registry_url = "quay.io"
      reference_host        = "quay.io"
      prefix                = var.ecr_prefixes.quay
      credential_arn        = var.quay_credentials_secret_arn
    }
  }

  upstreams = { for k, v in local.all_upstreams : k => v if v.enabled }

  mirror_prefixes = { for u in values(local.upstreams) : u.reference_host => "${local.registry_host}/${u.prefix}" }

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "ecr.${local.dns_suffix}" }
        Action    = "sts:AssumeRole"
      },
    ]
  })

  repository_creation_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CreateCachedRepositories"
        Effect = "Allow"
        Action = [
          "ecr:CreateRepository",
          "ecr:ReplicateImage",
          "ecr:TagResource",
        ]
        Resource = [
          for u in values(local.upstreams) :
          "arn:${local.partition}:ecr:${local.region}:${local.account_id}:repository/${u.prefix}/*"
        ]
      },
      {
        Sid    = "UseCmkForCachedRepositories"
        Effect = "Allow"
        Action = [
          "kms:CreateGrant",
          "kms:RetireGrant",
          "kms:DescribeKey",
        ]
        Resource = var.kms_key_arn
      },
    ]
  })
}

resource "aws_ecr_pull_through_cache_rule" "this" {
  for_each = local.upstreams

  ecr_repository_prefix = each.value.prefix
  upstream_registry_url = each.value.upstream_registry_url
  credential_arn        = each.value.credential_arn

  lifecycle {
    precondition {
      condition     = each.key != "dockerhub" || each.value.credential_arn != null
      error_message = "enable_dockerhub requires dockerhub_credentials_secret_arn: ECR's Docker Hub upstream always authenticates."
    }
  }
}

resource "aws_iam_role" "repository_creation" {
  name               = "${var.name_prefix}-ecr-repo-creation"
  description        = "Lets ECR create and tag pull-through cache repositories encrypted with the platform key."
  assume_role_policy = local.assume_role_policy
  tags               = var.tags
}

resource "aws_iam_role_policy" "repository_creation" {
  name   = "repository-creation"
  role   = aws_iam_role.repository_creation.id
  policy = local.repository_creation_policy
}

resource "aws_ecr_repository_creation_template" "this" {
  for_each = local.upstreams

  prefix               = each.value.prefix
  description          = "Pull-through cache repositories for ${each.value.reference_host}"
  applied_for          = ["PULL_THROUGH_CACHE"]
  image_tag_mutability = var.image_tag_mutability
  custom_role_arn      = aws_iam_role.repository_creation.arn
  resource_tags        = var.tags

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = var.kms_key_arn
  }

  # The template's custom role must carry its policy before ECR assumes it on the first pull.
  depends_on = [aws_iam_role_policy.repository_creation]
}
