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
    defaults = { arn = "arn:aws-us-gov:iam::000000000000:role/stac-test-ecr-repo-creation" }
  }
}

variables {
  name_prefix                 = "stac-test"
  kms_key_arn                 = "arn:aws-us-gov:kms:us-gov-west-1:000000000000:key/00000000-0000-0000-0000-000000000000"
  ghcr_credentials_secret_arn = "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:ecr-pullthroughcache/ghcr-AbCdEf"
}

run "ghcr_only_by_default" {
  command = plan
  assert {
    condition     = keys(aws_ecr_pull_through_cache_rule.this) == ["ghcr"]
    error_message = "only ghcr is enabled by default"
  }
  assert {
    condition = (
      aws_ecr_pull_through_cache_rule.this["ghcr"].upstream_registry_url == "ghcr.io"
      && aws_ecr_pull_through_cache_rule.this["ghcr"].ecr_repository_prefix == "ghcr"
      && aws_ecr_pull_through_cache_rule.this["ghcr"].credential_arn == var.ghcr_credentials_secret_arn
    )
    error_message = "ghcr rule must point at ghcr.io with prefix ghcr and the supplied credential"
  }
}

run "template_per_rule_with_cmk" {
  command = plan
  assert {
    condition     = keys(aws_ecr_repository_creation_template.this) == ["ghcr"]
    error_message = "one creation template per enabled upstream"
  }
  assert {
    condition = (
      aws_ecr_repository_creation_template.this["ghcr"].prefix == "ghcr"
      && toset(aws_ecr_repository_creation_template.this["ghcr"].applied_for) == toset(["PULL_THROUGH_CACHE"])
      && aws_ecr_repository_creation_template.this["ghcr"].image_tag_mutability == "MUTABLE"
    )
    error_message = "template must apply to pull-through cache only, mutable tags, prefix ghcr"
  }
  assert {
    condition = (
      one(aws_ecr_repository_creation_template.this["ghcr"].encryption_configuration).encryption_type == "KMS"
      && one(aws_ecr_repository_creation_template.this["ghcr"].encryption_configuration).kms_key == var.kms_key_arn
    )
    error_message = "template must encrypt with the platform CMK"
  }
}

run "mirror_prefix_from_partition" {
  command = plan
  assert {
    condition     = output.ghcr_mirror_prefix == "000000000000.dkr.ecr.us-gov-west-1.dns-suffix.example/ghcr"
    error_message = "ghcr_mirror_prefix must be built from account, region and dns suffix"
  }
  assert {
    condition     = output.mirror_prefixes == { "ghcr.io" = "000000000000.dkr.ecr.us-gov-west-1.dns-suffix.example/ghcr" }
    error_message = "mirror_prefixes must hold only ghcr.io by default"
  }
}

run "creation_role_trust" {
  command = plan
  assert {
    condition = (
      jsondecode(aws_iam_role.repository_creation.assume_role_policy).Statement[0].Principal.Service == "ecr.dns-suffix.example"
      && jsondecode(aws_iam_role.repository_creation.assume_role_policy).Statement[0].Action == "sts:AssumeRole"
    )
    error_message = "role must be assumable by the partition's ECR service principal"
  }
}

run "creation_role_scope" {
  command = plan
  assert {
    condition     = toset(jsondecode(aws_iam_role_policy.repository_creation.policy).Statement[0].Action) == toset(["ecr:CreateRepository", "ecr:ReplicateImage", "ecr:TagResource"])
    error_message = "ECR statement must grant only create, replicate and tag"
  }
  assert {
    condition     = toset(jsondecode(aws_iam_role_policy.repository_creation.policy).Statement[0].Resource) == toset(["arn:aws-us-gov:ecr:us-gov-west-1:000000000000:repository/ghcr/*"])
    error_message = "ECR statement must be scoped to the enabled prefixes"
  }
  assert {
    condition     = toset(jsondecode(aws_iam_role_policy.repository_creation.policy).Statement[1].Action) == toset(["kms:CreateGrant", "kms:RetireGrant", "kms:DescribeKey"])
    error_message = "KMS statement must grant only grant and describe actions"
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.repository_creation.policy).Statement[1].Resource == var.kms_key_arn
    error_message = "KMS statement must be scoped to the platform key"
  }
}

run "all_upstreams" {
  command = plan
  variables {
    enable_dockerhub                 = true
    dockerhub_credentials_secret_arn = "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:ecr-pullthroughcache/dockerhub-AbCdEf"
    enable_quay                      = true
  }
  assert {
    condition     = length(aws_ecr_pull_through_cache_rule.this) == 3
    error_message = "three rules expected"
  }
  assert {
    condition     = aws_ecr_pull_through_cache_rule.this["dockerhub"].upstream_registry_url == "registry-1.docker.io"
    error_message = "Docker Hub upstream is registry-1.docker.io"
  }
  assert {
    condition     = aws_ecr_pull_through_cache_rule.this["quay"].credential_arn == null
    error_message = "quay needs no credential"
  }
  assert {
    condition     = toset(keys(output.mirror_prefixes)) == toset(["ghcr.io", "docker.io", "quay.io"])
    error_message = "mirror_prefixes keys are the reference hosts"
  }
  assert {
    condition     = endswith(output.mirror_prefixes["docker.io"], "/docker-hub")
    error_message = "docker.io mirrors under docker-hub"
  }
  assert {
    condition     = length(jsondecode(aws_iam_role_policy.repository_creation.policy).Statement[0].Resource) == 3
    error_message = "ECR statement must cover three prefixes"
  }
}

run "custom_prefixes" {
  command = plan
  variables {
    ecr_prefixes = { ghcr = "sh-ghcr", dockerhub = "docker-hub", quay = "quay" }
  }
  assert {
    condition     = endswith(output.ghcr_mirror_prefix, "/sh-ghcr")
    error_message = "ghcr prefix must be configurable"
  }
}

run "rejects_ghcr_secret_outside_prefix" {
  command = plan
  variables {
    ghcr_credentials_secret_arn = "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:ghcr-creds-AbCdEf"
  }
  expect_failures = [var.ghcr_credentials_secret_arn]
}

run "dockerhub_needs_credentials" {
  command = plan
  variables {
    enable_dockerhub = true
  }
  expect_failures = [aws_ecr_pull_through_cache_rule.this]
}

run "rejects_kms_key_id" {
  command = plan
  variables {
    kms_key_arn = "00000000-0000-0000-0000-000000000000"
  }
  expect_failures = [var.kms_key_arn]
}
