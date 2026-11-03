# R-4 · Terraform base modules (kms, storage, secrets, registry) + CI validation + example skeleton — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Tracking:** GitHub #140 (epic #136).

**Goal:** Start `infra/terraform/modules/` with the four modules every later Terraform slice builds on (`kms`, `storage`, `secrets`, `registry`), a placeholder root module at `infra/examples/aws-eks/`, and a CI workflow that validates all of it with both OpenTofu and Terraform, without an AWS account.

**Architecture:** Each module is a provider-less library module: it declares `required_providers` and reads the account, partition and region from data sources. Every ARN, service principal and hostname is built from those data sources. IAM and KMS policies are `jsonencode()`d in `locals`, so `tofu test` / `terraform test` with a **mocked AWS provider** can decode and assert them. The tests run under a mocked `aws-us-gov` partition with a sentinel DNS suffix, which proves the GovCloud rule by test, not by review. The example root wires the four modules together with placeholder values. It is validated and tested under mocks, never applied. R-5 replaces its four direct calls with the `stac-higher` composition module.

**Tech Stack:** OpenTofu ≥ 1.10 / Terraform ≥ 1.10 (native `.tftest.hcl` tests with `mock_provider`), `hashicorp/aws ~> 6.0`, `hashicorp/random ~> 3.6`, tflint with `tflint-ruleset-aws`, trivy (`config`), GitHub Actions, bash.

**Spec:** `docs/superpowers/specs/2026-11-02-deployment-artifacts-design.md` §4, §6.4, §7, §7.1, §7.2, §9, §10, §12. Decision record: ADR 0024. Bucket versioning: ADR 0011.

## Global Constraints

- **Layout:** `infra/terraform/modules/{kms,storage,secrets,registry}/`. Each module has `versions.tf`, `variables.tf`, `main.tf`, `outputs.tf`, `README.md` and `tests/<module>.tftest.hcl`. Also: `infra/terraform/README.md`, `infra/terraform/.tflint.hcl`, and `infra/examples/aws-eks/` (same file set, plus its own test). R-4 builds no `stac-higher` composition module; that is R-5's.
- **Versions:** each `versions.tf` sets `terraform { required_version = ">= 1.10" }`, with `required_providers { aws = { source = "hashicorp/aws", version = "~> 6.0" } }`. Only `secrets` adds `random = { source = "hashicorp/random", version = "~> 3.6" }` (`random_bytes` needs ≥ 3.6).
- **No provider, backend or account in modules.** Modules never contain a `provider` or `backend` block. The example declares `provider "aws" { region = var.region }` and nothing else: no backend, no credentials, no `assume_role`.
- **Partition-aware:**
  - Partition: `data.aws_partition.current.partition`.
  - Service principals and hostnames: `data.aws_partition.current.dns_suffix`.
  - Region: `data.aws_region.current.region`. In provider 6.x, `.name` is deprecated; do not use it.
  - Account: `data.aws_caller_identity.current.account_id`.
  - The literal strings `arn:aws:` and `amazonaws.com` never appear in any `*.tf`, `*.tfvars` or `*.hcl` file under `infra/terraform/` or `infra/examples/`, including the tests. `scripts/check-partition-arns.sh` fails CI on either. Markdown is not scanned, so READMEs may name the rule.
- **Policies** are `jsonencode()` objects in `locals`, never `data "aws_iam_policy_document"`. Under a mock provider that data source returns a random string, so its JSON cannot be asserted.
- **Common inputs.** Every module takes these two variables, with this exact validation:
  - `name_prefix` (string):
    ```hcl
    validation {
      condition     = can(regex("^[a-z][a-z0-9-]{0,30}[a-z0-9]$", var.name_prefix))
      error_message = "name_prefix must be 2-32 characters of lowercase letters, digits and hyphens, starting with a letter and not ending in a hyphen (it becomes part of bucket, IAM role, KMS alias and secret names)."
    }
    ```
  - `tags` (map(string), default `{}`), set as `tags = var.tags` on every taggable resource. For ECR templates it goes in `resource_tags`.
- **KMS key ARN inputs** (`storage`, `secrets`, `registry`: `var.kms_key_arn`) are validated with `can(regex("^arn:[a-z-]+:kms:[a-z0-9-]+:[0-9]{12}:key/.+$", var.kms_key_arn))`. ECR templates need the full key ARN, so a key id or alias is refused at plan.
- **Every variable and output has a `description`.** tflint runs `preset = "all"`.
- **Test harness:** every `.tftest.hcl` starts with this mock block (repeat it per file; OpenTofu does not share mock files across modules). Assertions compare against these exact strings:
  ```hcl
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
  ```
  - Use `variables { name_prefix = "stac-test" }`, and `kms_key_arn = "arn:aws-us-gov:kms:us-gov-west-1:000000000000:key/00000000-0000-0000-0000-000000000000"` where the module takes one.
  - The mock provider **still runs the AWS provider's argument validation**, so every ARN-typed test input must be a well-formed ARN. When a mocked computed ARN flows into another resource's ARN argument under `command = apply`, give that resource a `mock_resource` default with a gov-partition ARN.
  - `random` is not mocked; it runs for real under `command = apply`.
  - Prefer `command = plan`. Use `apply` only when an assertion needs a generated value.
- **Placeholders only:** `000000000000`, `example.com` and `us-east-1` in the example; `us-gov-west-1` and `dns-suffix.example` in tests only. Never commit an account id, ARN, hostname, CIDR or secret value (ADR 0024).
- **trivy:**
  - The gate is HIGH and CRITICAL.
  - A justified suppression goes on the line directly above the resource, as `#trivy:ignore:AWS-<NNNN> -- <reason>`. trivy ≥ 0.69 IDs are `AWS-NNNN`; CI's trivy-action v0.36.0 runs trivy v0.70.0.
  - The existing `security.yml` `misconfig` job (trivy over `.`) will also scan these trees on every PR, so the two must agree.
- **Never committed:** `.terraform/`, `.terraform.lock.hcl`, `*.tfstate*`, `*.tfplan`, `*.tfvars`. The modules are libraries and the example is never applied; the deployment repo commits its own lock file.
- **Local verification:** `tofu`, `terraform` and `trivy` are installed (Homebrew); `tflint` may not be. If a tool is missing when you run a step, write "CI validates" for that step in your report instead of skipping silently.
- **Gates:**
  - Before declaring the slice done: `npm run verify` from the worktree root (run `npm install` first if `node_modules/` is missing).
  - Every IaC command in Task 5's final step.
  - No pipeline change, so no pytest/ruff. No Docker, no dev server, no push, no AWS account.
- **Commits:** message `R-4: <what>`, ending with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Docs carry no status** (AGENTS.md link rule). The infra README links #140's epic once in its header.

## Review Focus

