output "secret_arns" {
  description = "Map of entry key to Secrets Manager ARN (ARNs only, never values)."
  value       = { for k, s in aws_secretsmanager_secret.this : k => s.arn }
}

output "secret_names" {
  description = "Map of entry key to Secrets Manager name, <name_prefix>/<suffix>."
  value       = { for k, s in aws_secretsmanager_secret.this : k => s.name }
}

output "db_role_names" {
  description = "Database login roles with a generated password, in spec 7.2 order."
  value       = ["stac_higher_owner", "stac_api", "stac_reader", "stac_proxy_policy", "cube_server"]
}
