data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  partition  = data.aws_partition.current.partition
  dns_suffix = data.aws_partition.current.dns_suffix
  region     = data.aws_region.current.region
}

# The S3 gateway-endpoint prefix list, for the process-run NetworkPolicy.
data "aws_ec2_managed_prefix_list" "s3" {
  name = "${join(".", reverse(split(".", local.dns_suffix)))}.${local.region}.s3"
}

module "kms" {
  source = "../kms"

  name_prefix             = var.name_prefix
  tags                    = var.tags
  deletion_window_in_days = var.kms_deletion_window_in_days
}

module "storage" {
  source = "../storage"

  name_prefix          = var.name_prefix
  tags                 = var.tags
  name                 = var.bucket_name
  kms_key_arn          = module.kms.key_arn
  cors_allowed_origins = local.cors_allowed_origins
  force_destroy        = var.bucket_force_destroy
}

module "secrets" {
  source = "../secrets"

  name_prefix             = var.name_prefix
  tags                    = var.tags
  kms_key_arn             = module.kms.key_arn
  recovery_window_in_days = var.secrets_recovery_window_in_days
}

module "registry" {
  source = "../registry"

  name_prefix                      = var.name_prefix
  tags                             = var.tags
  kms_key_arn                      = module.kms.key_arn
  ghcr_credentials_secret_arn      = var.ghcr_credentials_secret_arn
  enable_dockerhub                 = var.enable_dockerhub
  dockerhub_credentials_secret_arn = var.dockerhub_credentials_secret_arn
  enable_quay                      = var.enable_quay
  quay_credentials_secret_arn      = var.quay_credentials_secret_arn
  ecr_prefixes                     = var.ecr_prefixes
  image_tag_mutability             = var.image_tag_mutability
}

module "database" {
  source = "../database"

  name_prefix                  = var.name_prefix
  tags                         = var.tags
  kms_key_arn                  = module.kms.key_arn
  vpc_id                       = var.vpc_id
  subnet_ids                   = var.db_subnet_ids
  allowed_security_group_ids   = var.db_allowed_security_group_ids
  instance_class               = var.db_instance_class
  engine_version               = var.db_engine_version
  db_name                      = var.db_name
  master_username              = var.db_master_username
  allocated_storage            = var.db_allocated_storage
  max_allocated_storage        = var.db_max_allocated_storage
  multi_az                     = var.db_multi_az
  deletion_protection          = var.db_deletion_protection
  backup_retention_period      = var.db_backup_retention_period
  backup_window                = var.db_backup_window
  maintenance_window           = var.db_maintenance_window
  skip_final_snapshot          = var.db_skip_final_snapshot
  parameter_overrides          = var.db_parameter_overrides
  ca_cert_identifier           = var.db_ca_cert_identifier
  performance_insights_enabled = var.db_performance_insights_enabled
  apply_immediately            = var.db_apply_immediately
}

module "identity" {
  source = "../identity"

  name_prefix       = var.name_prefix
  tags              = var.tags
  kms_key_arn       = module.kms.key_arn
  bucket_arn        = module.storage.bucket_arn
  workload_identity = var.workload_identity
  cluster_name      = var.cluster_name
  oidc_provider_arn = var.oidc_provider_arn
  namespace         = var.namespace
  service_accounts  = var.service_accounts
}

module "cognito" {
  source = "../cognito"
  count  = var.enable_cognito ? 1 : 0

  name_prefix                   = var.name_prefix
  tags                          = var.tags
  callback_urls                 = local.cognito_callback_urls
  logout_urls                   = local.cognito_logout_urls
  domain_prefix                 = var.cognito_domain_prefix
  role_groups                   = var.cognito_role_groups
  data_groups                   = var.cognito_data_groups
  deletion_protection           = var.cognito_deletion_protection
  access_token_validity_minutes = var.cognito_access_token_validity_minutes
  refresh_token_validity_days   = var.cognito_refresh_token_validity_days
}

module "demo_destination" {
  source = "../demo-destination"
  count  = var.enable_demo_destination ? 1 : 0