1. **A CORS origin with a trailing slash, a path or a wildcard** (`https://app.example.com/`, `*`). S3 compares the `Origin` header exactly and browsers send no trailing slash, so presigned PUTs would fail silently in the browser. The `storage` validation refuses anything except `scheme://host[:port]`. Tests are in Task 2.
2. **An upstream credential secret outside `ecr-pullthroughcache/`, or a CMK-encrypted one.** ECR requires the prefix and supports only the default `aws/secretsmanager` key. The `registry` validation refuses a wrong prefix at plan. The key requirement cannot be checked from an ARN, so it goes in the module README and the infra README. Tests are in Task 4.
3. **Docker Hub enabled without credentials.** ECR's Docker Hub upstream requires them, and a bare enable would fail only at apply. A precondition fails the plan instead (spiked: `expect_failures` on a `for_each` resource works in both engines). Test is in Task 4.
4. **A KMS key id or alias passed where the ARN is expected.** S3 accepts it, but the ECR creation template does not. The shared `kms_key_arn` validation fails the plan in all three consuming modules. Tests are in Tasks 2, 3 and 4.
5. **An invalid `name_prefix`** (uppercase, underscore, over 32 characters). It would surface as an S3 or IAM naming error at apply, after other resources exist. The shared validation fails the plan. Tests are in Tasks 1 and 2.

---

## File Structure

| Path | Responsibility |
|---|---|
| `.github/workflows/infra.yml` | New workflow: fmt, partition check, tflint, validate + test (two engines × floor and current), trivy |
| `scripts/check-partition-arns.sh` | Fails on literal `arn:aws:` / `amazonaws.com` in Terraform files of the two trees |
| `scripts/check-partition-arns.test.sh` | Self-test proving the checker fails on a planted literal |
| `scripts/check-terraform.sh` | `init -backend=false` + `validate` + `test` for every module and example, for one engine |
| `infra/terraform/.tflint.hcl` | tflint config: `terraform` preset `all`, AWS ruleset, local module calls |
| `.gitignore` | Terraform state, lock and plugin dirs, tfvars |
| `infra/terraform/modules/kms/` | One CMK, rotation on, alias, partition-aware key policy |
| `infra/terraform/modules/storage/` | One bucket per module instance: SSE-KMS, public access blocked, versioning disabled, TLS-only, CORS, abort-MPU lifecycle |
| `infra/terraform/modules/secrets/` | Ten generated Secrets Manager entries (five DB login roles, five platform secrets) |
| `infra/terraform/modules/registry/` | ECR pull-through cache rules (ghcr required; Docker Hub, Quay optional), creation templates, their IAM role, the mirror-prefix outputs |
| `infra/examples/aws-eks/` | Placeholder root wiring the four modules; validated + mock-tested, never applied |
| `infra/terraform/README.md` | Module index, the no-account-details rule, consuming by git ref, prerequisites, local validation |
| `docs/FEATURES.md`, `docs/README.md`, `docs/backend.md`, `docs/ISSUES.md` | Index entries and one accepted limitation |

**Rulings this plan makes (where the spec, the issue and the docs were silent or disagreed):**

- **`storage` is one bucket per module instance, not a map.** The only two buckets the spec names have different policies. The platform bucket needs CORS. The `demo-destination` bucket (R-5) needs an IAM-user write policy and no CORS. A map would add `for_each` plumbing for one consumer, while `demo-destination` can call `storage` a second time with `cors_allowed_origins = []`.
- **`kms` key policy.** The lead listed "service principals (s3, secretsmanager, ecr, rds, logs)". S3, Secrets Manager, ECR and RDS use a key with the *caller's* credentials, never as a service principal. So the policy expresses those four as `kms:ViaService` conditions (the AWS-managed-key pattern, scoped by `kms:CallerAccount`). Only CloudWatch Logs gets a service principal, `logs.<region>.<dns_suffix>`, with the logs encryption-context condition.
- **`CREDENTIALS_MASTER_KEY` is `random_bytes` (32 bytes, `.base64`), not `random_password`.** The app and the pipeline require standard base64 of exactly 32 bytes (`docs/connections.md`; the pipeline uses `b64decode(validate=True)`). A password string cannot meet that.
- **DB entries store JSON `{"username","password"}`; platform entries store the bare value.** The JSON shape mirrors the RDS-managed master secret the bootstrap Job also reads, so the Job reads all roles one way. Generated values are alphanumeric (`special = false`), so they need no URL-encoding inside DSNs or HTTP headers.
- **Generated values live in Terraform state.** Write-only arguments (`secret_string_wo`) need engine ≥ 1.11, above the 1.10 floor. This is recorded as an ISSUES entry.
- **Scanner Docker Hub credentials** (`REGISTRY_DOCKERHUB_USER`/`_TOKEN`, spec §6.4) **and the ECR upstream credential secrets are not generated here.** They are real third-party tokens that the deployment supplies. The upstream secrets are module *inputs* (by ARN).
- **`PROXY_AUTH_TOKEN` is generated, but wiring it is R-6's call.** Nothing in the browser sends `X-Proxy-Auth` today (`app/src/pages/api/proxy.ts`), so setting the env var unconditionally would 401 the UI's external-catalog proxy. The README says so.
- **The registry module creates no account-wide ECR scanning configuration.** "Scan on push" in §7.1 is a registry-level singleton that other workloads in the account share. Platform images are scanned by the platform's own scanner (ADR 0021). The README says so.
- **The registry module creates an IAM role for the creation templates.** ECR requires a custom role when a template uses KMS ("This role must be provided when using repository tags and/or KMS in the template, otherwise the repository creation will fail"). Per the ECR guide, the role's policy is `ecr:CreateRepository`, `ecr:ReplicateImage`, `ecr:TagResource` and `kms:CreateGrant`, `kms:RetireGrant`, `kms:DescribeKey`, scoped here to the cached prefixes and the key.
- **`mirror_prefixes` is keyed by the host as it appears in image references** (`ghcr.io`, `docker.io`, `quay.io`). R-8's executor rewrites image references, and Docker Hub images say `docker.io`, not the ECR rule's upstream URL `registry-1.docker.io`.
- **CI is a new `infra.yml` workflow, not a job in `ci.yml`.**
  - `ci.yml` has no path gating at all (every job runs on every PR). A workflow-level `paths:` filter is the native mechanism and needs no new action.
  - `main` has no branch protection or rulesets, so a skipped workflow cannot block a merge.
  - Existing workflows pin actions by tag (`actions/checkout@v7`, `aquasecurity/trivy-action@v0.36.0`), not by SHA; this follows them.
- **CI validates at the version floor and at a current release** for both engines. `>= 1.10` is a claim, and only the floor run tests it.
- **A second literal is enforced beside `arn:aws:`.** `amazonaws.com` is also refused (spec §9: "every service principal and endpoint suffix from … dns_suffix"). The test sentinel `dns-suffix.example` is what makes that assertable.

---

### Task 1: Validation harness + `kms` module

**Files:**
- Create: `scripts/check-partition-arns.sh`, `scripts/check-partition-arns.test.sh`, `scripts/check-terraform.sh` (all `chmod +x`)
- Create: `infra/terraform/.tflint.hcl`
- Modify: `.gitignore` (append a Terraform section)
- Create: `infra/terraform/modules/kms/{versions.tf,variables.tf,main.tf,outputs.tf,README.md}`
- Test: `infra/terraform/modules/kms/tests/kms.tftest.hcl`
- Create: `.github/workflows/infra.yml`

**Interfaces:**
- Produces, `scripts/check-partition-arns.sh [dir...]`:
  - With no arguments, it scans whichever of `infra/terraform` and `infra/examples` exist. A directory given explicitly that does not exist exits 2.
  - It scans `*.tf`, `*.tfvars` and `*.hcl` (including `*.tftest.hcl`), skipping `.terraform/`.
  - It prints `file:line: <match>` for each literal `arn:aws:` or `amazonaws.com`; exit 1 if any hit, 0 if none.
