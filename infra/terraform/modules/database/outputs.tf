output "address" {
  description = "Database host name (no port)."
  value       = aws_db_instance.this.address
}

output "port" {
  description = "Database port."
  value       = aws_db_instance.this.port
}

output "db_name" {
  description = "Initial database name."
  value       = aws_db_instance.this.db_name
}

output "master_username" {
  description = "Master user name; its password is in master_user_secret_arn."
  value       = aws_db_instance.this.username
}

output "master_user_secret_arn" {
  description = "ARN of the RDS-managed Secrets Manager secret holding the master credentials (read only by the chart's bootstrap Job)."
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}

output "security_group_id" {
  description = "ID of the database security group."
  value       = aws_security_group.this.id
}

output "instance_arn" {
  description = "ARN of the RDS instance."
  value       = aws_db_instance.this.arn
}

output "instance_identifier" {
  description = "Identifier of the RDS instance, <name_prefix>-db."
  value       = aws_db_instance.this.identifier
}

output "parameter_group_name" {
  description = "Name of the pgstac parameter group."
  value       = aws_db_parameter_group.this.name
}

output "ca_cert_identifier" {
  description = "RDS CA certificate identifier in use; the client CA bundle must match it."
  value       = aws_db_instance.this.ca_cert_identifier
}
