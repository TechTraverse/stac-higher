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
  kms_key_arn = "arn:aws-us-gov:kms:us-gov-west-1:000000000000:key/00000000-0000-0000-0000-000000000000"
}

run "bucket_name_from_prefix" {
  command = plan
  assert {
    condition     = aws_s3_bucket.this.bucket == "stac-test-platform" && aws_s3_bucket.this.force_destroy == false
    error_message = "bucket must be <name_prefix>-<name> and not force-destroyable by default"
  }
}

run "encrypts_with_cmk" {
  command = plan
  assert {
    condition     = one(one(aws_s3_bucket_server_side_encryption_configuration.this.rule).apply_server_side_encryption_by_default).sse_algorithm == "aws:kms"
    error_message = "default encryption must be aws:kms"
  }
  assert {
    condition     = one(one(aws_s3_bucket_server_side_encryption_configuration.this.rule).apply_server_side_encryption_by_default).kms_master_key_id == var.kms_key_arn
    error_message = "default encryption must use the supplied CMK"
  }
  assert {
    condition     = one(aws_s3_bucket_server_side_encryption_configuration.this.rule).bucket_key_enabled == true
    error_message = "bucket key must be enabled"
  }
}

run "public_access_blocked" {
  command = plan
  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.this.block_public_acls,
      aws_s3_bucket_public_access_block.this.block_public_policy,
      aws_s3_bucket_public_access_block.this.ignore_public_acls,
      aws_s3_bucket_public_access_block.this.restrict_public_buckets,
    ])
    error_message = "all four public access block flags must be true"
  }
}

run "acls_disabled" {
  command = plan
  assert {
    condition     = one(aws_s3_bucket_ownership_controls.this.rule).object_ownership == "BucketOwnerEnforced"
    error_message = "ACLs must be disabled via BucketOwnerEnforced"
  }
}

run "versioning_disabled" {
  command = plan
  assert {
    condition     = one(aws_s3_bucket_versioning.this.versioning_configuration).status == "Disabled"
    error_message = "versioning must stay disabled (ADR 0011)"
  }
}

run "tls_only_policy" {
  command = plan
  assert {
    condition     = jsondecode(aws_s3_bucket_policy.this.policy).Statement[0].Effect == "Deny"
    error_message = "statement 0 must Deny"
  }
  assert {
    condition     = jsondecode(aws_s3_bucket_policy.this.policy).Statement[0].Principal == "*" && jsondecode(aws_s3_bucket_policy.this.policy).Statement[0].Action == "s3:*"
    error_message = "statement must apply to every principal and action"
  }
  assert {
    condition     = toset(jsondecode(aws_s3_bucket_policy.this.policy).Statement[0].Resource) == toset(["arn:aws-us-gov:s3:::stac-test-platform", "arn:aws-us-gov:s3:::stac-test-platform/*"])
    error_message = "resources must be the bucket and its objects in the mocked partition"
  }
  assert {
    condition     = jsondecode(aws_s3_bucket_policy.this.policy).Statement[0].Condition.Bool["aws:SecureTransport"] == "false"
    error_message = "statement must match insecure transport"
  }
}

run "aborts_multipart_after_7_days" {
  command = plan
  assert {
    condition     = length(aws_s3_bucket_lifecycle_configuration.this.rule) == 1 && one(aws_s3_bucket_lifecycle_configuration.this.rule).status == "Enabled"
    error_message = "exactly one enabled lifecycle rule"
  }
  assert {
    condition     = one(one(aws_s3_bucket_lifecycle_configuration.this.rule).abort_incomplete_multipart_upload).days_after_initiation == 7
    error_message = "incomplete multipart uploads must abort after 7 days"
  }
  assert {
    condition     = length(one(aws_s3_bucket_lifecycle_configuration.this.rule).expiration) == 0
    error_message = "no expiration rule: object deletion belongs to asset_gc (ADR 0011)"
  }
}

run "no_cors_by_default" {
  command = plan
  assert {
    condition     = length(aws_s3_bucket_cors_configuration.this) == 0
    error_message = "no CORS configuration without origins"
  }
}

run "cors_from_origins" {
  command = plan
  variables {
    cors_allowed_origins = ["https://app.example.com", "http://localhost:4321"]
  }
  assert {
    condition     = toset(one(aws_s3_bucket_cors_configuration.this[0].cors_rule).allowed_origins) == toset(["https://app.example.com", "http://localhost:4321"])
    error_message = "allowed_origins must be the supplied origins"
  }
  assert {
    condition     = toset(one(aws_s3_bucket_cors_configuration.this[0].cors_rule).allowed_methods) == toset(["GET", "HEAD", "PUT"])
    error_message = "allowed_methods must be GET, HEAD, PUT"
  }
  assert {
    condition     = one(aws_s3_bucket_cors_configuration.this[0].cors_rule).allowed_headers == toset(["*"]) && one(aws_s3_bucket_cors_configuration.this[0].cors_rule).expose_headers == toset(["ETag"])
    error_message = "allowed_headers must be * and expose_headers ETag"
  }
  assert {
    condition     = one(aws_s3_bucket_cors_configuration.this[0].cors_rule).max_age_seconds == 3000
    error_message = "max_age_seconds must be 3000"
  }
}

run "rejects_trailing_slash_origin" {
  command = plan
  variables {
    cors_allowed_origins = ["https://app.example.com/"]
  }
  expect_failures = [var.cors_allowed_origins]
}

run "rejects_wildcard_origin" {
  command = plan
  variables {
    cors_allowed_origins = ["*"]
  }
  expect_failures = [var.cors_allowed_origins]
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
