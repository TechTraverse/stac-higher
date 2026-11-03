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
  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws-us-gov:kms:us-gov-west-1:000000000000:key/00000000-0000-0000-0000-000000000000" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws-us-gov:iam::000000000000:role/mock" }
  }
  # The bucket id is computed, so the mock would generate a random one. The
  # default stands in for the id AWS returns (the bucket name).
  mock_resource "aws_s3_bucket" {
    defaults = { id = "stac-higher-example-platform" }
  }
  mock_data "aws_secretsmanager_secret" {
    defaults = { arn = "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:ecr-pullthroughcache/ghcr-AbCdEf" }
  }
}

# A test can read module outputs, not resources inside a module, so the CORS
# wiring is covered by the storage module's own tests.
run "wiring" {
  command = apply

  assert {
    condition     = module.storage.kms_key_arn == module.kms.key_arn
    error_message = "storage must be encrypted with the kms module's key"
  }
  assert {
    condition     = output.bucket_name == "stac-higher-example-platform"
    error_message = "bucket name is <name_prefix>-platform"
  }
  assert {
    condition     = length(output.secret_arns) == 10
    error_message = "the secrets module creates ten secrets"
  }
  assert {
    condition     = keys(output.mirror_prefixes) == ["ghcr.io"]
    error_message = "only the GHCR mirror is enabled by default"
  }
  assert {
    condition     = module.secrets.secret_names["session_secret"] == "stac-higher-example/platform/session_secret"
    error_message = "platform secrets live under <name_prefix>/platform/"
  }
}
