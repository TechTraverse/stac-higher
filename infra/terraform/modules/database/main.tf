locals {
  engine_major = tonumber(split(".", var.engine_version)[0])

  # Ported from eoapi-cdk v11.8.1 PgStacDatabase.getParameters() as RDS
  # formulas, so the same ratios hold for every instance class.
  pgstac_parameters = {
    "max_connections"           = "LEAST({DBInstanceClassMemory/9531392},5000)"
    "shared_buffers"            = "{DBInstanceClassMemory/32768}"
    "effective_cache_size"      = "{DBInstanceClassMemory/10923}"
    "maintenance_work_mem"      = "{DBInstanceClassMemory/16384}"
    "work_mem"                  = "2327"
    "max_locks_per_transaction" = "1024"
    "temp_buffers"              = "16384"
    "seq_page_cost"             = "1"
    "random_page_cost"          = "1.1"
    "rds.force_ssl"             = "1"
  }

  parameters = merge(local.pgstac_parameters, var.parameter_overrides)
}

resource "aws_db_subnet_group" "this" {
  name       = "${var.name_prefix}-db"
  subnet_ids = var.subnet_ids
  tags       = var.tags
}

# No inline rules and no egress rule: RDS needs none, and Terraform removes
# AWS's default allow-all egress rule.
resource "aws_security_group" "this" {
  name        = "${var.name_prefix}-db"
  description = "stac-higher PostgreSQL: 5432 from the allowed security groups only"
  vpc_id      = var.vpc_id
  tags        = var.tags
}

resource "aws_vpc_security_group_ingress_rule" "postgres" {
  for_each = { for i, sg in var.allowed_security_group_ids : tostring(i) => sg }

  security_group_id            = aws_security_group.this.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = each.value
  description                  = "PostgreSQL from ${each.value}"
  tags                         = var.tags
}

resource "aws_db_parameter_group" "this" {
  name_prefix = "${var.name_prefix}-pgstac-"
  family      = "postgres${local.engine_major}"
  description = "pgstac tuning from eoapi-cdk v11.8.1 PgStacDatabase; TLS enforced"

  dynamic "parameter" {
    for_each = local.parameters

    content {
      name         = parameter.key
      value        = parameter.value
      apply_method = "pending-reboot"
    }
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = var.tags
}

resource "aws_db_instance" "this" {
  identifier     = "${var.name_prefix}-db"
  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class

  db_name  = var.db_name
  username = var.master_username

  manage_master_user_password   = true
  master_user_secret_kms_key_id = var.kms_key_arn

  storage_type          = "gp3"
  allocated_storage     = var.allocated_storage
  max_allocated_storage = var.max_allocated_storage
  storage_encrypted     = true
  kms_key_id            = var.kms_key_arn

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.this.id]
  parameter_group_name   = aws_db_parameter_group.this.name
  publicly_accessible    = false
  ca_cert_identifier     = var.ca_cert_identifier

  multi_az                = var.multi_az
  deletion_protection     = var.deletion_protection
  backup_retention_period = var.backup_retention_period
  backup_window           = var.backup_window
  maintenance_window      = var.maintenance_window

  skip_final_snapshot       = var.skip_final_snapshot
  final_snapshot_identifier = var.skip_final_snapshot ? null : "${var.name_prefix}-db-final"
  copy_tags_to_snapshot     = true

  auto_minor_version_upgrade  = true
  allow_major_version_upgrade = var.allow_major_version_upgrade
  apply_immediately           = var.apply_immediately

  performance_insights_enabled    = var.performance_insights_enabled
  performance_insights_kms_key_id = var.performance_insights_enabled ? var.kms_key_arn : null

  tags = var.tags
}
