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
  # The bucket id and ARN are computed, so the mock would generate random
  # ones. The defaults stand in for what AWS returns.
  mock_resource "aws_s3_bucket" {
    defaults = {
      id  = "stac-higher-example-platform"
      arn = "arn:aws-us-gov:s3:::stac-higher-example-platform"
    }
  }
  mock_resource "aws_db_instance" {
    defaults = {
      address = "stac-example-db.example.com"
      port    = 5432
      master_user_secret = [{
        secret_arn    = "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:rds!db-00000000-AbCdEf"
        kms_key_id    = "arn:aws-us-gov:kms:us-gov-west-1:000000000000:key/00000000-0000-0000-0000-000000000000"
        secret_status = "active"
      }]
    }
  }
  mock_resource "aws_cognito_user_pool" {
    defaults = { id = "us-gov-west-1_EXAMPLE" }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = { arn = "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:stac-higher-example/mock-AbCdEf" }
  }
  mock_resource "aws_iam_user" {
    defaults = { arn = "arn:aws-us-gov:iam::000000000000:user/stac-higher/stac-higher-example-demo-destination" }
  }
  mock_resource "aws_iam_access_key" {
    defaults = {
      id     = "AKIAMOCKMOCKMOCKMOCK"
      secret = "mock-secret-access-key"
    }
  }
  mock_data "aws_ec2_managed_prefix_list" {
    defaults = {
      entries = [{ cidr = "192.0.2.0/24", description = "placeholder (RFC 5737)" }]
    }
  }
  mock_data "aws_secretsmanager_secret" {
    defaults = { arn = "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:ecr-pullthroughcache/ghcr-AbCdEf" }
  }
}

run "wiring" {
  command = apply

  assert {
    condition     = length(output.secret_arns) == 10
    error_message = "the secrets module creates ten secrets"
  }
  assert {
    condition     = keys(output.mirror_prefixes) == ["ghcr.io"]
    error_message = "only the GHCR mirror is enabled by default"
  }
  assert {
    condition     = module.stac_higher.secret_names["session_secret"] == "stac-higher-example/platform/session_secret"
    error_message = "platform secrets live under <name_prefix>/platform/"
  }
}

run "helm_values" {
  command = apply

  assert {
    condition     = keys(output.helm_values) == ["auth", "aws", "database", "demoDestination", "global", "identity", "publicUrls", "registry", "secrets"]
    error_message = "the example enables cognito and the demo destination, so all nine keys appear"
  }
  assert {
    condition     = output.helm_values.identity.mode == "pod-identity"
    error_message = "the example uses Pod Identity"
  }
  assert {
    condition     = output.helm_values.publicUrls.app == "https://app.example.com"
    error_message = "publicUrls.app is app_url"
  }
  assert {
    condition     = jsonencode(yamldecode(output.helm_values_yaml)) == jsonencode(output.helm_values)
    error_message = "helm_values_yaml round-trips to helm_values"
  }
}