  name_prefix             = var.name_prefix
  tags                    = var.tags
  name                    = var.demo_destination_bucket_name
  kms_key_arn             = module.kms.key_arn
  force_destroy           = var.demo_destination_force_destroy
  recovery_window_in_days = var.secrets_recovery_window_in_days
}

locals {
  # null means "derive from app_url"; an explicit [] stays [] (no CORS), so no coalesce.
  cors_allowed_origins  = var.cors_allowed_origins == null ? [var.app_url] : var.cors_allowed_origins
  cognito_callback_urls = var.cognito_callback_urls == null ? ["${var.app_url}/api/auth/callback"] : var.cognito_callback_urls
  cognito_logout_urls   = var.cognito_logout_urls == null ? ["${var.app_url}/"] : var.cognito_logout_urls

  # The chart's names are camelCase; the Terraform inputs use snake_case.
  sa_helm = {
    app        = var.service_accounts.app
    pipeline   = var.service_accounts.pipeline
    titiler    = var.service_accounts.titiler
    cubeServer = var.service_accounts.cube_server
  }
  role_arns_helm = {
    app        = module.identity.role_arns["app"]
    pipeline   = module.identity.role_arns["pipeline"]
    titiler    = module.identity.role_arns["titiler"]
    cubeServer = module.identity.role_arns["cube_server"]
  }

  # Each optional section is an object when its module is on and null when off.
  auth = var.enable_cognito ? {
    mode          = "oidc"
    issuer        = module.cognito[0].issuer_url
    clientId      = module.cognito[0].client_id
    audienceClaim = "client_id"
    audiences     = [module.cognito[0].client_id]
    claimsMapping = module.cognito[0].claims_mapping
    hostedDomain  = module.cognito[0].hosted_domain
  } : null

  demo = var.enable_demo_destination ? {
    bucket               = module.demo_destination[0].bucket_name
    region               = local.region
    credentialsSecretArn = module.demo_destination[0].credentials_secret_arn
  } : null

  helm_base = {
    global = {
      imageRegistry = module.registry.ghcr_mirror_prefix
    }
    publicUrls = {
      app         = var.app_url
      objectStore = "https://s3.${local.region}.${local.dns_suffix}"
    }
    aws = {
      partition                   = local.partition
      region                      = local.region
      bucket                      = module.storage.bucket_name
      kmsKeyArn                   = module.kms.key_arn
      processRunRoleArn           = module.identity.process_run_role_arn
      processMaxCredentialSeconds = 3600
      s3PrefixListCidrs           = sort([for e in data.aws_ec2_managed_prefix_list.s3.entries : e.cidr])
    }
    registry = {
      host           = module.registry.registry_host
      mirrorPrefixes = module.registry.mirror_prefixes
    }
    database = {
      host             = module.database.address
      port             = module.database.port
      name             = module.database.db_name
      masterSecretArn  = module.database.master_user_secret_arn
      caCertIdentifier = module.database.ca_cert_identifier
    }
    identity = {
      mode            = replace(var.workload_identity, "_", "-")
      namespace       = var.namespace
      serviceAccounts = local.sa_helm
      roleArns        = local.role_arns_helm
    }
    secrets = {
      mode = "external-secrets"
      arns = module.secrets.secret_arns
    }
  }

  # Contract with the chart (R-6): keys are fixed; omit, never null (Helm deletes defaults on null). See infra/terraform/README.md.
  helm_values = merge(local.helm_base, { for k, v in { auth = local.auth, demoDestination = local.demo } : k => v if v != null })

  external_secrets_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadPlatformSecrets"
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
        Resource = concat(
          values(module.secrets.secret_arns),
          [module.database.master_user_secret_arn],
          module.demo_destination[*].credentials_secret_arn,
        )
      },
      {
        Sid       = "DecryptPlatformSecrets"
        Effect    = "Allow"
        Action    = ["kms:Decrypt"]
        Resource  = [module.kms.key_arn]
        Condition = { StringEquals = { "kms:ViaService" = "secretsmanager.${local.region}.${local.dns_suffix}" } }
      },
    ]
  })
}
