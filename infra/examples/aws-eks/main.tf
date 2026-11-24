# This is the template a deployment repo copies.
#
# A deployment uses git:: sources pinned by tag instead of the local paths
# below (see infra/terraform/README.md).
#
# R-5 replaces these four module calls with one call to the stac-higher
# composition module and adds the helm_values output.

provider "aws" {
  region = var.region
}

# The deployment creates this secret; see the registry module README.
data "aws_secretsmanager_secret" "ghcr" {
  name = var.ghcr_credentials_secret_name
}

module "kms" {
  source = "../../terraform/modules/kms"

  name_prefix = var.name_prefix
}

module "storage" {
  source = "../../terraform/modules/storage"

  name_prefix          = var.name_prefix
  kms_key_arn          = module.kms.key_arn
  cors_allowed_origins = [var.app_origin]
}

module "secrets" {
  source = "../../terraform/modules/secrets"

  name_prefix = var.name_prefix
  kms_key_arn = module.kms.key_arn
}

module "registry" {
  source = "../../terraform/modules/registry"

  name_prefix                 = var.name_prefix
  kms_key_arn                 = module.kms.key_arn
  ghcr_credentials_secret_arn = data.aws_secretsmanager_secret.ghcr.arn
}
