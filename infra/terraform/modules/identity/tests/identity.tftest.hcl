mock_provider "aws" {
  mock_data "aws_partition" {
    defaults = { partition = "aws-us-gov", dns_suffix = "dns-suffix.example" }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "000000000000" }
  }
  mock_data "aws_region" {
    defaults = { region = "us-gov-west-1" }
  }
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws-us-gov:iam::000000000000:role/mock"
    }
  }
}

variables {
  name_prefix  = "stac-test"
  kms_key_arn  = "arn:aws-us-gov:kms:us-gov-west-1:000000000000:key/00000000-0000-0000-0000-000000000000"
  bucket_arn   = "arn:aws-us-gov:s3:::stac-test-platform"
  cluster_name = "example-cluster"
}

run "role_names" {
  command = plan
  assert {
    condition = (
      aws_iam_role.workload["app"].name == "stac-test-app" &&
      aws_iam_role.workload["pipeline"].name == "stac-test-pipeline" &&
      aws_iam_role.workload["titiler"].name == "stac-test-titiler" &&
      aws_iam_role.workload["cube_server"].name == "stac-test-cube-server" &&
      aws_iam_role.process_run.name == "stac-test-process-run"
    )
    error_message = "role names are <name_prefix>-<workload> with hyphens"
  }
  assert {
    condition     = aws_iam_role.process_run.max_session_duration == 3600
    error_message = "the run role is capped at the 1 h role-chaining limit"
  }
}

run "pod_identity_trust" {
  command = plan
  assert {
    condition = alltrue([
      for k, r in aws_iam_role.workload :
      length(jsondecode(r.assume_role_policy).Statement) == 1 &&
      jsondecode(r.assume_role_policy).Statement[0].Principal.Service == "pods.eks.dns-suffix.example" &&
      toset(jsondecode(r.assume_role_policy).Statement[0].Action) == toset(["sts:AssumeRole", "sts:TagSession"]) &&
      !contains(keys(jsondecode(r.assume_role_policy).Statement[0]), "Condition")
    ])
    error_message = "Pod Identity trust: pods.eks service principal, AssumeRole + TagSession, no extra conditions"
  }
}

run "pod_identity_associations" {
  command = plan
  assert {
    condition     = length(aws_eks_pod_identity_association.this) == 4
    error_message = "one association per workload"
  }
  assert {
    condition = alltrue([
      for a in values(aws_eks_pod_identity_association.this) :
      a.cluster_name == "example-cluster" && a.namespace == "stac-higher"
    ])
    error_message = "associations name the cluster and the default namespace"
  }
  assert {
    condition = (
      aws_eks_pod_identity_association.this["app"].service_account == "stac-higher-app" &&
      aws_eks_pod_identity_association.this["pipeline"].service_account == "stac-higher-pipeline" &&
      aws_eks_pod_identity_association.this["titiler"].service_account == "stac-higher-titiler" &&
      aws_eks_pod_identity_association.this["cube_server"].service_account == "stac-higher-cube-server"
    )
    error_message = "default service-account names"
  }
}

run "app_policy" {
  command = plan
  assert {
    condition     = toset(keys({ for s in jsondecode(aws_iam_role_policy.workload["app"].policy).Statement : s.Sid => s })) == toset(["PresignGet", "PresignPut", "BucketKms"])
    error_message = "app statements"
  }
  assert {
    condition = (
      { for s in jsondecode(aws_iam_role_policy.workload["app"].policy).Statement : s.Sid => s }["PresignGet"].Action == ["s3:GetObject"] &&
      { for s in jsondecode(aws_iam_role_policy.workload["app"].policy).Statement : s.Sid => s }["PresignGet"].Resource == ["arn:aws-us-gov:s3:::stac-test-platform/*"]
    )
    error_message = "PresignGet"
  }
  assert {
    condition = (
      { for s in jsondecode(aws_iam_role_policy.workload["app"].policy).Statement : s.Sid => s }["PresignPut"].Action == ["s3:PutObject"] &&
      toset({ for s in jsondecode(aws_iam_role_policy.workload["app"].policy).Statement : s.Sid => s }["PresignPut"].Resource) == toset(["arn:aws-us-gov:s3:::stac-test-platform/staging/*", "arn:aws-us-gov:s3:::stac-test-platform/assets/*"])
    )
    error_message = "PresignPut is staging/* and assets/* only"
  }
  assert {
    condition = (
      toset({ for s in jsondecode(aws_iam_role_policy.workload["app"].policy).Statement : s.Sid => s }["BucketKms"].Action) == toset(["kms:Decrypt", "kms:GenerateDataKey"]) &&
      { for s in jsondecode(aws_iam_role_policy.workload["app"].policy).Statement : s.Sid => s }["BucketKms"].Resource == "arn:aws-us-gov:kms:us-gov-west-1:000000000000:key/00000000-0000-0000-0000-000000000000" &&
      { for s in jsondecode(aws_iam_role_policy.workload["app"].policy).Statement : s.Sid => s }["BucketKms"].Condition == { StringEquals = { "kms:ViaService" = "s3.us-gov-west-1.dns-suffix.example" } }
    )
    error_message = "BucketKms via S3"
  }
}

