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
}

variables {
  name_prefix = "stac-test"
  kms_key_arn = "arn:aws-us-gov:kms:us-gov-west-1:000000000000:key/00000000-0000-0000-0000-000000000000"
}

run "ten_entries_with_stable_keys" {
  command = apply
  assert {
    condition = toset(keys(output.secret_arns)) == toset([
      "db_stac_higher_owner", "db_stac_api", "db_stac_reader", "db_stac_proxy_policy", "db_cube_server",
      "session_secret", "catalog_bff_shared_secret", "internal_api_token", "credentials_master_key", "proxy_auth_token",
    ])
    error_message = "secret_arns must carry exactly the ten contract keys"
  }
  assert {
    condition     = length(aws_secretsmanager_secret.this) == 10
    error_message = "exactly ten secrets"
  }
}

run "names_under_prefix" {
  command = apply
  assert {
    condition = output.secret_names == {
      db_stac_higher_owner      = "stac-test/db/stac_higher_owner"
      db_stac_api               = "stac-test/db/stac_api"
      db_stac_reader            = "stac-test/db/stac_reader"
      db_stac_proxy_policy      = "stac-test/db/stac_proxy_policy"
      db_cube_server            = "stac-test/db/cube_server"
      session_secret            = "stac-test/platform/session_secret"
      catalog_bff_shared_secret = "stac-test/platform/catalog_bff_shared_secret"
      internal_api_token        = "stac-test/platform/internal_api_token"
      credentials_master_key    = "stac-test/platform/credentials_master_key"
      proxy_auth_token          = "stac-test/platform/proxy_auth_token"
    }
    error_message = "secret names must be <name_prefix>/<suffix> exactly"
  }
}

run "encrypted_with_cmk_and_recovery_window" {
  command = apply
  assert {
    condition     = alltrue([for s in aws_secretsmanager_secret.this : s.kms_key_id == var.kms_key_arn])
    error_message = "every secret must use the supplied CMK"
  }
  assert {
    condition     = alltrue([for s in aws_secretsmanager_secret.this : s.recovery_window_in_days == 7])
    error_message = "default recovery window is 7 days"
  }
}

run "db_entries_are_json_with_role_username" {
  command = apply
  assert {
    condition = alltrue([
      for k, role in {
        db_stac_higher_owner = "stac_higher_owner"
        db_stac_api          = "stac_api"
        db_stac_reader       = "stac_reader"
        db_stac_proxy_policy = "stac_proxy_policy"
        db_cube_server       = "cube_server"
      } :
      jsondecode(aws_secretsmanager_secret_version.this[k].secret_string).username == role
    ])
    error_message = "each db secret must carry its login role as username"
  }
  assert {
    condition = alltrue([
      for k in ["db_stac_higher_owner", "db_stac_api", "db_stac_reader", "db_stac_proxy_policy", "db_cube_server"] :
      can(regex("^[A-Za-z0-9]{48}$", jsondecode(aws_secretsmanager_secret_version.this[k].secret_string).password))
    ])
    error_message = "each db password must be 48 alphanumeric characters"
  }
}

run "tokens_are_64_alnum" {
  command = apply
  assert {
    condition = alltrue([
      for k in ["session_secret", "catalog_bff_shared_secret", "internal_api_token", "proxy_auth_token"] :
      can(regex("^[A-Za-z0-9]{64}$", aws_secretsmanager_secret_version.this[k].secret_string))
    ])
    error_message = "each token must be 64 alphanumeric characters"
  }
}

run "master_key_is_std_base64_of_32_bytes" {
  command = apply
  assert {
    condition     = can(regex("^[A-Za-z0-9+/]{43}=$", aws_secretsmanager_secret_version.this["credentials_master_key"].secret_string))
    error_message = "credentials_master_key must be standard base64 of 32 bytes (44 chars)"
  }
}

run "db_role_names_in_spec_order" {
  command = apply
  assert {
    condition     = output.db_role_names == ["stac_higher_owner", "stac_api", "stac_reader", "stac_proxy_policy", "cube_server"]
    error_message = "db_role_names must follow the spec 7.2 order"
  }
}

run "description_names_env_var" {
  command = apply
  assert {
    condition     = strcontains(aws_secretsmanager_secret.this["session_secret"].description, "SESSION_SECRET")
    error_message = "description must name the env var"
  }
}

# OpenTofu cannot reference run.<name>.<output> in assertions, so this plans over
# the state the earlier apply runs left behind. A keepers/timestamp() value would
# be replaced (unknown) in this plan and the assertions would error.
run "values_stable_across_applies" {
  command = plan
  assert {
    condition = alltrue([
      for k in ["session_secret", "catalog_bff_shared_secret", "internal_api_token", "proxy_auth_token"] :
      can(regex("^[A-Za-z0-9]{64}$", aws_secretsmanager_secret_version.this[k].secret_string))
    ])
    error_message = "planning over applied state must keep the generated values (no churn)"
  }
  assert {
    condition     = can(regex("^[A-Za-z0-9+/]{43}=$", aws_secretsmanager_secret_version.this["credentials_master_key"].secret_string))
    error_message = "planning over applied state must keep the master key (no churn)"
  }
}

run "rejects_recovery_window_3" {
  command = plan
  variables {
    recovery_window_in_days = 3
  }
  expect_failures = [var.recovery_window_in_days]
}

run "rejects_kms_key_id" {
  command = plan
  variables {
    kms_key_arn = "00000000-0000-0000-0000-000000000000"
  }
  expect_failures = [var.kms_key_arn]
}
