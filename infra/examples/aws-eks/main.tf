# This is the template a deployment repo copies. To use it:
#
#   1. Copy this directory into the deployment repo.
#   2. Replace the local module source below with
#      git::https://github.com/<owner>/<repo>.git//infra/terraform/modules/stac-higher?ref=vX.Y.Z
#   3. Add a backend in versions.tf (encrypted; generated secrets live in state).
#   4. Set real values for the variables.

provider "aws" {
  region = var.region
}

# The deployment creates this secret; see the registry module README.
data "aws_secretsmanager_secret" "ghcr" {
  name = var.ghcr_credentials_secret_name
}

module "stac_higher" {
  source = "../../terraform/modules/stac-higher"

  name_prefix                   = var.name_prefix
  app_url                       = var.app_url
  ghcr_credentials_secret_arn   = data.aws_secretsmanager_secret.ghcr.arn
  vpc_id                        = var.vpc_id
  db_subnet_ids                 = var.db_subnet_ids
  db_allowed_security_group_ids = var.db_allowed_security_group_ids
  cluster_name                  = var.cluster_name
  enable_cognito                = var.enable_cognito
  enable_demo_destination       = var.enable_demo_destination
}