run "pipeline_policy" {
  command = plan
  assert {
    condition     = toset(keys({ for s in jsondecode(aws_iam_role_policy.workload["pipeline"].policy).Statement : s.Sid => s })) == toset(["BucketObjects", "BucketList", "BucketKms", "AssumeProcessRunRole"])
    error_message = "pipeline statements"
  }
  assert {
    condition = (
      toset({ for s in jsondecode(aws_iam_role_policy.workload["pipeline"].policy).Statement : s.Sid => s }["BucketObjects"].Action) == toset(["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]) &&
      { for s in jsondecode(aws_iam_role_policy.workload["pipeline"].policy).Statement : s.Sid => s }["BucketObjects"].Resource == ["arn:aws-us-gov:s3:::stac-test-platform/*"]
    )
    error_message = "BucketObjects"
  }
  assert {
    condition = (
      { for s in jsondecode(aws_iam_role_policy.workload["pipeline"].policy).Statement : s.Sid => s }["BucketList"].Action == ["s3:ListBucket"] &&
      { for s in jsondecode(aws_iam_role_policy.workload["pipeline"].policy).Statement : s.Sid => s }["BucketList"].Resource == ["arn:aws-us-gov:s3:::stac-test-platform"]
    )
    error_message = "BucketList"
  }
  assert {
    condition = (
      toset({ for s in jsondecode(aws_iam_role_policy.workload["pipeline"].policy).Statement : s.Sid => s }["BucketKms"].Action) == toset(["kms:Decrypt", "kms:GenerateDataKey"]) &&
      { for s in jsondecode(aws_iam_role_policy.workload["pipeline"].policy).Statement : s.Sid => s }["BucketKms"].Resource == "arn:aws-us-gov:kms:us-gov-west-1:000000000000:key/00000000-0000-0000-0000-000000000000" &&
      { for s in jsondecode(aws_iam_role_policy.workload["pipeline"].policy).Statement : s.Sid => s }["BucketKms"].Condition == { StringEquals = { "kms:ViaService" = "s3.us-gov-west-1.dns-suffix.example" } }
    )
    error_message = "BucketKms via S3"
  }
  assert {
    condition = (
      toset({ for s in jsondecode(aws_iam_role_policy.workload["pipeline"].policy).Statement : s.Sid => s }["AssumeProcessRunRole"].Action) == toset(["sts:AssumeRole", "sts:TagSession"]) &&
      { for s in jsondecode(aws_iam_role_policy.workload["pipeline"].policy).Statement : s.Sid => s }["AssumeProcessRunRole"].Resource == "arn:aws-us-gov:iam::000000000000:role/stac-test-process-run"
    )
    error_message = "the pipeline may assume the run role only"
  }
}

run "titiler_policy" {
  command = plan
  assert {
    condition     = toset(keys({ for s in jsondecode(aws_iam_role_policy.workload["titiler"].policy).Statement : s.Sid => s })) == toset(["AssetsRead", "AssetsList", "BucketKms"])
    error_message = "titiler statements"
  }
  assert {
    condition = (
      { for s in jsondecode(aws_iam_role_policy.workload["titiler"].policy).Statement : s.Sid => s }["AssetsRead"].Action == ["s3:GetObject"] &&
      { for s in jsondecode(aws_iam_role_policy.workload["titiler"].policy).Statement : s.Sid => s }["AssetsRead"].Resource == ["arn:aws-us-gov:s3:::stac-test-platform/assets/*"]
    )
    error_message = "AssetsRead"
  }
  assert {
    condition = (
      { for s in jsondecode(aws_iam_role_policy.workload["titiler"].policy).Statement : s.Sid => s }["AssetsList"].Action == ["s3:ListBucket"] &&
      { for s in jsondecode(aws_iam_role_policy.workload["titiler"].policy).Statement : s.Sid => s }["AssetsList"].Resource == ["arn:aws-us-gov:s3:::stac-test-platform"] &&
      { for s in jsondecode(aws_iam_role_policy.workload["titiler"].policy).Statement : s.Sid => s }["AssetsList"].Condition.StringLike["s3:prefix"] == ["assets/*"]
    )
    error_message = "AssetsList"
  }
  assert {
    condition = (
      { for s in jsondecode(aws_iam_role_policy.workload["titiler"].policy).Statement : s.Sid => s }["BucketKms"].Action == ["kms:Decrypt"] &&
      { for s in jsondecode(aws_iam_role_policy.workload["titiler"].policy).Statement : s.Sid => s }["BucketKms"].Condition == { StringEquals = { "kms:ViaService" = "s3.us-gov-west-1.dns-suffix.example" } }
    )
    error_message = "titiler decrypts only"
  }
}

run "cube_server_policy" {
  command = plan
  assert {
    condition     = toset(keys({ for s in jsondecode(aws_iam_role_policy.workload["cube_server"].policy).Statement : s.Sid => s })) == toset(["CubeRead", "CubeList", "BucketKms"])
    error_message = "cube-server statements"
  }
  assert {
    condition = (
      { for s in jsondecode(aws_iam_role_policy.workload["cube_server"].policy).Statement : s.Sid => s }["CubeRead"].Action == ["s3:GetObject"] &&
      { for s in jsondecode(aws_iam_role_policy.workload["cube_server"].policy).Statement : s.Sid => s }["CubeRead"].Resource == ["arn:aws-us-gov:s3:::stac-test-platform/assets/*/_cube/*"]
    )
    error_message = "CubeRead"
  }
  assert {
    condition = (
      { for s in jsondecode(aws_iam_role_policy.workload["cube_server"].policy).Statement : s.Sid => s }["CubeList"].Action == ["s3:ListBucket"] &&
      { for s in jsondecode(aws_iam_role_policy.workload["cube_server"].policy).Statement : s.Sid => s }["CubeList"].Resource == ["arn:aws-us-gov:s3:::stac-test-platform"] &&
      { for s in jsondecode(aws_iam_role_policy.workload["cube_server"].policy).Statement : s.Sid => s }["CubeList"].Condition.StringLike["s3:prefix"] == ["assets/*/_cube/*"]
    )
    error_message = "CubeList"
  }
  assert {
    condition = (
      { for s in jsondecode(aws_iam_role_policy.workload["cube_server"].policy).Statement : s.Sid => s }["BucketKms"].Action == ["kms:Decrypt"] &&
      { for s in jsondecode(aws_iam_role_policy.workload["cube_server"].policy).Statement : s.Sid => s }["BucketKms"].Condition == { StringEquals = { "kms:ViaService" = "s3.us-gov-west-1.dns-suffix.example" } }
    )
    error_message = "cube-server decrypts only"
  }
}

run "process_run_trust" {
  command = plan
  assert {
    condition     = length(jsondecode(aws_iam_role.process_run.assume_role_policy).Statement) == 1
    error_message = "one trust statement"
  }
  assert {
    condition = (
      jsondecode(aws_iam_role.process_run.assume_role_policy).Statement[0].Principal.AWS == "arn:aws-us-gov:iam::000000000000:role/stac-test-pipeline" &&
      toset(jsondecode(aws_iam_role.process_run.assume_role_policy).Statement[0].Action) == toset(["sts:AssumeRole", "sts:TagSession"])
    )
    error_message = "only the pipeline role may assume the run role"
  }
}

run "process_run_policy" {
  command = plan
  assert {
    condition     = toset(keys({ for s in jsondecode(aws_iam_role_policy.process_run.policy).Statement : s.Sid => s })) == toset(["RunWritablePrefixes", "SourceCollectionsRead", "RunPrefixList", "BucketKms"])
    error_message = "run-role statements"
  }
  assert {
    condition = (
      toset({ for s in jsondecode(aws_iam_role_policy.process_run.policy).Statement : s.Sid => s }["RunWritablePrefixes"].Action) == toset(["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]) &&
      toset({ for s in jsondecode(aws_iam_role_policy.process_run.policy).Statement : s.Sid => s }["RunWritablePrefixes"].Resource) == toset(["arn:aws-us-gov:s3:::stac-test-platform/staging/runs/*", "arn:aws-us-gov:s3:::stac-test-platform/scans/*"])
    )
    error_message = "RunWritablePrefixes"
  }
  assert {
    condition = (
      { for s in jsondecode(aws_iam_role_policy.process_run.policy).Statement : s.Sid => s }["SourceCollectionsRead"].Action == ["s3:GetObject"] &&
      { for s in jsondecode(aws_iam_role_policy.process_run.policy).Statement : s.Sid => s }["SourceCollectionsRead"].Resource == ["arn:aws-us-gov:s3:::stac-test-platform/assets/*"]
    )
    error_message = "SourceCollectionsRead"
  }
  assert {
    condition = (
      { for s in jsondecode(aws_iam_role_policy.process_run.policy).Statement : s.Sid => s }["RunPrefixList"].Action == ["s3:ListBucket"] &&
      { for s in jsondecode(aws_iam_role_policy.process_run.policy).Statement : s.Sid => s }["RunPrefixList"].Resource == ["arn:aws-us-gov:s3:::stac-test-platform"] &&
      toset({ for s in jsondecode(aws_iam_role_policy.process_run.policy).Statement : s.Sid => s }["RunPrefixList"].Condition.StringLike["s3:prefix"]) == toset(["staging/runs/*", "scans/*", "assets/*"])
    )
    error_message = "RunPrefixList"
  }
  assert {
    condition = (
      toset({ for s in jsondecode(aws_iam_role_policy.process_run.policy).Statement : s.Sid => s }["BucketKms"].Action) == toset(["kms:Decrypt", "kms:GenerateDataKey"]) &&
      { for s in jsondecode(aws_iam_role_policy.process_run.policy).Statement : s.Sid => s }["BucketKms"].Condition == { StringEquals = { "kms:ViaService" = "s3.us-gov-west-1.dns-suffix.example" } }
    )
    error_message = "BucketKms via S3"
  }
}

run "irsa_trust" {
  command = plan
  variables {
    workload_identity = "irsa"
    oidc_provider_arn = "arn:aws-us-gov:iam::000000000000:oidc-provider/oidc.eks.us-gov-west-1.dns-suffix.example/id/EXAMPLE"
    cluster_name      = null
  }
  assert {
    condition     = length(aws_eks_pod_identity_association.this) == 0
    error_message = "no associations under irsa"
  }
  assert {
    condition = (
      jsondecode(aws_iam_role.workload["app"].assume_role_policy).Statement[0].Principal.Federated == var.oidc_provider_arn &&
      jsondecode(aws_iam_role.workload["app"].assume_role_policy).Statement[0].Action == "sts:AssumeRoleWithWebIdentity"
    )
    error_message = "IRSA trust names the OIDC provider and web-identity action"
  }
  assert {
    condition = (
      jsondecode(aws_iam_role.workload["app"].assume_role_policy).Statement[0].Condition.StringEquals["oidc.eks.us-gov-west-1.dns-suffix.example/id/EXAMPLE:sub"] == "system:serviceaccount:stac-higher:stac-higher-app" &&
      jsondecode(aws_iam_role.workload["app"].assume_role_policy).Statement[0].Condition.StringEquals["oidc.eks.us-gov-west-1.dns-suffix.example/id/EXAMPLE:aud"] == "sts.dns-suffix.example"
    )
    error_message = "IRSA sub and aud conditions"
  }
  assert {
    condition     = jsondecode(aws_iam_role.workload["pipeline"].assume_role_policy).Statement[0].Condition.StringEquals["oidc.eks.us-gov-west-1.dns-suffix.example/id/EXAMPLE:sub"] == "system:serviceaccount:stac-higher:stac-higher-pipeline"
    error_message = "pipeline sub names its own service account"
  }
}

run "irsa_requires_oidc_provider" {
  command = plan
  variables {
    workload_identity = "irsa"
  }
  expect_failures = [aws_iam_role.workload]
}

run "pod_identity_requires_cluster_name" {
  command = plan
  variables {
    cluster_name = null
  }
  expect_failures = [aws_iam_role.workload]
}

run "rejects_unknown_mode" {
  command = plan
  variables {
    workload_identity = "kiam"
  }
  expect_failures = [var.workload_identity]
}

run "rejects_bad_service_account" {
  command = plan
  variables {
    service_accounts = {
      app         = "stac-higher-app"
      pipeline    = "stac-higher-pipeline"
      titiler     = "stac-higher-titiler"
      cube_server = "Cube_Server"
    }
  }
  expect_failures = [var.service_accounts]
}

run "rejects_kms_key_id" {
  command = plan
  variables {
    kms_key_arn = "00000000-0000-0000-0000-000000000000"
  }
  expect_failures = [var.kms_key_arn]
}

run "rejects_bad_name_prefix" {
  command = plan
  variables {
    name_prefix = "Stac_Test"
  }
  expect_failures = [var.name_prefix]
}

run "outputs_known_at_plan" {
  command = plan
  assert {
    condition = output.role_arns == {
      app         = "arn:aws-us-gov:iam::000000000000:role/stac-test-app"
      pipeline    = "arn:aws-us-gov:iam::000000000000:role/stac-test-pipeline"
      titiler     = "arn:aws-us-gov:iam::000000000000:role/stac-test-titiler"
      cube_server = "arn:aws-us-gov:iam::000000000000:role/stac-test-cube-server"
    }
    error_message = "role ARNs are built in locals so they are known at plan"
  }
  assert {
    condition     = output.process_run_role_arn == "arn:aws-us-gov:iam::000000000000:role/stac-test-process-run"
    error_message = "run role ARN"
  }
}
