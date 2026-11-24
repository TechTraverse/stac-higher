output "bucket_name" {
  description = "Name of the demo destination bucket."
  value       = module.bucket.bucket_name
}

output "bucket_arn" {
  description = "ARN of the demo destination bucket."
  value       = module.bucket.bucket_arn
}

output "user_name" {
  description = "Name of the write-only IAM user."
  value       = aws_iam_user.this.name
}

output "user_arn" {
  description = "ARN of the write-only IAM user."
  value       = aws_iam_user.this.arn
}

output "credentials_secret_arn" {
  description = "ARN of the Secrets Manager entry holding the user's access key (never the key itself)."
  value       = aws_secretsmanager_secret.this.arn
}

output "credentials_secret_name" {
  description = "Name of the Secrets Manager entry holding the user's access key."
  value       = aws_secretsmanager_secret.this.name
}
