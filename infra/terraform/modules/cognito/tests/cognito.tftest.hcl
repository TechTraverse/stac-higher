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
  mock_resource "aws_cognito_user_pool" {
    defaults = { id = "us-gov-west-1_EXAMPLE" }
  }
}

variables {
  name_prefix   = "stac-test"
  callback_urls = ["https://app.example.com/api/auth/callback"]
  logout_urls   = ["https://app.example.com/"]
  tags          = { project = "stac-higher" }
}

run "user_pool_defaults" {
  command = plan
  assert {
    condition     = aws_cognito_user_pool.this.name == "stac-test-users"
    error_message = "pool name is <name_prefix>-users"
  }
  assert {
    condition     = one(aws_cognito_user_pool.this.admin_create_user_config).allow_admin_create_user_only == true
    error_message = "users are admin-created only (no self sign-up)"
  }
  assert {
    condition     = aws_cognito_user_pool.this.username_attributes == toset(["email"]) && aws_cognito_user_pool.this.auto_verified_attributes == toset(["email"])
    error_message = "email is the username and is auto-verified"
  }
  assert {
    condition     = aws_cognito_user_pool.this.deletion_protection == "ACTIVE"
    error_message = "deletion protection is on by default"
  }
  assert {
    condition     = aws_cognito_user_pool.this.mfa_configuration == "OPTIONAL" && one(aws_cognito_user_pool.this.software_token_mfa_configuration).enabled == true
    error_message = "MFA is optional TOTP"
  }
  assert {
    condition     = one(aws_cognito_user_pool.this.password_policy).minimum_length == 12
    error_message = "password minimum is 12"
  }
  assert {
    condition     = aws_cognito_user_pool.this.tags == tomap({ project = "stac-higher" })
    error_message = "the pool carries var.tags"
  }
}

run "public_pkce_client" {
  command = plan
  assert {
    condition     = aws_cognito_user_pool_client.this.name == "stac-test-app" && aws_cognito_user_pool_client.this.generate_secret == false
    error_message = "the client is public (no secret)"
  }
  assert {
    condition     = aws_cognito_user_pool_client.this.allowed_oauth_flows == toset(["code"]) && aws_cognito_user_pool_client.this.allowed_oauth_flows_user_pool_client == true
    error_message = "authorization-code flow only"
  }
  assert {
    condition     = aws_cognito_user_pool_client.this.allowed_oauth_scopes == toset(["openid", "profile", "email"])
    error_message = "scopes are openid profile email"
  }
  assert {
    condition     = aws_cognito_user_pool_client.this.supported_identity_providers == toset(["COGNITO"])
    error_message = "only the Cognito IdP"
  }
  assert {
    condition     = aws_cognito_user_pool_client.this.callback_urls == toset(var.callback_urls) && aws_cognito_user_pool_client.this.logout_urls == toset(var.logout_urls)
    error_message = "callback and logout URLs come from the inputs"
  }
  assert {
    condition     = aws_cognito_user_pool_client.this.explicit_auth_flows == toset(["ALLOW_REFRESH_TOKEN_AUTH"])
    error_message = "only refresh-token auth is enabled outside the hosted UI flow"
  }
  assert {
    condition     = aws_cognito_user_pool_client.this.prevent_user_existence_errors == "ENABLED" && aws_cognito_user_pool_client.this.enable_token_revocation == true
    error_message = "user-existence errors are hidden and tokens are revocable"
  }
}

run "token_validity" {
  command = plan
  assert {
    condition = (
      aws_cognito_user_pool_client.this.access_token_validity == 60 &&
      aws_cognito_user_pool_client.this.id_token_validity == 60 &&
      aws_cognito_user_pool_client.this.refresh_token_validity == 1
    )
    error_message = "defaults: 60 min access/id, 1 day refresh"
  }
  assert {
    condition = (
      one(aws_cognito_user_pool_client.this.token_validity_units).access_token == "minutes" &&
      one(aws_cognito_user_pool_client.this.token_validity_units).id_token == "minutes" &&
      one(aws_cognito_user_pool_client.this.token_validity_units).refresh_token == "days"
    )
    error_message = "units are minutes, minutes, days"
  }
}

run "groups_from_role_and_data_groups" {
  command = plan
  variables {
    data_groups = ["earth-observation"]
  }
  assert {
    condition     = toset(keys(aws_cognito_user_group.this)) == toset(["stac-admins", "stac-operators", "stac-members", "earth-observation"])
    error_message = "one group per role group plus one per data group"
  }
}

run "domain_defaults_to_prefix" {
  command = plan
  assert {
    condition     = aws_cognito_user_pool_domain.this.domain == "stac-test"
    error_message = "the hosted domain defaults to name_prefix"
  }
}

run "claims_mapping_output" {
  command = plan
  assert {
    condition = jsonencode(output.claims_mapping) == jsonencode({
      groups  = "cognito:groups"
      roles   = "cognito:groups"
      roleMap = { "stac-admins" = "admin", "stac-operators" = "operator", "stac-members" = "member" }
      name    = ["name", "email", "username"]
    })
    error_message = "claims_mapping must match the app's AUTH_CLAIMS_MAPPING shape"
  }
}

run "issuer_and_hosted_domain" {
  command = apply
  assert {
    condition     = output.issuer_url == "https://cognito-idp.us-gov-west-1.dns-suffix.example/us-gov-west-1_EXAMPLE"
    error_message = "issuer is https://cognito-idp.<region>.<dns_suffix>/<pool id>"
  }
  assert {
    condition     = output.hosted_domain == "stac-test.auth.us-gov-west-1.amazoncognito.com"
    error_message = "hosted domain is <prefix>.auth.<region>.amazoncognito.com"
  }
}

run "allows_localhost_callback" {
  command = plan
  variables {
    callback_urls = ["http://localhost:4321/api/auth/callback"]
  }
  assert {
    condition     = aws_cognito_user_pool_client.this.callback_urls == toset(["http://localhost:4321/api/auth/callback"])
    error_message = "http://localhost callbacks are allowed"
  }
}

run "rejects_http_callback" {
  command = plan
  variables {
    callback_urls = ["http://app.example.com/api/auth/callback"]
  }
  expect_failures = [var.callback_urls]
}

run "rejects_reserved_domain_prefix" {
  command = plan
  variables {
    domain_prefix = "stac-cognito"
  }
  expect_failures = [var.domain_prefix]
}

run "rejects_reserved_name_prefix_as_domain" {
  command = plan
  variables {
    name_prefix = "aws-test"
  }
  expect_failures = [aws_cognito_user_pool_domain.this]
}

run "rejects_unknown_role" {
  command = plan
  variables {
    role_groups = { "stac-root" = "superuser" }
  }
  expect_failures = [var.role_groups]
}

run "deletion_protection_off" {
  command = plan
  variables {
    deletion_protection = false
  }
  assert {
    condition     = aws_cognito_user_pool.this.deletion_protection == "INACTIVE"
    error_message = "deletion_protection = false plans INACTIVE"
  }
}

run "rejects_bad_name_prefix" {
  command = plan
  variables {
    name_prefix = "Stac_Test"
  }
  expect_failures = [var.name_prefix]
}