- Produces, `scripts/check-terraform.sh <tofu|terraform>`:
  - An unknown engine exits 2.
  - For each directory matching `infra/terraform/modules/*/` and `infra/examples/*/` (nullglob, sorted), it prints `== <dir>`, then runs `<engine> -chdir=<dir> init -backend=false -input=false`, `<engine> -chdir=<dir> validate`, and, if `<dir>/tests/` exists, `<engine> -chdir=<dir> test`.
  - `set -euo pipefail`; it stops at the first failure.
- Produces, `kms` module:
  - Inputs:
    - `name_prefix`, `tags` (Global Constraints).
    - `deletion_window_in_days` (number, default `30`, validation `>= 7 && <= 30`).
  - Outputs: `key_arn`, `key_id`, `alias_name`, `alias_arn` (all strings).

- [ ] **Step 1: Write the partition-check self-test**

`scripts/check-partition-arns.test.sh` (bash, `set -euo pipefail`) creates three temp dirs with `mktemp -d`:
- A clean dir containing `ok.tf` with `"arn:${data.aws_partition.current.partition}:iam::000000000000:root"`. The checker must exit 0.
- A dir containing `bad.tf` whose literal is assembled at runtime (`printf 'x = "arn:%s:iam::000000000000:root"\n' aws`), so this script never contains the literal itself. The checker must exit 1, and its output must contain `bad.tf:1`.
- A dir containing `bad.tftest.hcl` with `printf 'x = "s3.%s.com"\n' amazonaws`. The checker must exit 1.

On each wrong exit, print `FAIL: <case>` and exit 1. On full success, print `partition check self-test: 3/3 passed`.

- [ ] **Step 2: Run it to verify it fails**

Run: `scripts/check-partition-arns.test.sh`
Expected: FAIL (the checker does not exist yet: `No such file or directory`).

- [ ] **Step 3: Implement `scripts/check-partition-arns.sh`** to the interface above. Use `grep -rnE --include='*.tf' --include='*.tfvars' --include='*.hcl' --exclude-dir=.terraform -e 'arn:aws:' -e 'amazonaws\.com'`. grep's exit 1 (no match) means success; its exit ≥ 2 is an error.

- [ ] **Step 4: Run the self-test and the checker**

Run: `scripts/check-partition-arns.test.sh && scripts/check-partition-arns.sh; echo "exit=$?"`
Expected: `partition check self-test: 3/3 passed`, then `exit=0`. Neither tree exists yet, so the checker scans nothing.

- [ ] **Step 5: Add the harness files**
  - `scripts/check-terraform.sh` per the interface.
  - `.gitignore`: append the section `# Terraform / OpenTofu (infra/terraform, infra/examples) — never committed` with the lines `.terraform/`, `.terraform.lock.hcl`, `*.tfstate`, `*.tfstate.*`, `*.tfplan`, `*.tfvars`.
  - `infra/terraform/.tflint.hcl`: `config { call_module_type = "local" }`, `plugin "terraform" { enabled = true, preset = "all" }`, and `plugin "aws" { enabled = true, version = "0.50.0", source = "github.com/terraform-linters/tflint-ruleset-aws" }`. If tflint v0.65.0 rejects that ruleset, use the pair the ruleset's release notes name and record the change in the report.

- [ ] **Step 6: Write the failing `kms` tests** in `infra/terraform/modules/kms/tests/kms.tftest.hcl`. Use the Global Constraints mock block and `variables { name_prefix = "stac-test" }`. All runs are `command = plan`.
  - The key policy statements are fixed in this order: `[0]` `AccountRootAdministers`, `[1]` `UseThroughPlatformServices`, `[2]` `CloudWatchLogsUse`.
  - Let `p = jsondecode(aws_kms_key.this.policy)` in each assertion.

```hcl
run "key_rotation_on" {
  command = plan
  assert {
    condition     = aws_kms_key.this.enable_key_rotation == true && aws_kms_key.this.deletion_window_in_days == 30
    error_message = "rotation must be on, default deletion window 30 days"
  }
}
run "alias_from_prefix"      { # aws_kms_alias.this.name == "alias/stac-test"; output.alias_name == "alias/stac-test"
}
run "root_administers_partition_aware" {
  # p.Statement[0].Sid == "AccountRootAdministers", .Effect == "Allow", .Action == "kms:*",
  # .Principal.AWS == "arn:aws-us-gov:iam::000000000000:root"
}
run "platform_services_via_service" {
  # p.Statement[1].Principal.AWS == "*"
  # toset(p.Statement[1].Condition.StringEquals["kms:ViaService"]) == toset([
  #   "s3.us-gov-west-1.dns-suffix.example", "secretsmanager.us-gov-west-1.dns-suffix.example",
  #   "ecr.us-gov-west-1.dns-suffix.example", "rds.us-gov-west-1.dns-suffix.example"])
  # p.Statement[1].Condition.StringEquals["kms:CallerAccount"] == "000000000000"
  # toset(p.Statement[1].Action) == toset(["kms:Encrypt","kms:Decrypt","kms:ReEncrypt*","kms:GenerateDataKey*","kms:DescribeKey","kms:CreateGrant"])
}
run "cloudwatch_logs_principal" {
  # p.Statement[2].Principal.Service == "logs.us-gov-west-1.dns-suffix.example"
  # p.Statement[2].Condition.ArnLike["kms:EncryptionContext:aws:logs:arn"] == "arn:aws-us-gov:logs:us-gov-west-1:000000000000:*"
}
run "tags_applied" {
  # variables { tags = { project = "stac-higher" } }; aws_kms_key.this.tags == { project = "stac-higher" }
}
run "rejects_bad_name_prefix" {
  command         = plan
  variables { name_prefix = "Stac_Test" }
  expect_failures = [var.name_prefix]
}
run "rejects_short_deletion_window" {
  command         = plan
  variables { deletion_window_in_days = 6 }
  expect_failures = [var.deletion_window_in_days]
}
```

(The commented runs are the same shape as `key_rotation_on`, one `assert` per bullet.)

- [ ] **Step 7: Run to verify they fail**

Run: `scripts/check-terraform.sh tofu`
Expected: FAIL in `== infra/terraform/modules/kms/`, because the configuration does not exist yet (the first error names `aws_kms_key.this`).

- [ ] **Step 8: Implement the `kms` module**
  - `versions.tf` per Global Constraints.
  - Data sources: `aws_partition.current`, `aws_caller_identity.current`, `aws_region.current`.
  - `aws_kms_key.this`:
    - `description = "stac-higher platform key (${var.name_prefix}): S3, Secrets Manager, ECR, RDS, CloudWatch Logs"`, `enable_key_rotation = true`, `deletion_window_in_days = var.deletion_window_in_days`, `policy = local.key_policy`, `tags = var.tags`.
    - `local.key_policy` is a `jsonencode` of the three statements exactly as the tests pin them. Every statement has `Resource = "*"`. `CloudWatchLogsUse` grants `kms:Encrypt*`, `kms:Decrypt*`, `kms:ReEncrypt*`, `kms:GenerateDataKey*`, `kms:Describe*`.
  - `aws_kms_alias.this` with `name = "alias/${var.name_prefix}"`.
  - `README.md`: purpose, inputs and outputs tables, the key-policy table with one line on why the four services are `ViaService` conditions (see Rulings), and "one key for every platform service, by lead decision 2026-11-03".

