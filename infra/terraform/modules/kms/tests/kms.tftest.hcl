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
}

variables {
  name_prefix = "stac-test"
}

run "key_rotation_on" {
  command = plan
  assert {
    condition     = aws_kms_key.this.enable_key_rotation == true && aws_kms_key.this.deletion_window_in_days == 30
    error_message = "rotation must be on, default deletion window 30 days"
  }
}

run "alias_from_prefix" {
  command = plan
  assert {
    condition     = aws_kms_alias.this.name == "alias/stac-test"
    error_message = "alias must be alias/<name_prefix>"
  }
  assert {
    condition     = output.alias_name == "alias/stac-test"
    error_message = "alias_name output must be alias/<name_prefix>"
  }
}

run "root_administers_partition_aware" {
  command = plan
  assert {
    condition     = jsondecode(aws_kms_key.this.policy).Statement[0].Sid == "AccountRootAdministers"
    error_message = "statement 0 must be AccountRootAdministers"
  }
  assert {
    condition     = jsondecode(aws_kms_key.this.policy).Statement[0].Effect == "Allow" && jsondecode(aws_kms_key.this.policy).Statement[0].Action == "kms:*"
    error_message = "root statement must Allow kms:*"
  }
  assert {
    condition     = jsondecode(aws_kms_key.this.policy).Statement[0].Principal.AWS == "arn:aws-us-gov:iam::000000000000:root"
    error_message = "root principal must use the mocked partition and account"
  }
}

run "platform_services_via_service" {
  command = plan
  assert {
    condition     = jsondecode(aws_kms_key.this.policy).Statement[1].Sid == "UseThroughPlatformServices"
    error_message = "statement 1 must be UseThroughPlatformServices"
  }
  assert {
    condition     = jsondecode(aws_kms_key.this.policy).Statement[1].Principal.AWS == "*"
    error_message = "platform-services principal must be * (narrowed by conditions)"
  }
  assert {
    condition = toset(jsondecode(aws_kms_key.this.policy).Statement[1].Condition.StringEquals["kms:ViaService"]) == toset([
      "s3.us-gov-west-1.dns-suffix.example",
      "secretsmanager.us-gov-west-1.dns-suffix.example",
      "ecr.us-gov-west-1.dns-suffix.example",
      "rds.us-gov-west-1.dns-suffix.example",
    ])
    error_message = "ViaService must list s3, secretsmanager, ecr, rds in the mocked region and dns suffix"
  }
  assert {
    condition     = jsondecode(aws_kms_key.this.policy).Statement[1].Condition.StringEquals["kms:CallerAccount"] == "000000000000"
    error_message = "CallerAccount must be the current account"
  }
  assert {
    condition     = toset(jsondecode(aws_kms_key.this.policy).Statement[1].Action) == toset(["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey", "kms:CreateGrant"])
    error_message = "platform-services actions mismatch"
  }
}

run "cloudwatch_logs_principal" {
  command = plan
  assert {
    condition     = jsondecode(aws_kms_key.this.policy).Statement[2].Sid == "CloudWatchLogsUse"
    error_message = "statement 2 must be CloudWatchLogsUse"
  }
  assert {
    condition     = jsondecode(aws_kms_key.this.policy).Statement[2].Principal.Service == "logs.us-gov-west-1.dns-suffix.example"
    error_message = "logs service principal must use region and dns suffix"
  }
  assert {
    condition     = jsondecode(aws_kms_key.this.policy).Statement[2].Condition.ArnLike["kms:EncryptionContext:aws:logs:arn"] == "arn:aws-us-gov:logs:us-gov-west-1:000000000000:*"
    error_message = "logs encryption context must be scoped to this account and region"
  }
}

run "tags_applied" {
  command = plan
  variables {
    tags = { project = "stac-higher" }
  }
  assert {
    condition     = aws_kms_key.this.tags == tomap({ project = "stac-higher" })
    error_message = "tags must be applied to the key"
  }
}

run "rejects_bad_name_prefix" {
  command = plan
  variables {
    name_prefix = "Stac_Test"
  }
  expect_failures = [var.name_prefix]
}

run "rejects_short_deletion_window" {
  command = plan
  variables {
    deletion_window_in_days = 6
  }
  expect_failures = [var.deletion_window_in_days]
}
