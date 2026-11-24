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
  mock_resource "aws_s3_bucket" {
    defaults = {
      id  = "stac-test-demo-destination"
      arn = "arn:aws-us-gov:s3:::stac-test-demo-destination"
    }
  }
  mock_resource "aws_iam_user" {
    defaults = {
      arn = "arn:aws-us-gov:iam::000000000000:user/stac-higher/stac-test-demo-destination"
    }
  }
  mock_resource "aws_iam_access_key" {
    defaults = {
      id     = "AKIAMOCKMOCKMOCKMOCK"
      secret = "mock-secret-access-key"
    }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = {
      arn = "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:mock-AbCdEf"
    }
  }
}

variables {
  name_prefix = "stac-test"
  kms_key_arn = "arn:aws-us-gov:kms:us-gov-west-1:000000000000:key/00000000-0000-0000-0000-000000000000"
}

run "bucket_uses_storage_with_platform_key" {
  command = plan
  assert {
    condition     = module.bucket.kms_key_arn == var.kms_key_arn
    error_message = "the demo bucket must be encrypted with the platform key"
  }
}

run "user_named" {
  command = plan
  assert {
    condition     = aws_iam_user.this.name == "stac-test-demo-destination" && aws_iam_user.this.path == "/stac-higher/"
    error_message = "user is <name_prefix>-<name> under /stac-higher/"
  }
}

run "user_policy_write_only" {
  command = plan
  assert {
    condition     = toset([for s in jsondecode(aws_iam_user_policy.this.policy).Statement : s.Sid]) == toset(["DestinationWrite", "DestinationList", "DestinationKms"])
    error_message = "statements must be exactly DestinationWrite, DestinationList, DestinationKms"
  }
  assert {
    condition = (
      toset([for s in jsondecode(aws_iam_user_policy.this.policy).Statement : s.Action if s.Sid == "DestinationWrite"][0]) == toset(["s3:PutObject", "s3:AbortMultipartUpload"]) &&
      [for s in jsondecode(aws_iam_user_policy.this.policy).Statement : s.Resource if s.Sid == "DestinationWrite"][0] == ["arn:aws-us-gov:s3:::stac-test-demo-destination/*"]
    )
    error_message = "DestinationWrite is Put + AbortMultipart on bucket/*"
  }
  assert {
    condition = (
      [for s in jsondecode(aws_iam_user_policy.this.policy).Statement : s.Action if s.Sid == "DestinationList"][0] == ["s3:ListBucket"] &&
      [for s in jsondecode(aws_iam_user_policy.this.policy).Statement : s.Resource if s.Sid == "DestinationList"][0] == ["arn:aws-us-gov:s3:::stac-test-demo-destination"]
    )
    error_message = "DestinationList is ListBucket on the bucket"
  }
  assert {
    condition = (
      toset([for s in jsondecode(aws_iam_user_policy.this.policy).Statement : s.Action if s.Sid == "DestinationKms"][0]) == toset(["kms:GenerateDataKey", "kms:Decrypt"]) &&
      [for s in jsondecode(aws_iam_user_policy.this.policy).Statement : s.Resource if s.Sid == "DestinationKms"][0] == [var.kms_key_arn] &&
      [for s in jsondecode(aws_iam_user_policy.this.policy).Statement : s.Condition.StringEquals["kms:ViaService"] if s.Sid == "DestinationKms"][0] == "s3.us-gov-west-1.dns-suffix.example"
    )
    error_message = "DestinationKms is GenerateDataKey + Decrypt on the key, via S3 only"
  }
  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_user_policy.this.policy).Statement :
      alltrue([for a in tolist(s.Action) : a != "s3:GetObject" && a != "s3:DeleteObject" && !strcontains(a, "*")])
    ])
    error_message = "the user must have no Get, no Delete and no wildcard actions"
  }
}

run "access_key_for_user" {
  command = plan
  assert {
    condition     = aws_iam_access_key.this.user == "stac-test-demo-destination"
    error_message = "the access key belongs to the demo user"
  }
}

run "credentials_secret" {
  command = plan
  assert {
    condition     = aws_secretsmanager_secret.this.name == "stac-test/demo/destination_credentials"
    error_message = "secret name is <name_prefix>/demo/destination_credentials"
  }
  assert {
    condition     = aws_secretsmanager_secret.this.kms_key_id == var.kms_key_arn && aws_secretsmanager_secret.this.recovery_window_in_days == 7
    error_message = "secret uses the platform key and a 7 day recovery window by default"
  }
}

run "credentials_secret_shape" {
  command = apply
  assert {
    condition     = toset(keys(jsondecode(aws_secretsmanager_secret_version.this.secret_string))) == toset(["access_key_id", "secret_access_key"])
    error_message = "secret holds exactly access_key_id and secret_access_key"
  }
}

run "custom_name" {
  command = plan
  variables {
    name = "delivery-test"
  }
  assert {
    condition     = [for s in jsondecode(aws_iam_user_policy.this.policy).Statement : s.Resource if s.Sid == "DestinationWrite"][0] == ["arn:aws-us-gov:s3:::stac-test-delivery-test/*"]
    error_message = "the policy follows the custom bucket name"
  }
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
    name_prefix = "stac_test"
  }
  expect_failures = [var.name_prefix]
}
