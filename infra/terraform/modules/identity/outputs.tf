output "role_arns" {
  description = "Workload role ARNs keyed app, pipeline, titiler, cube_server; known at plan."
  value       = local.role_arn
}

output "role_names" {
  description = "Workload role names keyed app, pipeline, titiler, cube_server."
  value       = local.role_name
}

output "process_run_role_arn" {
  description = "ARN of the process-run role (the ceiling the per-run session policy narrows)."
  value       = local.process_run_role_arn
}

output "process_run_role_name" {
  description = "Name of the process-run role."
  value       = local.process_run_role_name
}

output "pod_identity_association_ids" {
  description = "Pod Identity association IDs keyed by workload; empty under irsa."
  value       = { for k, a in aws_eks_pod_identity_association.this : k => a.association_id }
}

output "workload_identity" {
  description = "Pass-through of the identity mode (pod_identity or irsa)."
  value       = var.workload_identity
}

output "namespace" {
  description = "Pass-through of the namespace the roles are bound to."
  value       = var.namespace
}

output "service_accounts" {
  description = "Pass-through of the ServiceAccount names the roles are bound to."
  value       = var.service_accounts
}