- [ ] **Step 9: Run everything for the module**

Run, from the worktree root:
```bash
scripts/check-terraform.sh tofu && scripts/check-terraform.sh terraform
tofu fmt -check -recursive -diff infra/terraform
scripts/check-partition-arns.sh
tflint --init --config "$(pwd)/infra/terraform/.tflint.hcl" && (cd infra && tflint --recursive --config "$(pwd)/terraform/.tflint.hcl")
trivy config --exit-code 1 --severity HIGH,CRITICAL infra/terraform
```
Expected:
- Both engines print `Success! The configuration is valid.` and `8 passed, 0 failed`.
- fmt prints nothing.
- The checker exits 0.
- tflint reports no issues.
- trivy reports `0` misconfigurations at HIGH/CRITICAL.

- [ ] **Step 10: Write `.github/workflows/infra.yml`.** Follow `ci.yml`'s style (job `name:`s with `·`, `timeout-minutes`, `permissions: contents: read`, a PR-only `cancel-in-progress` concurrency group `infra-${{ github.ref }}`).
  - **Triggers:** `push` to `main` and `pull_request`, both with `paths: [infra/terraform/**, infra/examples/**, scripts/check-partition-arns.sh, scripts/check-partition-arns.test.sh, scripts/check-terraform.sh, .github/workflows/infra.yml]`, plus `workflow_dispatch`.
  - **Job `lint`** (`Terraform · fmt, partition check, tflint`, 10 min):
    1. `actions/checkout@v7`.
    2. `opentofu/setup-opentofu@v2.0.2` with `tofu_version: 1.13.2` and `tofu_wrapper: false`.
    3. `tofu fmt -check -recursive -diff infra/terraform`, then the same for `infra/examples`.
    4. `scripts/check-partition-arns.test.sh`, then `scripts/check-partition-arns.sh`.
    5. `terraform-linters/setup-tflint@v6.3.2` with `tflint_version: v0.65.0`.
    6. `tflint --init --config "$GITHUB_WORKSPACE/infra/terraform/.tflint.hcl"` with `env: GITHUB_TOKEN: ${{ github.token }}`.
    7. `tflint --recursive --config "$GITHUB_WORKSPACE/infra/terraform/.tflint.hcl" --format compact` with `working-directory: infra`.
  - **Job `validate`** (`Terraform · validate + test (${{ matrix.engine }} ${{ matrix.version }})`, 15 min):
    - `strategy.fail-fast: false`, with `matrix.include` = `{engine: tofu, version: 1.10.10}`, `{engine: tofu, version: 1.13.2}`, `{engine: terraform, version: 1.10.5}`, `{engine: terraform, version: 1.17.1}`.
    - Job `env`: `TF_PLUGIN_CACHE_DIR: ${{ runner.temp }}/tf-plugin-cache`.
    - Steps:
      1. `actions/checkout@v7`.
      2. `opentofu/setup-opentofu@v2.0.2` (`if: matrix.engine == 'tofu'`, `tofu_version: ${{ matrix.version }}`, `tofu_wrapper: false`).
      3. `hashicorp/setup-terraform@v4.0.1` (`if: matrix.engine == 'terraform'`, `terraform_version: ${{ matrix.version }}`, `terraform_wrapper: false`).
      4. `mkdir -p "$TF_PLUGIN_CACHE_DIR"`.
      5. `scripts/check-terraform.sh "${{ matrix.engine }}"`.
    - Comment above the matrix: the floor legs prove `required_version = ">= 1.10"`; the current legs catch deprecations.
  - **Job `trivy`** (`Terraform · trivy config`, 10 min):
    1. `actions/checkout@v7`.
    2. `aquasecurity/trivy-action@v0.36.0` with `scan-type: config`, `scan-ref: infra/terraform`, `severity: HIGH,CRITICAL`, `exit-code: "1"`.
    3. The same action again with `scan-ref: infra/examples` and `skip-setup-trivy: true`.
    - Comment: `security.yml`'s `misconfig` job also scans `.`; this job keeps the infra gate self-contained on path-filtered PRs.
  - Run `actionlint .github/workflows/infra.yml` if `actionlint` is installed locally. Expected: no output. Otherwise write "CI validates (ci.yml actionlint job)" in the report; do not run it through Docker.

- [ ] **Step 11: Commit**

```bash
git add .gitignore .github/workflows/infra.yml scripts/check-partition-arns.sh scripts/check-partition-arns.test.sh scripts/check-terraform.sh infra/terraform/.tflint.hcl infra/terraform/modules/kms
git commit -m "R-4: Terraform validation harness + kms module"
```

---

### Task 2: `storage` module (one bucket per instance)

**Files:**
- Create: `infra/terraform/modules/storage/{versions.tf,variables.tf,main.tf,outputs.tf,README.md}`
- Test: `infra/terraform/modules/storage/tests/storage.tftest.hcl`

**Interfaces:**
- Consumes: the `kms` module's `key_arn` (string), passed in by the caller as `kms_key_arn`.
- Produces, `storage` module:
  - Inputs:
    - `name_prefix`, `tags`.
    - `name` (string, default `"platform"`, validation `^[a-z0-9][a-z0-9-]{0,28}[a-z0-9]$`). The bucket is `"${var.name_prefix}-${var.name}"`, at most 63 characters by construction.
    - `kms_key_arn` (string, validated).
    - `cors_allowed_origins` (list(string), default `[]`). Validation: `alltrue([for o in var.cors_allowed_origins : can(regex("^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$", o))])`. The error message says "scheme://host[:port] only — no path, no trailing slash, no wildcard".
    - `force_destroy` (bool, default `false`).
  - Outputs:
    - `bucket_name` (= `aws_s3_bucket.this.id`).
    - `bucket_arn` (= `aws_s3_bucket.this.arn`).
    - `bucket_regional_domain_name`.
    - `kms_key_arn` (pass-through).

- [ ] **Step 1: Write the failing tests** in `infra/terraform/modules/storage/tests/storage.tftest.hcl`: the mock block, plus `variables { name_prefix = "stac-test", kms_key_arn = <gov key ARN> }`, all `command = plan`. Read nested blocks with `one(...)` (or `tolist(...)[0]`).
  - `bucket_name_from_prefix`: `aws_s3_bucket.this.bucket == "stac-test-platform"` and `force_destroy == false`.
  - `encrypts_with_cmk`: the default rule's `sse_algorithm == "aws:kms"`, `kms_master_key_id == var.kms_key_arn`, and `bucket_key_enabled == true`.
  - `public_access_blocked`: all four `aws_s3_bucket_public_access_block.this` flags are `true`.
  - `acls_disabled`: `aws_s3_bucket_ownership_controls.this` rule `object_ownership == "BucketOwnerEnforced"`.
  - `versioning_disabled`: `aws_s3_bucket_versioning.this` status is `"Disabled"`.
  - `tls_only_policy`. With `s = jsondecode(aws_s3_bucket_policy.this.policy).Statement[0]`:
    - `s.Effect == "Deny"`, `s.Principal == "*"`, `s.Action == "s3:*"`.
    - `toset(s.Resource) == toset(["arn:aws-us-gov:s3:::stac-test-platform", "arn:aws-us-gov:s3:::stac-test-platform/*"])`.
    - `s.Condition.Bool["aws:SecureTransport"] == "false"`.
  - `aborts_multipart_after_7_days`: one lifecycle rule, `status == "Enabled"`, `abort_incomplete_multipart_upload` `days_after_initiation == 7`, and no `expiration` block.
  - `no_cors_by_default`: `length(aws_s3_bucket_cors_configuration.this) == 0`.
  - `cors_from_origins` (`cors_allowed_origins = ["https://app.example.com", "http://localhost:4321"]`). The single rule has:
    - `allowed_origins` equal to those two.
    - `toset(allowed_methods) == toset(["GET", "HEAD", "PUT"])`.
    - `allowed_headers == ["*"]`, `expose_headers == ["ETag"]`, `max_age_seconds == 3000`.
  - `rejects_trailing_slash_origin` (`["https://app.example.com/"]`) and `rejects_wildcard_origin` (`["*"]`): `expect_failures = [var.cors_allowed_origins]`.
  - `rejects_kms_key_id` (`kms_key_arn = "00000000-0000-0000-0000-000000000000"`): `expect_failures = [var.kms_key_arn]`.
  - `rejects_bad_name_prefix` (`"stac_test"`): `expect_failures = [var.name_prefix]`.

