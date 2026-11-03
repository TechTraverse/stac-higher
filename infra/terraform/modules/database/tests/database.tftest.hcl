mock_provider "aws" {
  mock_data "aws_partition" {
    defaults = { partition = "aws-us-gov", dns_suffix = "dns-suffix.example" }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "000000000000" }
  }
  mock_data "aws_region" {
    defaults = { region = "us-gov-west-1" }
  }
  mock_resource "aws_db_instance" {
    defaults = {
      address = "stac-test-db.example.com"
      port    = 5432
      master_user_secret = [{
        secret_arn    = "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:rds!db-00000000-AbCdEf"
        kms_key_id    = "arn:aws-us-gov:kms:us-gov-west-1:000000000000:key/00000000-0000-0000-0000-000000000000"
        secret_status = "active"
      }]
    }
  }
}

variables {
  name_prefix                = "stac-test"
  kms_key_arn                = "arn:aws-us-gov:kms:us-gov-west-1:000000000000:key/00000000-0000-0000-0000-000000000000"
  vpc_id                     = "vpc-00000000000000000"
  subnet_ids                 = ["subnet-00000000000000000", "subnet-00000000000000001"]
  allowed_security_group_ids = ["sg-00000000000000000"]
}

run "instance_defaults" {
  command = plan
  variables {
    tags = { project = "stac-higher" }
  }
  assert {
    condition     = aws_db_instance.this.identifier == "stac-test-db"
    error_message = "identifier is <name_prefix>-db"
  }
  assert {
    condition     = aws_db_instance.this.engine == "postgres" && aws_db_instance.this.engine_version == "17"
    error_message = "PostgreSQL 17"
  }
  assert {
    condition     = aws_db_instance.this.instance_class == "db.t4g.medium"
    error_message = "default instance class"
  }
  assert {
    condition     = aws_db_instance.this.db_name == "postgis" && aws_db_instance.this.username == "stac_higher_master"
    error_message = "database postgis, master stac_higher_master"
  }
  assert {
    condition     = aws_db_instance.this.publicly_accessible == false
    error_message = "never publicly accessible"
  }
  assert {
    condition     = aws_db_instance.this.storage_encrypted == true && aws_db_instance.this.kms_key_id == var.kms_key_arn
    error_message = "storage encrypted with the platform key"
  }
  assert {
    condition     = aws_db_instance.this.storage_type == "gp3" && aws_db_instance.this.allocated_storage == 50 && aws_db_instance.this.max_allocated_storage == 200
    error_message = "gp3, 50 GiB, autoscaling to 200"
  }
  assert {
    condition     = aws_db_instance.this.copy_tags_to_snapshot == true && aws_db_instance.this.auto_minor_version_upgrade == true
    error_message = "tags copy to snapshots; minor upgrades on"
  }
  assert {
    condition     = aws_db_instance.this.ca_cert_identifier == "rds-ca-rsa2048-g1"
    error_message = "default CA identifier"
  }
  assert {
    condition     = aws_db_instance.this.tags == tomap({ project = "stac-higher" })
    error_message = "instance tags equal the input"
  }
  assert {
    condition     = aws_security_group.this.tags == tomap({ project = "stac-higher" })
    error_message = "security group tags equal the input"
  }
  assert {
    condition     = aws_db_parameter_group.this.tags == tomap({ project = "stac-higher" })
    error_message = "parameter group tags equal the input"
  }
}

run "managed_master_password" {
  command = plan
  assert {
    condition     = aws_db_instance.this.manage_master_user_password == true
    error_message = "the master password is RDS-managed"
  }
  assert {
    condition     = aws_db_instance.this.master_user_secret_kms_key_id == var.kms_key_arn
    error_message = "the master secret is encrypted with the platform key"
  }
  assert {
    condition     = aws_db_instance.this.password == null
    error_message = "no Terraform-held password"
  }
}

run "toggles_default" {
  command = plan
  assert {
    condition     = aws_db_instance.this.multi_az == false
    error_message = "single-AZ by default"
  }
  assert {
    condition     = aws_db_instance.this.deletion_protection == true
    error_message = "deletion protection on by default"
  }
  assert {
    condition     = aws_db_instance.this.backup_retention_period == 7
    error_message = "7 days of backups by default"
  }
  assert {
    condition     = aws_db_instance.this.skip_final_snapshot == false
    error_message = "final snapshot by default"
  }
  assert {
    condition     = aws_db_instance.this.final_snapshot_identifier == "stac-test-db-final"
    error_message = "final snapshot named <name_prefix>-db-final"
  }
}

run "toggles_override" {
  command = plan
  variables {
    multi_az                = true
    deletion_protection     = false
    backup_retention_period = 14
    skip_final_snapshot     = true
  }
  assert {
    condition     = aws_db_instance.this.multi_az == true
    error_message = "multi_az follows the input"
  }
  assert {
    condition     = aws_db_instance.this.deletion_protection == false
    error_message = "deletion_protection follows the input"
  }
  assert {
    condition     = aws_db_instance.this.backup_retention_period == 14
    error_message = "backup_retention_period follows the input"
  }
  assert {
    condition     = aws_db_instance.this.final_snapshot_identifier == null
    error_message = "no final snapshot identifier when skipping the snapshot"
  }
}

