output "key_arn" {
  description = "ARN of the platform KMS key (full ARN; ECR templates require it)."
  value       = aws_kms_key.this.arn
}

output "key_id" {
  description = "Key id of the platform KMS key."
  value       = aws_kms_key.this.key_id
}

output "alias_name" {
  description = "Alias name of the key (alias/<name_prefix>)."
  value       = aws_kms_alias.this.name
}

output "alias_arn" {
  description = "ARN of the key alias."
  value       = aws_kms_alias.this.arn
}
