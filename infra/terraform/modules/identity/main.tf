data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  partition  = data.aws_partition.current.partition
  dns_suffix = data.aws_partition.current.dns_suffix
  region     = data.aws_region.current.region
  account_id = data.aws_caller_identity.current.account_id

  workload_keys = ["app", "pipeline", "titiler", "cube_server"]

  role_name = { for k in local.workload_keys : k => "${var.name_prefix}-${replace(k, "_", "-")}" }
  role_arn  = { for k, n in local.role_name : k => "arn:${local.partition}:iam::${local.account_id}:role/${n}" }

  process_run_role_name = "${var.name_prefix}-process-run"
  process_run_role_arn  = "arn:${local.partition}:iam::${local.account_id}:role/${local.process_run_role_name}"

  # The issuer host is derived from the provider ARN so the two cannot disagree.
  oidc_host = var.oidc_provider_arn == null ? "" : split("oidc-provider/", var.oidc_provider_arn)[1]

  kms_via_s3 = "s3.${local.region}.${local.dns_suffix}"

  kms_condition = { StringEquals = { "kms:ViaService" = local.kms_via_s3 } }

  # Trust per workload: Pod Identity has no extra conditions (scoping is the
  # association); IRSA pins the service account and audience.
  pod_identity_trust = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "pods.eks.${local.dns_suffix}" }
        Action    = ["sts:AssumeRole", "sts:TagSession"]
      },
    ]
  })

  irsa_trust = {
    for k in local.workload_keys : k => jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Effect    = "Allow"
          Principal = { Federated = var.oidc_provider_arn }
          Action    = "sts:AssumeRoleWithWebIdentity"
          Condition = {
            StringEquals = {
              "${local.oidc_host}:sub" = "system:serviceaccount:${var.namespace}:${var.service_accounts[k]}"
              "${local.oidc_host}:aud" = "sts.${local.dns_suffix}"
            }
          }
        },
      ]
    })
  }

  trust = { for k in local.workload_keys : k => var.workload_identity == "irsa" ? local.irsa_trust[k] : local.pod_identity_trust }

  app_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "PresignGet"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${var.bucket_arn}/*"]
      },
      {
        Sid      = "PresignPut"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = ["${var.bucket_arn}/staging/*", "${var.bucket_arn}/assets/*"]
      },
      {
        Sid       = "BucketKms"
        Effect    = "Allow"
        Action    = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource  = var.kms_key_arn
        Condition = local.kms_condition
      },
    ]
  })

  pipeline_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "BucketObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
        Resource = ["${var.bucket_arn}/*"]
      },
      {
        Sid      = "BucketList"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [var.bucket_arn]
      },
      {
        Sid       = "BucketKms"
        Effect    = "Allow"
        Action    = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource  = var.kms_key_arn
        Condition = local.kms_condition
      },
      {
        Sid      = "AssumeProcessRunRole"
        Effect   = "Allow"
        Action   = ["sts:AssumeRole", "sts:TagSession"]
        Resource = local.process_run_role_arn
      },
    ]
  })

  titiler_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AssetsRead"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${var.bucket_arn}/assets/*"]
      },
      {
        Sid       = "AssetsList"
        Effect    = "Allow"
        Action    = ["s3:ListBucket"]
        Resource  = [var.bucket_arn]
        Condition = { StringLike = { "s3:prefix" = ["assets/*"] } }
      },
      {
        Sid       = "BucketKms"
        Effect    = "Allow"
        Action    = ["kms:Decrypt"]
        Resource  = var.kms_key_arn
        Condition = local.kms_condition
      },
    ]
  })

  cube_server_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "CubeRead"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${var.bucket_arn}/assets/*/_cube/*"]
      },
      {
        Sid       = "CubeList"
        Effect    = "Allow"
        Action    = ["s3:ListBucket"]
        Resource  = [var.bucket_arn]
        Condition = { StringLike = { "s3:prefix" = ["assets/*/_cube/*"] } }
      },
      {
        Sid       = "BucketKms"
        Effect    = "Allow"
        Action    = ["kms:Decrypt"]
        Resource  = var.kms_key_arn
        Condition = local.kms_condition
      },
    ]
  })

  workloads = {
    app         = { sa = var.service_accounts.app, policy = local.app_policy }
    pipeline    = { sa = var.service_accounts.pipeline, policy = local.pipeline_policy }
    titiler     = { sa = var.service_accounts.titiler, policy = local.titiler_policy }
    cube_server = { sa = var.service_accounts.cube_server, policy = local.cube_server_policy }
  }

  process_run_trust = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { AWS = local.role_arn["pipeline"] }
        Action    = ["sts:AssumeRole", "sts:TagSession"]
      },
    ]
  })

  # The ceiling a run's session policy narrows. Mirrors session_policy() in
  # services/pipeline/src/pipeline/process/credentials.py and the prefixes in
  # services/pipeline/src/pipeline/storage/keys.py (run_staging_prefix ->
  # staging/runs/, image_scan_prefix -> scans/, canonical assets/); change
  # both together.
  process_run_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "RunWritablePrefixes"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]
        Resource = ["${var.bucket_arn}/staging/runs/*", "${var.bucket_arn}/scans/*"]
      },
      {
        Sid      = "SourceCollectionsRead"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${var.bucket_arn}/assets/*"]
      },
      {
        Sid       = "RunPrefixList"
        Effect    = "Allow"
        Action    = ["s3:ListBucket"]
        Resource  = [var.bucket_arn]
        Condition = { StringLike = { "s3:prefix" = ["staging/runs/*", "scans/*", "assets/*"] } }
      },
      {
        Sid       = "BucketKms"
        Effect    = "Allow"
        Action    = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource  = var.kms_key_arn
        Condition = local.kms_condition
      },
    ]
  })
}

resource "aws_iam_role" "workload" {
  for_each = local.workloads

  name               = local.role_name[each.key]
  description        = "stac-higher ${each.key} (${var.workload_identity})"
  assume_role_policy = local.trust[each.key]
  tags               = var.tags

  lifecycle {
    precondition {
      condition     = var.workload_identity != "irsa" || var.oidc_provider_arn != null
      error_message = "workload_identity = \"irsa\" requires oidc_provider_arn (the cluster's IAM OIDC provider)."
    }
    precondition {
      condition     = var.workload_identity != "pod_identity" || var.cluster_name != null
      error_message = "workload_identity = \"pod_identity\" requires cluster_name."
    }
  }
}

resource "aws_iam_role_policy" "workload" {
  for_each = local.workloads

  name   = "s3"
  role   = aws_iam_role.workload[each.key].id
  policy = each.value.policy
}

resource "aws_iam_role" "process_run" {
  name                 = local.process_run_role_name
  description          = "stac-higher process runs: the ceiling the per-run session policy narrows"
  assume_role_policy   = local.process_run_trust
  max_session_duration = 3600
  tags                 = var.tags

  # IAM refuses a trust principal that does not exist yet, and the trust
  # policy names the pipeline role.
  depends_on = [aws_iam_role.workload]
}

resource "aws_iam_role_policy" "process_run" {
  name   = "s3"
  role   = aws_iam_role.process_run.id
  policy = local.process_run_policy
}

resource "aws_eks_pod_identity_association" "this" {
  for_each = var.workload_identity == "pod_identity" ? local.workloads : {}

  cluster_name    = var.cluster_name
  namespace       = var.namespace
  service_account = each.value.sa
  role_arn        = aws_iam_role.workload[each.key].arn
  tags            = var.tags
}