run "pgstac_parameters" {
  command = plan
  assert {
    condition     = aws_db_parameter_group.this.family == "postgres17"
    error_message = "family follows the engine major"
  }
  assert {
    condition     = aws_db_parameter_group.this.name_prefix == "stac-test-pgstac-"
    error_message = "parameter group name_prefix"
  }
  assert {
    condition = { for p in aws_db_parameter_group.this.parameter : p.name => p.value } == {
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
    error_message = "parameters must be exactly the ten-entry pgstac table"
  }
  assert {
    condition     = alltrue([for p in aws_db_parameter_group.this.parameter : p.apply_method == "pending-reboot"])
    error_message = "every parameter applies at reboot"
  }
}

run "parameter_override_merges" {
  command = plan
  variables {
    parameter_overrides = { work_mem = "8192", log_min_duration_statement = "1000" }
  }
  assert {
    condition     = { for p in aws_db_parameter_group.this.parameter : p.name => p.value }["work_mem"] == "8192"
    error_message = "work_mem override wins"
  }
  assert {
    condition     = { for p in aws_db_parameter_group.this.parameter : p.name => p.value }["log_min_duration_statement"] == "1000"
    error_message = "new parameters are added"
  }
  assert {
    condition     = { for p in aws_db_parameter_group.this.parameter : p.name => p.value }["shared_buffers"] == "{DBInstanceClassMemory/32768}"
    error_message = "untouched defaults stay"
  }
  assert {
    condition     = { for p in aws_db_parameter_group.this.parameter : p.name => p.value }["rds.force_ssl"] == "1"
    error_message = "TLS stays forced"
  }
  assert {
    condition     = length(aws_db_parameter_group.this.parameter) == 11
    error_message = "ten defaults plus one new parameter"
  }
}

run "rejects_force_ssl_override" {
  command = plan
  variables {
    parameter_overrides = { "rds.force_ssl" = "0" }
  }
  expect_failures = [var.parameter_overrides]
}

run "family_follows_major" {
  command = plan
  variables {
    engine_version = "16.4"
  }
  assert {
    condition     = aws_db_parameter_group.this.family == "postgres16"
    error_message = "family is postgres<major>"
  }
}

run "rejects_old_engine" {
  command = plan
  variables {
    engine_version = "15"
  }
  expect_failures = [var.engine_version]
}

run "security_group_ingress_per_allowed_sg" {
  command = plan
  variables {
    allowed_security_group_ids = ["sg-00000000000000000", "sg-00000000000000001"]
  }
  assert {
    condition     = aws_security_group.this.vpc_id == var.vpc_id
    error_message = "security group lives in the input VPC"
  }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.postgres) == 2
    error_message = "one ingress rule per allowed security group"
  }
  assert {
    condition = alltrue([
      for r in aws_vpc_security_group_ingress_rule.postgres :
      r.ip_protocol == "tcp" && r.from_port == 5432 && r.to_port == 5432 && contains(var.allowed_security_group_ids, r.referenced_security_group_id)
    ])
    error_message = "each rule is tcp 5432 from an allowed security group"
  }
}

run "subnet_group_from_inputs" {
  command = plan
  assert {
    condition     = aws_db_subnet_group.this.name == "stac-test-db"
    error_message = "subnet group named <name_prefix>-db"
  }
  assert {
    condition     = toset(aws_db_subnet_group.this.subnet_ids) == toset(var.subnet_ids)
    error_message = "subnet group uses the input subnets"
  }
}

run "performance_insights_encrypted" {
  command = plan
  assert {
    condition     = aws_db_instance.this.performance_insights_enabled == true
    error_message = "performance insights on by default"
  }
  assert {
    condition     = aws_db_instance.this.performance_insights_kms_key_id == var.kms_key_arn
    error_message = "performance insights use the platform key"
  }
}

run "rejects_single_subnet" {
  command = plan
  variables {
    subnet_ids = ["subnet-00000000000000000"]
  }
  expect_failures = [var.subnet_ids]
}

run "rejects_no_allowed_security_group" {
  command = plan
  variables {
    allowed_security_group_ids = []
  }
  expect_failures = [var.allowed_security_group_ids]
}

run "rejects_kms_key_id" {
  command = plan
  variables {
    kms_key_arn = "00000000-0000-0000-0000-000000000000"
  }
  expect_failures = [var.kms_key_arn]
}

run "rejects_bad_name_prefix" {
  command = plan
  variables {
    name_prefix = "Stac_Test"
  }
  expect_failures = [var.name_prefix]
}

run "outputs_from_instance" {
  command = apply
  assert {
    condition     = output.address == "stac-test-db.example.com"
    error_message = "address comes from the instance"
  }
  assert {
    condition     = output.port == 5432
    error_message = "port comes from the instance"
  }
  assert {
    condition     = output.master_user_secret_arn == "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:rds!db-00000000-AbCdEf"
    error_message = "master secret ARN comes from the instance"
  }
  assert {
    condition     = output.db_name == "postgis"
    error_message = "db_name output"
  }
}
