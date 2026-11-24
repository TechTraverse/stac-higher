data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  domain_prefix = coalesce(var.domain_prefix, var.name_prefix)

  groups = merge(
    { for g, _ in var.role_groups : g => "Role group (${var.role_groups[g]})" },
    { for g in var.data_groups : g => "Data group" },
  )

  issuer_url = "https://cognito-idp.${data.aws_region.current.region}.${data.aws_partition.current.dns_suffix}/${aws_cognito_user_pool.this.id}"

  # The hosted-domain suffix is Cognito's own, not the partition's.
  hosted_domain = "${local.domain_prefix}.auth.${data.aws_region.current.region}.amazoncognito.com"
}

resource "aws_cognito_user_pool" "this" {
  name                     = "${var.name_prefix}-users"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  mfa_configuration        = "OPTIONAL"
  deletion_protection      = var.deletion_protection ? "ACTIVE" : "INACTIVE"
  tags                     = var.tags

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  software_token_mfa_configuration {
    enabled = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

resource "aws_cognito_user_group" "this" {
  for_each     = local.groups
  name         = each.key
  user_pool_id = aws_cognito_user_pool.this.id
  description  = each.value
}

resource "aws_cognito_user_pool_client" "this" {
  name         = "${var.name_prefix}-app"
  user_pool_id = aws_cognito_user_pool.this.id

  # Public client: the app authenticates with PKCE (it always sends a
  # challenge; Cognito has no flag to require one on a public client).
  generate_secret                      = false
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["openid", "profile", "email"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = var.callback_urls
  logout_urls                          = var.logout_urls

  explicit_auth_flows           = ["ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true

  access_token_validity  = var.access_token_validity_minutes
  id_token_validity      = var.access_token_validity_minutes
  refresh_token_validity = var.refresh_token_validity_days

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

resource "aws_cognito_user_pool_domain" "this" {
  domain       = local.domain_prefix
  user_pool_id = aws_cognito_user_pool.this.id

  lifecycle {
    precondition {
      condition     = !can(regex("aws|amazon|cognito", local.domain_prefix))
      error_message = "The hosted domain prefix (domain_prefix, or name_prefix when null) must not contain aws, amazon or cognito; set domain_prefix explicitly."
    }
  }
}