- [ ] **Step 2: Run to verify they fail**

Run: `scripts/check-terraform.sh tofu`
Expected: `kms` passes, then FAIL in `== infra/terraform/modules/storage/` (resources not declared).

- [ ] **Step 3: Implement the `storage` module.** Data source: `aws_partition.current` only. Locals: `bucket_name` and `bucket_arn = "arn:${data.aws_partition.current.partition}:s3:::${local.bucket_name}"`; the policy uses the local ARN. Resources (all on `aws_s3_bucket.this.id`):
  - `aws_s3_bucket.this`: `bucket`, `force_destroy`, `tags`.
  - `aws_s3_bucket_ownership_controls.this`: `BucketOwnerEnforced`.
  - `aws_s3_bucket_public_access_block.this`: all four flags `true`.
  - `aws_s3_bucket_server_side_encryption_configuration.this`: `aws:kms`, `var.kms_key_arn`, `bucket_key_enabled = true`.
  - `aws_s3_bucket_versioning.this`: `status = "Disabled"`, preceded by `#trivy:ignore:AWS-0090 -- versioning off by design: ADR 0011, deleted bytes must go`. Put a comment block above it: under ADR 0011 the `asset_gc` queue is the only byte-deletion path and must actually remove bytes. Versioning would keep a noncurrent copy of every collected object, so GC, retention and archive would free nothing and "deleted" data would stay readable. This is spec §7.1 and §14.
  - `aws_s3_bucket_policy.this`: the TLS-deny statement, with `depends_on = [aws_s3_bucket_public_access_block.this]`.
  - `aws_s3_bucket_lifecycle_configuration.this`: rule `id = "abort-incomplete-multipart-uploads"`, `filter {}`, abort after 7 days. Comment: no expiration rules. Object deletion belongs to `asset_gc` and the pipeline's staging sweep (ADR 0011), never to S3 lifecycle.
  - `aws_s3_bucket_cors_configuration.this` with `count = length(var.cors_allowed_origins) > 0 ? 1 : 0`.

  No access logging (trivy `AWS-0089` is LOW, below the gate; a deployment that wants logs supplies a log bucket). The README says so.

  `README.md`: inputs and outputs tables. Note that one instance is one bucket, and that R-5's `demo-destination` is expected to instantiate it again (see Rulings). Explain the CORS origin rule, versioning off (ADR 0011), and no lifecycle expiry.

- [ ] **Step 4: Run to verify they pass**

Run: the Task 1 Step 9 block, with `trivy config` also over `infra/terraform` (unchanged command).
Expected:
- Both engines: storage `13 passed, 0 failed`; kms is still green.
- fmt, the checker and tflint are clean.
- trivy: 0 HIGH/CRITICAL. With `--severity LOW,MEDIUM,HIGH,CRITICAL` it logs `Ignore finding rule="aws-s3-enable-versioning"` and shows only `AWS-0089 (LOW)`.

- [ ] **Step 5: Commit**

```bash
git add infra/terraform/modules/storage
git commit -m "R-4: storage module — SSE-KMS, public access blocked, versioning off (ADR 0011), TLS-only, CORS"
```

---

### Task 3: `secrets` module (generated platform secrets)

**Files:**
- Create: `infra/terraform/modules/secrets/{versions.tf,variables.tf,main.tf,outputs.tf,README.md}`
- Test: `infra/terraform/modules/secrets/tests/secrets.tftest.hcl`

