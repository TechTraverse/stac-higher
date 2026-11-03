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
  mock_data "aws_ec2_managed_prefix_list" {
    defaults = {
      entries = [{ cidr = "192.0.2.0/24", description = "placeholder (RFC 5737)" }]
    }
  }
  mock_resource "aws_kms_key" {
    defaults = {
      arn = "arn:aws-us-gov:kms:us-gov-west-1:000000000000:key/00000000-0000-0000-0000-000000000000"
    }
  }
  mock_resource "aws_s3_bucket" {
    defaults = {
      id  = "stac-test-platform"
      arn = "arn:aws-us-gov:s3:::stac-test-platform"
    }
  }
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws-us-gov:iam::000000000000:role/mock"
    }
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
  mock_resource "aws_cognito_user_pool" {
    defaults = {
      id = "us-gov-west-1_EXAMPLE"
    }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = {
      arn = "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:stac-test/mock-AbCdEf"
    }
  }
  mock_resource "aws_iam_user" {
    defaults = {
      arn = "arn:aws-us-gov:iam::000000000000:user/stac-higher/stac-test-demo-destination"
    }
  }
  mock_resource "aws_iam_access_key" {
    defaults = {
      id     = "AKIAMOCKMOCKMOCKMOCK"
      secret = "mock-secret-access-key"
    }
  }
}

variables {
  name_prefix                   = "stac-test"
  app_url                       = "https://app.example.com"
  ghcr_credentials_secret_arn   = "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:ecr-pullthroughcache/ghcr-AbCdEf"
  vpc_id                        = "vpc-00000000000000000"
  db_subnet_ids                 = ["subnet-00000000000000000", "subnet-00000000000000001"]
  db_allowed_security_group_ids = ["sg-00000000000000000"]
  cluster_name                  = "example-cluster"
}

run "helm_values_top_level_keys" {
  command = plan
  assert {
    condition     = keys(output.helm_values) == ["aws", "database", "global", "identity", "publicUrls", "registry", "secrets"]
    error_message = "top-level helm_values keys are the chart contract"
  }
}

run "helm_values_nested_keys" {
  command = plan
  assert {
    condition     = keys(output.helm_values.aws) == ["bucket", "kmsKeyArn", "partition", "processMaxCredentialSeconds", "processRunRoleArn", "region", "s3PrefixListCidrs"]
    error_message = "aws keys"
  }
  assert {
    condition     = keys(output.helm_values.database) == ["caCertIdentifier", "host", "masterSecretArn", "name", "port"]
    error_message = "database keys"
  }
  assert {
    condition = (
      keys(output.helm_values.identity) == ["mode", "namespace", "roleArns", "serviceAccounts"]
      && keys(output.helm_values.identity.roleArns) == ["app", "cubeServer", "pipeline", "titiler"]
      && keys(output.helm_values.identity.serviceAccounts) == ["app", "cubeServer", "pipeline", "titiler"]
    )
    error_message = "identity keys (camelCase cubeServer)"
  }
  assert {
    condition = (
      keys(output.helm_values.secrets) == ["arns", "mode"]
      && keys(output.helm_values.registry) == ["host", "mirrorPrefixes"]
      && keys(output.helm_values.global) == ["imageRegistry"]
      && keys(output.helm_values.publicUrls) == ["app", "objectStore"]
    )
    error_message = "secrets, registry, global and publicUrls keys"
  }
}

