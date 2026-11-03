output "user_pool_id" {
  description = "ID of the Cognito user pool."
  value       = aws_cognito_user_pool.this.id
}

output "user_pool_arn" {
  description = "ARN of the Cognito user pool."
  value       = aws_cognito_user_pool.this.arn
}

output "issuer_url" {
  description = "OIDC issuer URL (the app's OIDC_ISSUER): https://cognito-idp.<region>.<dns_suffix>/<pool id>."
  value       = local.issuer_url
}

output "client_id" {
  description = "ID of the public PKCE app client (OIDC_CLIENT_ID; also AUTH_BEARER_AUDIENCES and the proxy's ALLOWED_JWT_AUDIENCES)."
  value       = aws_cognito_user_pool_client.this.id
}

output "hosted_domain" {
  description = "Cognito hosted-UI domain, <prefix>.auth.<region>.amazoncognito.com (informational; the app reads endpoints from discovery)."
  value       = local.hosted_domain
}

output "claims_mapping" {
  description = "Object matching the app's AUTH_CLAIMS_MAPPING (jsonencode it): groups and roles from cognito:groups, roleMap from role_groups, name falling back to email then username."
  value = {
    groups  = "cognito:groups"
    roles   = "cognito:groups"
    roleMap = var.role_groups
    name    = ["name", "email", "username"]
  }
}

output "group_names" {
  description = "Names of every group created (role groups and data groups)."
  value       = toset(keys(local.groups))
}

output "callback_urls" {
  description = "Callback URLs registered on the app client."
  value       = var.callback_urls
}

output "logout_urls" {
  description = "Logout URLs registered on the app client."
  value       = var.logout_urls
}