**Interfaces:**
- Consumes: `kms_key_arn` (the `kms` module's `key_arn`).
- Produces, `secrets` module:
  - Inputs:
    - `name_prefix`, `tags`.
    - `kms_key_arn` (validated).
    - `recovery_window_in_days` (number, default `7`, validation `== 0 || (>= 7 && <= 30)`).
  - Outputs (the chart's `secrets.mode: external-secrets` reads these keys in R-6, so **the ten keys are a contract and never change**):
    - `secret_arns` (map(string), key → ARN).
    - `secret_names` (map(string), key → Secrets Manager name).
    - `db_role_names` (list(string), exactly `["stac_higher_owner", "stac_api", "stac_reader", "stac_proxy_policy", "cube_server"]`, the spec §7.2 order).

The entry table (`locals`; the name is `"${var.name_prefix}/" + suffix`):

| Key | Name suffix | Value | Read as (consumer) |
|---|---|---|---|
| `db_stac_higher_owner` | `db/stac_higher_owner` | JSON `{"username":"stac_higher_owner","password":<48 alnum>}` | `DATABASE_URL` (app, pipeline), migrate Jobs |
| `db_stac_api` | `db/stac_api` | JSON, username `stac_api` | stac-fastapi |
| `db_stac_reader` | `db/stac_reader` | JSON, username `stac_reader` | titiler, tipg |
| `db_stac_proxy_policy` | `db/stac_proxy_policy` | JSON, username `stac_proxy_policy` | auth-proxy `POLICY_DATABASE_URL` |
| `db_cube_server` | `db/cube_server` | JSON, username `cube_server` | cube-server `CUBE_DB_DSN` |
| `session_secret` | `platform/session_secret` | 64 alnum | `SESSION_SECRET` (app) |
| `catalog_bff_shared_secret` | `platform/catalog_bff_shared_secret` | 64 alnum | `CATALOG_BFF_SHARED_SECRET` (app + enforced auth-proxy) |
| `internal_api_token` | `platform/internal_api_token` | 64 alnum | `INTERNAL_API_TOKEN` (app; K-6 admission seam) |
| `credentials_master_key` | `platform/credentials_master_key` | standard base64 of 32 random bytes (44 chars) | `CREDENTIALS_MASTER_KEY` (app + pipeline) |
| `proxy_auth_token` | `platform/proxy_auth_token` | 64 alnum | `PROXY_AUTH_TOKEN` (app; wiring is R-6's call, see Rulings) |

Each secret's `description` names its env var and consumer, e.g. `"SESSION_SECRET for stac-higher (app). Generated by Terraform."`. For the DB entries it names the login role.

- [ ] **Step 1: Write the failing tests** in `infra/terraform/modules/secrets/tests/secrets.tftest.hcl`: the mock block, plus `variables { name_prefix = "stac-test", kms_key_arn = <gov key ARN> }`. Use `command = apply` for the value assertions, and `plan` for the failures.
  - `ten_entries_with_stable_keys`: `toset(keys(output.secret_arns))` equals the ten keys above, and `length(aws_secretsmanager_secret.this) == 10`.
  - `names_under_prefix`: `output.secret_names == { db_stac_higher_owner = "stac-test/db/stac_higher_owner", …, proxy_auth_token = "stac-test/platform/proxy_auth_token" }`. Write out all ten.
  - `encrypted_with_cmk_and_recovery_window`: for every entry, `kms_key_id == var.kms_key_arn` and `recovery_window_in_days == 7` (`alltrue([for s in aws_secretsmanager_secret.this : …])`).
  - `db_entries_are_json_with_role_username`: for each of the five `db_*` keys, `jsondecode(aws_secretsmanager_secret_version.this[k].secret_string)` has `username` equal to the role and a `password` matching `^[A-Za-z0-9]{48}$`.
  - `tokens_are_64_alnum`: the four token keys' `secret_string` each match `^[A-Za-z0-9]{64}$`.
  - `master_key_is_std_base64_of_32_bytes`: `can(regex("^[A-Za-z0-9+/]{43}=$", aws_secretsmanager_secret_version.this["credentials_master_key"].secret_string))`.
  - `db_role_names_in_spec_order`: `output.db_role_names` equals the list above.
  - `description_names_env_var`: `strcontains(aws_secretsmanager_secret.this["session_secret"].description, "SESSION_SECRET")`.
  - `values_stable_across_applies`: a second `apply` run in the same file, asserting `output.secret_arns` equals the first run's (`run.ten_entries_with_stable_keys.secret_arns`). This holds only if no `keepers` or `timestamp()` makes a value churn.
  - `rejects_recovery_window_3` (`recovery_window_in_days = 3`): `expect_failures = [var.recovery_window_in_days]`.
  - `rejects_kms_key_id`: `expect_failures = [var.kms_key_arn]`.

- [ ] **Step 2: Run to verify they fail**

Run: `scripts/check-terraform.sh tofu`
Expected: FAIL in `== infra/terraform/modules/secrets/`.

- [ ] **Step 3: Implement the `secrets` module.**
  - `versions.tf` adds `random`.
  - `random_password.db` (`for_each` over the five roles; `length = 48`, `special = false`).
  - `random_password.token` (`for_each` over the four token keys; `length = 64`, `special = false`).
  - `random_bytes.credentials_master_key` (`length = 32`; value `.base64`).
  - `aws_secretsmanager_secret.this` (`for_each` over the ten entries: `name`, `description`, `kms_key_id`, `recovery_window_in_days`, `tags`).
  - `aws_secretsmanager_secret_version.this` (`secret_id = aws_secretsmanager_secret.this[each.key].id`, `secret_string`).
  - Outputs are ARNs and names only; never output a value.
  - `README.md`:
    - The entry table.
    - Generated values sit in Terraform **state**, so the deployment's backend must be encrypted (ISSUES entry, Task 5).
    - `recovery_window_in_days = 0` for throwaway environments. A destroy then re-apply inside the window fails on the still-scheduled name.
    - What is *not* here: the RDS master (R-5, `manage_master_user_password`), the scanner's Docker Hub token, and ECR upstream credentials (deployment-supplied).
    - The `PROXY_AUTH_TOKEN` caveat.

- [ ] **Step 4: Run to verify they pass**

Run: the Task 1 Step 9 block.
Expected: secrets `11 passed, 0 failed` on both engines. Everything else stays green, with trivy at 0 HIGH/CRITICAL (Secrets Manager CMK check `AWS-0098` satisfied).

- [ ] **Step 5: Commit**

```bash
git add infra/terraform/modules/secrets
git commit -m "R-4: secrets module — per-role DB passwords and platform secrets in Secrets Manager (KMS)"
```

---

### Task 4: `registry` module (ECR pull-through cache)

**Files:**
- Create: `infra/terraform/modules/registry/{versions.tf,variables.tf,main.tf,outputs.tf,README.md}`
- Test: `infra/terraform/modules/registry/tests/registry.tftest.hcl`

**Interfaces:**
- Consumes: `kms_key_arn`, and upstream credential secret ARNs that the deployment created.
- Produces, `registry` module:
  - Inputs:
    - `name_prefix`, `tags`, `kms_key_arn`.
    - `ghcr_credentials_secret_arn` (string, required).
    - `enable_dockerhub` (bool, `false`), `dockerhub_credentials_secret_arn` (string, `null`).
    - `enable_quay` (bool, `false`), `quay_credentials_secret_arn` (string, `null`; Quay needs none for public images).
    - `ecr_prefixes` (object `{ ghcr = string, dockerhub = string, quay = string }`, default `{ ghcr = "ghcr", dockerhub = "docker-hub", quay = "quay" }`, each matching `^[a-z0-9]+(?:[._-][a-z0-9]+)*$` with 2–30 characters). This allows another prefix in an account that already caches GHCR under `ghcr`.
    - `image_tag_mutability` (string, default `"MUTABLE"`, one of `MUTABLE`/`IMMUTABLE`). MUTABLE is ECR's recommendation for pull-through, so cached tags can refresh.
  - The three credential ARNs share one validation: `can(regex("^arn:[a-z-]+:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:ecr-pullthroughcache/.+$", v))`. The two optional ones are written `v == null || can(…)`. The error message states the prefix rule and that the secret must use the default `aws/secretsmanager` key with JSON keys `username` and `accessToken`.
  - Outputs:
    - `registry_host`: `"<account>.dkr.ecr.<region>.<dns_suffix>"`.
    - `mirror_prefixes` (map(string)): reference host → `"<registry_host>/<prefix>"`, for enabled upstreams only. Keys are `ghcr.io`, `docker.io` and `quay.io`. This is the R-8 executor's mirror map.
    - `ghcr_mirror_prefix` (string): `mirror_prefixes["ghcr.io"]`.
    - `repository_creation_role_arn`.

`local.upstreams` (filtered by the enables):

| key | `upstream_registry_url` | reference host | prefix | credential |
|---|---|---|---|---|
| `ghcr` | `ghcr.io` | `ghcr.io` | `var.ecr_prefixes.ghcr` | `var.ghcr_credentials_secret_arn` |
| `dockerhub` | `registry-1.docker.io` | `docker.io` | `var.ecr_prefixes.dockerhub` | `var.dockerhub_credentials_secret_arn` |
| `quay` | `quay.io` | `quay.io` | `var.ecr_prefixes.quay` | `var.quay_credentials_secret_arn` |

- [ ] **Step 1: Write the failing tests** in `infra/terraform/modules/registry/tests/registry.tftest.hcl`: the mock block, plus `variables { name_prefix = "stac-test", kms_key_arn = <gov key ARN>, ghcr_credentials_secret_arn = "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:ecr-pullthroughcache/ghcr-AbCdEf" }`. All runs are `command = plan`. Let `pol = jsondecode(aws_iam_role_policy.repository_creation.policy)`.
  - `ghcr_only_by_default`: `keys(aws_ecr_pull_through_cache_rule.this) == ["ghcr"]`. The `ghcr` rule has `upstream_registry_url == "ghcr.io"`, `ecr_repository_prefix == "ghcr"` and `credential_arn == var.ghcr_credentials_secret_arn`.
  - `template_per_rule_with_cmk`: `keys(aws_ecr_repository_creation_template.this) == ["ghcr"]`. That template has:
    - `prefix == "ghcr"`, `toset(applied_for) == toset(["PULL_THROUGH_CACHE"])`, `image_tag_mutability == "MUTABLE"`.
    - Encryption `encryption_type == "KMS"` and `kms_key == var.kms_key_arn`.
  - `mirror_prefix_from_partition`:
    - `output.ghcr_mirror_prefix == "000000000000.dkr.ecr.us-gov-west-1.dns-suffix.example/ghcr"`.
    - `output.mirror_prefixes == { "ghcr.io" = "000000000000.dkr.ecr.us-gov-west-1.dns-suffix.example/ghcr" }`.
  - `creation_role_trust`: `jsondecode(aws_iam_role.repository_creation.assume_role_policy).Statement[0].Principal.Service == "ecr.dns-suffix.example"` with `Action == "sts:AssumeRole"`.
  - `creation_role_scope`:
    - `toset(pol.Statement[0].Action) == toset(["ecr:CreateRepository", "ecr:ReplicateImage", "ecr:TagResource"])`.
    - `toset(pol.Statement[0].Resource) == toset(["arn:aws-us-gov:ecr:us-gov-west-1:000000000000:repository/ghcr/*"])`.
    - `toset(pol.Statement[1].Action) == toset(["kms:CreateGrant", "kms:RetireGrant", "kms:DescribeKey"])`.
    - `pol.Statement[1].Resource == var.kms_key_arn`.
  - `all_upstreams`: `enable_dockerhub = true` with a valid `ecr-pullthroughcache/dockerhub-…` ARN, and `enable_quay = true` without credentials. Assert:
    - Three rules.
    - The `dockerhub` rule's `upstream_registry_url == "registry-1.docker.io"`.
    - The `quay` rule's `credential_arn == null`.
    - `toset(keys(output.mirror_prefixes)) == toset(["ghcr.io", "docker.io", "quay.io"])`.
    - `output.mirror_prefixes["docker.io"]` ends in `/docker-hub`.
    - `pol.Statement[0].Resource` has three entries.
  - `custom_prefixes`: `ecr_prefixes = { ghcr = "sh-ghcr", dockerhub = "docker-hub", quay = "quay" }` → `output.ghcr_mirror_prefix` ends in `/sh-ghcr`.
  - `rejects_ghcr_secret_outside_prefix` (`…:secret:ghcr-creds-AbCdEf`): `expect_failures = [var.ghcr_credentials_secret_arn]`.
  - `dockerhub_needs_credentials` (`enable_dockerhub = true` only): `expect_failures = [aws_ecr_pull_through_cache_rule.this]`.
  - `rejects_kms_key_id`: `expect_failures = [var.kms_key_arn]`.

- [ ] **Step 2: Run to verify they fail**

Run: `scripts/check-terraform.sh tofu`
Expected: FAIL in `== infra/terraform/modules/registry/`.

- [ ] **Step 3: Implement the `registry` module.**
  - Data sources: partition, caller identity, region.
  - `aws_ecr_pull_through_cache_rule.this` (`for_each = local.upstreams`), with:
    ```hcl
    lifecycle {
      precondition {
        condition     = each.key != "dockerhub" || each.value.credential_arn != null
        error_message = "enable_dockerhub requires dockerhub_credentials_secret_arn: ECR's Docker Hub upstream always authenticates."
      }
    }
    ```
  - `aws_iam_role.repository_creation`: `name = "${var.name_prefix}-ecr-repo-creation"`; the trust policy is the ECR guide's (`Service = "ecr.${dns_suffix}"`, `sts:AssumeRole`).
  - `aws_iam_role_policy.repository_creation`: statement 0 is the ECR actions on `arn:<p>:ecr:<region>:<account>:repository/<prefix>/*` for each enabled prefix; statement 1 is the KMS actions on `var.kms_key_arn`.
  - `aws_ecr_repository_creation_template.this` (`for_each = local.upstreams`):
    - `prefix`, `applied_for = ["PULL_THROUGH_CACHE"]`, `image_tag_mutability`.
    - `encryption_configuration { encryption_type = "KMS", kms_key = var.kms_key_arn }`.
    - `custom_role_arn = aws_iam_role.repository_creation.arn`, `resource_tags = var.tags`, and a `description`.
  - `README.md`:
    - **Deployment prerequisites:** create each upstream secret named `ecr-pullthroughcache/<name>`, JSON `{"username","accessToken"}`, encrypted with the **default `aws/secretsmanager` key** (ECR rejects a CMK), in the same account and region.
    - **Cluster prerequisite:** the node role that pulls needs `ecr:BatchImportUpstreamImage` and `ecr:CreateRepository` for the first, uncached pull. That is the cluster repo's concern; R-8 tests a by-digest uncached pull (spec §5).
    - Docker Hub official images need the `library/` path segment through the cache (R-8's mirror map).
    - No account-level scanning configuration (see Rulings).
    - The outputs, and R-8 as `mirror_prefixes`' consumer.

- [ ] **Step 4: Run to verify they pass**

Run: the Task 1 Step 9 block.
Expected: registry `10 passed, 0 failed` on both engines. Everything else stays green, with trivy at 0 HIGH/CRITICAL.

- [ ] **Step 5: Commit**

```bash
git add infra/terraform/modules/registry
git commit -m "R-4: registry module — ECR pull-through cache rules, creation templates, mirror prefixes"
```

---

### Task 5: Example root, infra README, docs

**Files:**
- Create: `infra/examples/aws-eks/{versions.tf,variables.tf,main.tf,outputs.tf,README.md}`
- Test: `infra/examples/aws-eks/tests/example.tftest.hcl`
- Create: `infra/terraform/README.md`
- Modify: `docs/FEATURES.md` (Phase 8 section), `docs/README.md` (Reference docs list), `docs/backend.md` ("Where each area is documented" table), `docs/ISSUES.md` (new section before `## Resolved — archived`)

**Interfaces:**
- Consumes: the four modules' inputs and outputs as defined in Tasks 1–4.
- Produces, the example:
  - Inputs:
    - `region` (default `"us-east-1"`).
    - `name_prefix` (default `"stac-higher-example"`).
    - `app_origin` (default `"https://app.example.com"`).
    - `ghcr_credentials_secret_name` (default `"ecr-pullthroughcache/ghcr"`).
  - Outputs: `kms_key_arn`, `bucket_name`, `secret_arns`, `mirror_prefixes`. R-5 adds `helm_values`.

- [ ] **Step 1: Write the failing example test** in `infra/examples/aws-eks/tests/example.tftest.hcl` (`command = apply`). Use the mock block plus:
  - `mock_resource "aws_kms_key"`, defaults `arn = "arn:aws-us-gov:kms:us-gov-west-1:000000000000:key/00000000-0000-0000-0000-000000000000"`.
  - `mock_resource "aws_iam_role"`, defaults `arn = "arn:aws-us-gov:iam::000000000000:role/mock"`.
  - `mock_data "aws_secretsmanager_secret"`, defaults `arn = "arn:aws-us-gov:secretsmanager:us-gov-west-1:000000000000:secret:ecr-pullthroughcache/ghcr-AbCdEf"`.

  One run, `wiring`, asserts:
  - `module.storage.kms_key_arn == module.kms.key_arn`.
  - `output.bucket_name == "stac-higher-example-platform"`.
  - `length(output.secret_arns) == 10`.
  - `keys(output.mirror_prefixes) == ["ghcr.io"]`.
  - `module.secrets.secret_names["session_secret"] == "stac-higher-example/platform/session_secret"`. (A test can read module outputs, not resources inside a module, so the CORS wiring is covered by the storage module's own tests.)

- [ ] **Step 2: Run to verify it fails**

Run: `scripts/check-terraform.sh tofu`
Expected: the four modules pass, then FAIL in `== infra/examples/aws-eks/` (no configuration).

- [ ] **Step 3: Implement the example.**
  - `versions.tf` per Global Constraints (aws only). A comment where a backend would go: a deployment repo adds its own backend here; this example has none and is never applied.
  - `main.tf`:
    - `provider "aws" { region = var.region }`.
    - `data "aws_secretsmanager_secret" "ghcr" { name = var.ghcr_credentials_secret_name }`. Comment: the deployment creates it, see the registry README.
    - Module calls `kms`, `storage` (`cors_allowed_origins = [var.app_origin]`), `secrets` and `registry` (`ghcr_credentials_secret_arn = data.aws_secretsmanager_secret.ghcr.arn`), each with `source = "../../terraform/modules/<m>"` and `name_prefix = var.name_prefix`.
    - Head-of-file comment:
      - This is the template a deployment repo copies.
      - A deployment uses `git::` sources pinned by tag (see `infra/terraform/README.md`).
      - **R-5 replaces these four calls with one call to the `stac-higher` composition module and adds the `helm_values` output.**
  - `README.md`: what the example shows, that it is never applied, and `tofu init -backend=false && tofu validate && tofu test` as the way to check it.

- [ ] **Step 4: Run to verify it passes**

Run: `scripts/check-terraform.sh tofu && scripts/check-terraform.sh terraform`
Expected: every directory green. The example prints `1 passed, 0 failed`.

- [ ] **Step 5: Write `infra/terraform/README.md`**:
  - **Header:** `Tracking: epic #136`; spec §7 and ADR 0024 links.
  - **The rules:**
    - No account ids, ARNs, hostnames, CIDRs or secrets anywhere in this repo; placeholders are `000000000000` and `example.com`.
    - Modules never configure a provider, backend or account.
    - Every ARN comes from the partition, and principals and hostnames from `dns_suffix`. CI refuses literal `arn:aws:` and `amazonaws.com` in Terraform files.
    - OpenTofu ≥ 1.10 or Terraform ≥ 1.10.
  - **Modules:** a table (module, creates, key inputs, outputs) for the four modules, with a pointer to each module's README. Note that `database`, `identity`, `cognito`, `demo-destination` and the `stac-higher` composition arrive in R-5.
  - **Consuming by git ref:** the spec §7 form with placeholders,
    `git::https://github.com/<owner>/<repo>.git//infra/terraform/modules/stac-higher?ref=vX.Y.Z`.
    Note that the composition module arrives in R-5. Until then a deployment calls the four modules the same way (`//infra/terraform/modules/kms?ref=vX.Y.Z`, …), as `infra/examples/aws-eks/` does with local paths.
  - **Deployment prerequisites:**
    - The upstream credential secrets (registry README).
    - An encrypted state backend, because generated secrets live in state.
    - The node-role ECR permissions (cluster repo).
  - **Validating locally:** the exact commands from Task 5 Step 7.

- [ ] **Step 6: Docs.**
  - `docs/FEATURES.md`, under `## Phase 8`: add the subsection `### Deployment artifacts (R queue)`. It gets one line `Spec: docs/superpowers/specs/2026-11-02-deployment-artifacts-design.md; slices are GitHub issues under epic #136 (queue: deploy artifacts (R)).` and a table with the row `| R-4 · Terraform base modules | ✅ | kms, storage, secrets, registry under infra/terraform/modules/ (entry: infra/terraform/README.md); placeholder root infra/examples/aws-eks/; CI workflow .github/workflows/infra.yml (fmt, partition check, tflint, validate + mock-provider tests on OpenTofu and Terraform at 1.10 and current, trivy) |`. If a sibling R slice already added the subsection, add only the row.
  - `docs/README.md`, Reference docs list: add `- [`../infra/terraform/README.md`](../infra/terraform/README.md) — the Terraform/OpenTofu modules a deployment repo consumes, their rules and how to validate them (Phase 8, ADR 0024).`
  - `docs/backend.md`, "Where each area is documented" table: add the row `| Deploying to AWS (Terraform/OpenTofu modules; the Helm chart follows in R-6) | [`../infra/terraform/README.md`](../infra/terraform/README.md), ADR 0024 |`.
  - `docs/ISSUES.md`: before `## Resolved — archived`, add `## Deployment artifacts (R queue, epic #136)` with the next free I-number (I-148 when this plan was written; re-check with `grep -oE 'I-[0-9]+' docs/ISSUES.md docs/ISSUES-ARCHIVE.md | sort -t- -k2 -n | tail -1`). Use the entry `### I-148 · Generated platform secrets sit in Terraform state 🟡`, saying:
    - The `secrets` module's `random_*` values are stored in state in plaintext.
    - Write-only arguments (`secret_string_wo`) would avoid that but need OpenTofu/Terraform ≥ 1.11, above the modules' 1.10 floor.
    - Mitigation: the deployment's state backend must be encrypted and access-controlled.
    - Revisit when the floor moves.

    No tracking line (it is a limitation, not work).

- [ ] **Step 7: Full gate run**

Run, from the worktree root:
```bash
tofu fmt -check -recursive -diff infra/terraform && tofu fmt -check -recursive -diff infra/examples
scripts/check-partition-arns.test.sh && scripts/check-partition-arns.sh
scripts/check-terraform.sh tofu && scripts/check-terraform.sh terraform
tflint --init --config "$(pwd)/infra/terraform/.tflint.hcl" && (cd infra && tflint --recursive --config "$(pwd)/terraform/.tflint.hcl")
trivy config --exit-code 1 --severity HIGH,CRITICAL infra/terraform && trivy config --exit-code 1 --severity HIGH,CRITICAL infra/examples
git status --porcelain | grep -E '\.terraform|tfstate|lock\.hcl' || echo "no Terraform artifacts staged"
npm run verify
```
Expected:
- fmt prints nothing; `partition check self-test: 3/3 passed`; the checker exits 0.
- Five directories green on both engines, with test totals kms 8, storage 13, secrets 11, registry 10, example 1.
- tflint is clean and both trivy scans report 0 HIGH/CRITICAL.
- `no Terraform artifacts staged`.
- `npm run verify` passes (app untouched).

For any tool that is not installed, write "CI validates" in the report.

- [ ] **Step 8: Commit**

```bash
git add infra/examples/aws-eks infra/terraform/README.md docs/FEATURES.md docs/README.md docs/backend.md docs/ISSUES.md
git commit -m "R-4: aws-eks example root, infra/terraform README, docs index + state-secrets limitation"
```

**PR notes for the lead:**
- The body starts `Closes #140` and lists the gates run.
- Lead-only steps: none. Nothing is applied until R-11.
- Deviations: the Rulings above, especially `random_bytes` for the master key, the KMS `ViaService` interpretation, the extra `amazonaws.com` check, the separate `infra.yml`, and the generated-but-unwired `PROXY_AUTH_TOKEN`.