run "helm_values_values" {
  command = apply
  assert {
    condition     = output.helm_values.aws.partition == "aws-us-gov" && output.helm_values.aws.region == "us-gov-west-1"
    error_message = "partition and region come from data sources"
  }
  assert {
    condition     = output.helm_values.aws.bucket == "stac-test-platform"
    error_message = "aws.bucket is the platform bucket"
  }
  assert {
    condition     = output.helm_values.publicUrls.objectStore == "https://s3.us-gov-west-1.dns-suffix.example" && output.helm_values.publicUrls.app == "https://app.example.com"
    error_message = "public URLs"
  }
  assert {
    condition     = output.helm_values.aws.processMaxCredentialSeconds == 3600 && jsonencode(output.helm_values.aws.s3PrefixListCidrs) == jsonencode(["192.0.2.0/24"])
    error_message = "credential cap and prefix list CIDRs"
  }
  assert {
    condition = (
      output.helm_values.aws.processRunRoleArn == "arn:aws-us-gov:iam::000000000000:role/stac-test-process-run"
      && output.helm_values.identity.roleArns.cubeServer == "arn:aws-us-gov:iam::000000000000:role/stac-test-cube-server"
    )
    error_message = "role ARNs come from the identity module"
  }
  assert {
    condition     = output.helm_values.identity.mode == "pod-identity" && output.helm_values.identity.namespace == "stac-higher"
    error_message = "identity mode is spelled pod-identity in helm_values"
  }
  assert {
    condition = jsonencode(output.helm_values.identity.serviceAccounts) == jsonencode({
      app        = "stac-higher-app"
      cubeServer = "stac-higher-cube-server"
      pipeline   = "stac-higher-pipeline"
      titiler    = "stac-higher-titiler"
    })
    error_message = "service accounts map cube_server to cubeServer"
  }
  assert {
    condition     = output.helm_values.global.imageRegistry == "000000000000.dkr.ecr.us-gov-west-1.dns-suffix.example/ghcr" && keys(output.helm_values.registry.mirrorPrefixes) == ["ghcr.io"]
    error_message = "image registry is the ghcr mirror prefix"
  }
  assert {
    condition = (
      output.helm_values.secrets.mode == "external-secrets"
      && keys(output.helm_values.secrets.arns) == [
        "catalog_bff_shared_secret", "credentials_master_key", "db_cube_server", "db_stac_api", "db_stac_higher_owner",
        "db_stac_proxy_policy", "db_stac_reader", "internal_api_token", "proxy_auth_token", "session_secret",
      ]
    )
    error_message = "secrets.arns carries the secrets module's ten keys verbatim"
  }
  assert {
    condition = (
      output.helm_values.database.host == "stac-test-db.example.com"
      && output.helm_values.database.port == 5432
      && output.helm_values.database.name == "postgis"
      && output.helm_values.database.caCertIdentifier == "rds-ca-rsa2048-g1"
      && output.helm_values.database.masterSecretArn == "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:rds!db-00000000-AbCdEf"
    )
    error_message = "database values come from the database module"
  }
}

run "helm_values_yaml_roundtrip" {
  command = apply
  assert {
    condition     = jsonencode(yamldecode(output.helm_values_yaml)) == jsonencode(output.helm_values)
    error_message = "helm_values_yaml must round-trip to helm_values"
  }
}

run "s3_prefix_list_lookup" {
  command = plan
  assert {
    condition     = data.aws_ec2_managed_prefix_list.s3.name == "example.dns-suffix.us-gov-west-1.s3"
    error_message = "the S3 prefix list name is reverse(dns_suffix).region.s3"
  }
}

run "optional_modules_off" {
  command = apply
  assert {
    condition     = output.cognito_client_id == null && output.demo_destination_bucket_name == null
    error_message = "optional module outputs are null when off"
  }
  assert {
    condition     = !contains(keys(output.helm_values), "auth") && !contains(keys(output.helm_values), "demoDestination")
    error_message = "auth and demoDestination are omitted, not null, when off"
  }
  assert {
    condition     = length([for s in jsondecode(output.external_secrets_policy_json).Statement : s.Resource if s.Sid == "ReadPlatformSecrets"][0]) == 11
    error_message = "read statement lists 10 platform secrets and the master secret when the demo destination is off"
  }
}

run "cognito_and_demo_on" {
  command = apply
  variables {
    enable_cognito          = true
    enable_demo_destination = true
  }
  override_resource {
    target = module.demo_destination[0].module.bucket.aws_s3_bucket.this
    values = {
      id  = "stac-test-demo-destination"
      arn = "arn:aws-us-gov:s3:::stac-test-demo-destination"
    }
  }
  override_resource {
    target = module.demo_destination[0].aws_secretsmanager_secret.this
    values = {
      arn = "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:stac-test/demo-AbCdEf"
    }
  }
  assert {
    condition     = keys(output.helm_values) == ["auth", "aws", "database", "demoDestination", "global", "identity", "publicUrls", "registry", "secrets"]
    error_message = "auth and demoDestination appear when enabled"
  }
  assert {
    condition = (
      keys(output.helm_values.auth) == ["audienceClaim", "audiences", "claimsMapping", "clientId", "hostedDomain", "issuer", "mode"]
      && output.helm_values.auth.mode == "oidc"
      && output.helm_values.auth.audienceClaim == "client_id"
      && output.helm_values.auth.audiences == [output.helm_values.auth.clientId]
    )
    error_message = "auth section shape"
  }
  assert {
    condition     = output.helm_values.auth.issuer == "https://cognito-idp.us-gov-west-1.dns-suffix.example/us-gov-west-1_EXAMPLE"
    error_message = "issuer derives from region, dns suffix and pool id"
  }
  assert {
    condition     = keys(output.helm_values.demoDestination) == ["bucket", "credentialsSecretArn", "region"] && output.helm_values.demoDestination.region == "us-gov-west-1"
    error_message = "demoDestination section shape"
  }
  assert {
    condition     = output.helm_values.demoDestination.bucket == "stac-test-demo-destination" && output.helm_values.aws.bucket == "stac-test-platform" && output.helm_values.demoDestination.credentialsSecretArn == "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:stac-test/demo-AbCdEf"
    error_message = "demoDestination carries the bucket name (not ARN) and the credentials secret ARN"
  }
  assert {
    condition     = output.helm_values.auth.hostedDomain == "stac-test.auth.us-gov-west-1.amazoncognito.com"
    error_message = "auth.hostedDomain comes from the cognito module"
  }
  assert {
    condition = jsonencode(output.helm_values.auth.claimsMapping) == jsonencode({
      groups  = "cognito:groups"
      name    = ["name", "email", "username"]
      roleMap = { "stac-admins" = "admin", "stac-members" = "member", "stac-operators" = "operator" }
      roles   = "cognito:groups"
    })
    error_message = "auth.claimsMapping comes from the cognito module"
  }
  assert {
    condition     = jsonencode(output.cognito_callback_urls) == jsonencode(["https://app.example.com/api/auth/callback"]) && jsonencode(output.cognito_logout_urls) == jsonencode(["https://app.example.com/"])
    error_message = "Cognito URLs default from app_url"
  }
}

run "irsa_mode" {
  command = plan
  variables {
    workload_identity = "irsa"
    oidc_provider_arn = "arn:aws-us-gov:iam::000000000000:oidc-provider/oidc.eks.us-gov-west-1.dns-suffix.example/id/EXAMPLE"
    cluster_name      = null
  }
  assert {
    condition     = output.helm_values.identity.mode == "irsa"
    error_message = "irsa mode is passed through"
  }
}

run "external_secrets_policy" {
  command = apply
  variables {
    enable_demo_destination = true
  }
  override_resource {
    target = module.demo_destination[0].aws_secretsmanager_secret.this
    values = {
      arn = "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:stac-test/demo-AbCdEf"
    }
  }
  assert {
    condition = (
      toset([for s in jsondecode(output.external_secrets_policy_json).Statement : s.Sid]) == toset(["ReadPlatformSecrets", "DecryptPlatformSecrets"])
    )
    error_message = "two statements"
  }
  assert {
    condition = (
      toset([for s in jsondecode(output.external_secrets_policy_json).Statement : toset(s.Action) if s.Sid == "ReadPlatformSecrets"][0]) == toset(["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"])
      && contains([for s in jsondecode(output.external_secrets_policy_json).Statement : s.Resource if s.Sid == "ReadPlatformSecrets"][0], "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:rds!db-00000000-AbCdEf")
      && contains([for s in jsondecode(output.external_secrets_policy_json).Statement : s.Resource if s.Sid == "ReadPlatformSecrets"][0], "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:stac-test/mock-AbCdEf")
    )
    error_message = "read statement covers platform, master and demo secrets"
  }
  assert {
    condition     = contains([for s in jsondecode(output.external_secrets_policy_json).Statement : s.Resource if s.Sid == "ReadPlatformSecrets"][0], "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:stac-test/demo-AbCdEf")
    error_message = "read statement must include the demo destination credentials secret"
  }
  assert {
    condition     = length([for s in jsondecode(output.external_secrets_policy_json).Statement : s.Resource if s.Sid == "ReadPlatformSecrets"][0]) == 12
    error_message = "read statement lists 10 platform secrets, the master secret and the demo secret"
  }
  assert {
    condition = (
      [for s in jsondecode(output.external_secrets_policy_json).Statement : s.Action if s.Sid == "DecryptPlatformSecrets"][0] == ["kms:Decrypt"]
      && [for s in jsondecode(output.external_secrets_policy_json).Statement : s.Condition.StringEquals["kms:ViaService"] if s.Sid == "DecryptPlatformSecrets"][0] == "secretsmanager.us-gov-west-1.dns-suffix.example"
    )
    error_message = "decrypt only through Secrets Manager"
  }
  assert {
    condition     = jsonencode([for s in jsondecode(output.external_secrets_policy_json).Statement : s.Resource if s.Sid == "DecryptPlatformSecrets"][0]) == jsonencode(["arn:aws-us-gov:kms:us-gov-west-1:000000000000:key/00000000-0000-0000-0000-000000000000"])
    error_message = "decrypt is scoped to the platform key only"
  }
}

run "rejects_app_url_with_path" {
  command = plan
  variables {
    app_url = "https://app.example.com/"
  }
  expect_failures = [var.app_url]
}

run "rejects_bad_name_prefix" {
  command = plan
  variables {
    name_prefix = "Bad_Prefix"
  }
  expect_failures = [var.name_prefix]
}
