# R-2 · Pipeline + cube-server: S3 credential chain, RDS TLS, credential cap at mint — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The `pipeline` and `cube-server` images authenticate to S3 through the AWS default credential chain (Pod Identity or IRSA) when no static keys are configured, reach RDS over verified TLS, and the pipeline refuses to mint run credentials longer than `PROCESS_MAX_CREDENTIAL_SECONDS`.

**Architecture:**
- **Blank keys mean the default chain.** The platform keys become optional settings. Every platform-bucket client (boto3 S3 and STS, rasterio/GDAL, Icechunk) passes no explicit credentials when they are unset, so each library resolves its own chain. User S3 connections never fall through to that chain.
- **TLS is libpq's job.** psycopg, psycopg_pool and pypgstac hand the DSN to libpq unchanged, so the libpq-standard `PGSSLMODE` / `PGSSLROOTCERT` (or the same keys as DSN parameters) give verified TLS with no code change. Tests pin that, and docs record it.
- **The credential budget is checked at mint.** `mint_prefix_credentials` refuses a duration above the cap with `CredentialBudgetExceeded`. That covers process runs and image scans. The runner records the refusal as a dead run; it is never an infrastructure requeue. A cross-runtime fixture, shared with R-1, pins the arithmetic.

**Tech Stack:** Python 3.12; boto3/botocore 1.43.49, rasterio 1.5.0, icechunk 2.3.0, obstore 0.11.1, psycopg 3.3.4 (binary, libpq 18), psycopg-pool 3.3.1, pypgstac 0.9.11 (pipeline `uv.lock`); icechunk 2.3.0 + psycopg 3 (cube-server `uv.lock`); pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-11-02-deployment-artifacts-design.md` §8 (static S3 credentials, STS role chaining, TLS to RDS), §7.2 (TLS to RDS), §9 (Pod Identity default, IRSA toggle), §15 (workload-identity risk). ADR `docs/decisions/0024-deployment-artifacts.md`. Issue #138, epic #136.

## Global Constraints

- **Worktree:** `.claude/worktrees/r2-pipeline-aws-creds`, branch `feat/r2-pipeline-aws-creds`, off `origin/main` at `d29e979`. Never touch the main checkout or another worktree.
- **S3 default chain:** if the access key id and secret settings are unset or blank, pass **no** explicit credentials to boto3, rasterio/GDAL or Icechunk. Each then uses its default provider chain: env, Pod Identity's `AWS_CONTAINER_CREDENTIALS_FULL_URI` + `AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE`, IRSA web identity, instance profile. Blank means `""` or whitespace, because compose passes `${VAR:-}`. Normalise blank to `None` **before** any library sees it: Icechunk 2.3.0's `s3_credentials(access_key_id="", secret_access_key="")` raises `ValueError: Conflicting arguments`.
- **Keys come as a pair.** Setting exactly one of the pair is a startup `ValueError` naming both variables. A silent fall-through to the chain is never allowed.
- **Credentials are independent of the rest.** Endpoint URL, region and path-style settings keep today's defaults and meaning. `STAGING_S3_ENDPOINT` unset still means the compose MinIO.
- **Compose keeps its explicit keys.** No change to `docker-compose.yml`: `STAGING_S3_ACCESS_KEY_ID=${…:-minioadmin}` and `CUBE_S3_ACCESS_KEY_ID=cube-reader` already set them.
- **`PROCESS_MAX_CREDENTIAL_SECONDS`** (cross-slice contract with R-1, verbatim): integer seconds; unset or empty = no limit (today's behaviour). It is the `Settings` field `process_max_credential_seconds: int | None`, placed next to `process_credential_grace_seconds`. When set and non-blank it must parse as an integer ≥ 900, which is STS's minimum and the pipeline's `MIN_DURATION_SECONDS`. Anything else is a startup `ValueError` naming the variable. This rule matches the app's (R-1).
- **Mint arithmetic:** requested = `max(MIN_DURATION_SECONDS, timeout_seconds + process_credential_grace_seconds)`. That is exactly the `DurationSeconds` sent to STS today. `max_queue_wait_seconds` is **0** until K-4 (#12, unmerged) adds it, so do **not** add the queue wait at mint. This makes the mint intentionally looser than the app's deploy gate. The app uses the profile's `max_queue_wait_seconds`, a required K-1 field, and checks `max_queue_wait_seconds + timeout_seconds + grace ≤ cap`. If the cap is set and requested > cap, raise `CredentialBudgetExceeded`. Never clamp.
- **The refusal is a run error, not infrastructure.** `CredentialBudgetExceeded` must **not** subclass `RunCredentialsError`, because the runner requeues that one without spending an attempt, forever. The runner records the refusal as `dead`, like `SecretResolutionError`.
- **Refusal token:** `CredentialBudgetExceeded.code == "credential_budget_exceeded"`, and the message starts with that token. This equals the fixture's `mint_refusal.error`.
- **Contract fixture:** `tests/contract-fixtures/process-credential-budget.json`, byte-for-byte the content in Task 4. R-1 creates the identical file.
- **TLS env names** are libpq's own: `PGSSLMODE`, `PGSSLROOTCERT`. Unset = today's behaviour. No `Settings` field duplicates them.
- **Never commit** account ids, ARNs, hostnames, CIDRs or secrets. Examples use placeholders (`<rds-endpoint>`, `/etc/rds/ca-bundle.pem`).
- **Logging:** data in `extra={...}`, never interpolated (backend-invariants).
- **Gates, every task:** from `services/pipeline/`: `uv run pytest` and `uv run ruff check .`. Tasks touching `infra/cube-server/` also run, from there, `uv run --extra dev pytest -q` and `uv run --extra dev ruff check .`. Tasks 4 and 6 also run `npm run verify` from the repo root. No e2e, no dev server, no Docker, no push. Commit messages end with the session's attribution trailer.
- **Docs fold into the owning task.** No ADR. This plan adds no `docs/ISSUES.md` entry. If one becomes necessary, take the next free I-number at implementation time; collisions resolve at rebase.

## Decisions this plan takes (flag in the PR)

1. **The unset default changes.** Unset `STAGING_S3_ACCESS_KEY_ID`/`_SECRET_ACCESS_KEY` no longer means `minioadmin`; it means the default chain (issue #138: "When unset or empty"). Compose sets both, so the local stack is unaffected. A host-run pipeline against compose MinIO must now export them.
2. **The cap check sits in `mint_prefix_credentials`,** which `mint_run_credentials` and the image-scan launch both call. A scan's `AssumeRole` is role-chained too. An over-budget scan fails through the drain's existing "could not start" path.
3. **A cap below STS's 900 s floor is a startup error** (cross-slice ruling, identical in R-1). No mint could ever succeed under such a cap, so the process fails at settings load, naming the variable.
4. **A signed S3 connection without both keys is refused** (Task 3). Before R-2 it built a boto3 client with `None` keys, that is the default chain. That was harmless in compose and becomes a confused deputy on EKS: the connection would act as the pipeline's own role. The adapter refuses it with the app's existing message.
5. **`CUBE_S3_ENDPOINT` stays required** on the cube-server, and path-style stays forced. On AWS the chart (R-7) sets the regional endpoint `https://s3.<region>.amazonaws.com`. Making it optional is not needed for R-2.
6. **The GDAL default-chain session is cached per region.** rasterio's `AWSSession` builds a fresh `boto3.Session` per call, which would resolve the chain (an HTTP call to the Pod Identity agent) on every `/vsis3` read. A `functools.cache`d `boto3.Session` resolves once and refreshes through botocore's `RefreshableCredentials`.

## Review Focus

1. **Half-configured keys.** Only `STAGING_S3_ACCESS_KEY_ID` set, or only `CUBE_S3_SECRET_ACCESS_KEY`, fails at startup naming both variables. Signing with half a key pair, or silently with the pod role, never happens. Tests: Task 1, Task 5.
2. **A signed user connection with no keys.** It must not inherit the pipeline's workload identity: refused with the app's message for both boto3 and GDAL. Test: Task 3.
3. **A run over budget.** It dies once, naming requested and cap. It is not requeued every minute without spending an attempt, which is what a `RunCredentialsError` subclass would do. Test: Task 4 runner test.
4. **A cap below 900 s, or one that is not an integer (`899`, `1h`, `3600.5`).** It fails at startup naming `PROCESS_MAX_CREDENTIAL_SECONDS`. It never surfaces later as an opaque STS `ValidationError` on every run. Test: Task 1.
5. **Blank strings from compose `${VAR:-}`.** Keys and cap are treated as unset, never as `""` credentials (which Icechunk rejects) or `int("")`. Tests: Task 1.

---

### Task 1: Settings — optional platform keys and `PROCESS_MAX_CREDENTIAL_SECONDS`

**Files:**
- Modify: `services/pipeline/src/pipeline/config.py`. Module docstring env list, the two `DEFAULT_STAGING_S3_*_KEY` constants (delete them), `Settings` fields, `from_env`, and a new `_parse_max_credential_seconds`.
- Modify: `services/pipeline/README.md` "Environment contract" table.
- Test: `services/pipeline/tests/test_config.py`

**Interfaces:**
- Produces:
  - `Settings.staging_s3_access_key: str | None = None`.
  - `Settings.staging_s3_secret_key: str | None = field(default=None, repr=False)`.
  - `Settings.process_max_credential_seconds: int | None = None`, next to `process_credential_grace_seconds`.
  - `from_env` raises `ValueError` for a half-set key pair and for a non-positive or non-integer cap.

- [ ] **Step 1: Write the failing tests** in `tests/test_config.py`

```python
@pytest.mark.parametrize("env", [{}, {"STAGING_S3_ACCESS_KEY_ID": "", "STAGING_S3_SECRET_ACCESS_KEY": "  "}])
def test_staging_s3_keys_unset_or_blank_mean_the_default_chain(env):
    s = Settings.from_env(env=env)
    assert s.staging_s3_access_key is None and s.staging_s3_secret_key is None

@pytest.mark.parametrize("env", [{"STAGING_S3_ACCESS_KEY_ID": "AK"}, {"STAGING_S3_SECRET_ACCESS_KEY": "SK"}])
def test_staging_s3_keys_must_be_set_together(env):
    with pytest.raises(ValueError, match="STAGING_S3_ACCESS_KEY_ID and STAGING_S3_SECRET_ACCESS_KEY"):
        Settings.from_env(env=env)

def test_explicit_staging_keys_are_kept_and_the_secret_never_prints():
    s = Settings.from_env(env={"STAGING_S3_ACCESS_KEY_ID": "AK", "STAGING_S3_SECRET_ACCESS_KEY": "SK-secret"})
    assert (s.staging_s3_access_key, s.staging_s3_secret_key) == ("AK", "SK-secret")
    assert "SK-secret" not in repr(s)

@pytest.mark.parametrize("env", [{}, {"PROCESS_MAX_CREDENTIAL_SECONDS": ""}, {"PROCESS_MAX_CREDENTIAL_SECONDS": " "}])
def test_max_credential_seconds_unset_or_blank_is_no_limit(env):
    assert Settings.from_env(env=env).process_max_credential_seconds is None

@pytest.mark.parametrize(("raw", "want"), [("3600", 3600), ("900", 900)])
def test_max_credential_seconds_parses(raw, want):
    assert Settings.from_env(env={"PROCESS_MAX_CREDENTIAL_SECONDS": raw}).process_max_credential_seconds == want

@pytest.mark.parametrize("raw", ["0", "-1", "899", "1h", "3600.5"])
def test_max_credential_seconds_refuses_anything_but_an_integer_of_at_least_900(raw):
    with pytest.raises(ValueError, match="PROCESS_MAX_CREDENTIAL_SECONDS"):
        Settings.from_env(env={"PROCESS_MAX_CREDENTIAL_SECONDS": raw})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_config.py -v`
Expected: the new tests FAIL. The unset keys are `minioadmin`, and the field does not exist yet.

- [ ] **Step 3: Implement in `config.py`**

- Read both keys through the existing `_optional()` helper.
- If exactly one is `None`, raise `ValueError("STAGING_S3_ACCESS_KEY_ID and STAGING_S3_SECRET_ACCESS_KEY must be set together, or both left unset for the default AWS credential chain")`.
- Add `_parse_max_credential_seconds(raw: str | None) -> int | None`: blank → `None`; `int(raw)` must succeed and be ≥ `STS_MIN_DURATION_SECONDS`. Otherwise raise `ValueError(f"PROCESS_MAX_CREDENTIAL_SECONDS must be an integer number of seconds, at least {STS_MIN_DURATION_SECONDS} (the STS minimum), got {raw!r}")`.
- Define `STS_MIN_DURATION_SECONDS = 900` in `config.py`; config must not import the process package at module load. `process/credentials.py` then sets `MIN_DURATION_SECONDS = STS_MIN_DURATION_SECONDS`, so the floor has one source.
- Run `grep -rn DEFAULT_STAGING_S3_ACCESS_KEY services/` first: nothing outside `config.py` uses the constants today.
- Update the docstring bullet: "unset or blank = the default AWS credential chain; set both or neither". Add a `PROCESS_MAX_CREDENTIAL_SECONDS` bullet.

- [ ] **Step 4: Update `services/pipeline/README.md`'s env table.** Add these rows after `REGISTRY_DOCKERHUB_*`:
  - `STAGING_S3_ACCESS_KEY_ID` / `STAGING_S3_SECRET_ACCESS_KEY`. Default: _(unset — the default AWS credential chain)_. Meaning: the platform bucket's keys, used for the S3 client, STS, EXTRACT's GDAL reads and the cube writer's Icechunk storage. Set both or neither. Unset = Pod Identity / IRSA / instance profile. Compose sets the MinIO keys. The secret never appears in `repr`.
  - `PROCESS_CREDENTIAL_GRACE_SECONDS`. Default `300`. Meaning: run credentials live for `timeout_seconds` + this.
  - `PROCESS_MAX_CREDENTIAL_SECONDS`. Default: _(unset — no limit)_. Meaning: the longest run credential the pipeline will mint, as an integer ≥ 900 (anything else fails startup). Set `3600` on AWS, where STS role chaining caps a session at one hour. A run or scan needing more is refused with `credential_budget_exceeded` (deployment-artifacts spec §8).

- [ ] **Step 5: Run the gates**

Run: `cd services/pipeline && uv run pytest && uv run ruff check .`
Expected: all PASS. If an existing test relied on the `minioadmin` default, give it explicit keys; do not restore the default.

- [ ] **Step 6: Commit**

```bash
git add services/pipeline/src/pipeline/config.py services/pipeline/tests/test_config.py services/pipeline/README.md
git commit -m "R-2: platform S3 keys optional (default chain), PROCESS_MAX_CREDENTIAL_SECONDS setting"
```

### Task 2: Platform-bucket clients use the default chain when the keys are unset

**Files:**
- Modify: `services/pipeline/src/pipeline/storage/platform.py`. Changes: `build_platform_client`, `PlatformS3Access`, `raster_location`, plus a new `static_s3_credentials` and `default_chain_session`.
- Modify: `services/pipeline/src/pipeline/process/credentials.py`: `build_sts_client`.
- Modify: `services/pipeline/src/pipeline/cubes/icerepo.py`: `cube_storage`. The docstring is the only change, because `None` keys already reach `ic.s3_storage` and select `FromEnv`.
- Test: `services/pipeline/tests/test_platform_credentials.py` (create)

**Interfaces:**
- Consumes: Task 1's `Settings.staging_s3_access_key` / `staging_s3_secret_key: str | None`.
- Produces:
  - `static_s3_credentials(settings: Settings) -> dict[str, str]` returns `{"aws_access_key_id": …, "aws_secret_access_key": …}`, or `{}` when unset. It is splatted into `boto3.client(...)`.
  - `default_chain_session(region: str) -> boto3.Session` is `functools.cache`d.
  - `PlatformS3Access.access_key: str | None`, `PlatformS3Access.secret_key: str | None`. Both stay `repr=False`.

- [ ] **Step 1: Write the failing tests** in `tests/test_platform_credentials.py`

Shared setup:
- A `_capture(monkeypatch, target)` helper replaces `target` with a fake that appends `{"args": args, **kwargs}` per call and returns a sentinel object. The list it returns is what the tests read.
- `KEYLESS = Settings.from_env(env={"STAGING_S3_ENDPOINT": "http://minio:9000", "EGRESS_ALLOW_HOSTS": "minio"})`.
- `KEYED` is the same plus `AK`/`SK`.
- An autouse fixture calls `default_chain_session.cache_clear()` before and after each test.

```python
def test_platform_client_without_keys_passes_no_credentials(monkeypatch):
    calls = _capture(monkeypatch, "pipeline.storage.platform.boto3.client")
    build_platform_client(KEYLESS)
    kwargs = calls[0]
    assert "aws_access_key_id" not in kwargs and "aws_secret_access_key" not in kwargs
    assert kwargs["region_name"] == "us-east-1" and kwargs["endpoint_url"].endswith(":9000")

def test_platform_client_with_keys_passes_them(monkeypatch):
    calls = _capture(monkeypatch, "pipeline.storage.platform.boto3.client")
    build_platform_client(KEYED)
    assert (calls[0]["aws_access_key_id"], calls[0]["aws_secret_access_key"]) == ("AK", "SK")

def test_sts_client_without_keys_uses_the_default_chain(monkeypatch):
    calls = _capture(monkeypatch, "pipeline.process.credentials.boto3.client")
    build_sts_client(KEYLESS)
    assert calls[0]["args"] == ("sts",)
    assert "aws_access_key_id" not in calls[0] and "aws_secret_access_key" not in calls[0]

def test_raster_location_without_keys_signs_with_the_default_chain(monkeypatch, tmp_path):
    for name, value in {"AWS_ACCESS_KEY_ID": "CHAIN-AK", "AWS_SECRET_ACCESS_KEY": "CHAIN-SK",
                        "AWS_SESSION_TOKEN": "CHAIN-T", "AWS_EC2_METADATA_DISABLED": "true",
                        "AWS_SHARED_CREDENTIALS_FILE": str(tmp_path / "none"),
                        "AWS_CONFIG_FILE": str(tmp_path / "none")}.items():
        monkeypatch.setenv(name, value)
    access = platform_s3_access(KEYLESS)
    first = raster_location(access, "stac-higher", "assets/c/i/a.tif", gdal_cachemax_mb=64)
    second = raster_location(access, "stac-higher", "assets/c/i/b.tif", gdal_cachemax_mb=64)
    opts = first.session.get_credential_options()
    assert (opts["AWS_ACCESS_KEY_ID"], opts["AWS_SESSION_TOKEN"]) == ("CHAIN-AK", "CHAIN-T")
    assert first.session._session is second.session._session  # one chain resolution per process

def test_cube_storage_without_keys_selects_icechunk_from_env(monkeypatch):
    calls = _capture(monkeypatch, "pipeline.cubes.icerepo.ic.s3_storage")
    cube_storage(KEYLESS, "goes19-c13-cube")
    kw = calls[0]
    assert kw["access_key_id"] is None and kw["secret_access_key"] is None
    assert isinstance(ic.s3_credentials(access_key_id=kw["access_key_id"],
                                        secret_access_key=kw["secret_access_key"]),
                      ic.S3Credentials.FromEnv)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_platform_credentials.py -v`
Expected: the boto3 and STS tests FAIL with `aws_access_key_id` present as `None`. The raster test FAILs because `default_chain_session` does not exist (ImportError). The Icechunk test PASSes already; keep it, since it pins the `None` → `FromEnv` contract.

- [ ] **Step 3: Implement**

- Add `static_s3_credentials`. `build_platform_client` and `build_sts_client` replace their two key kwargs with `**static_s3_credentials(settings)`.
- `raster_location`:
  - **Keys present:** construct the `AWSSession` exactly as today.
  - **Keys absent:** `AWSSession(session=default_chain_session(access.region), endpoint_url=gdal_endpoint(access.endpoint_url))`. rasterio then freezes botocore's (refreshable) chain credentials into the GDAL options per read.
- Update the module docstrings that say "the platform's own keys" to say "the platform's keys or its workload identity".

- [ ] **Step 4: Run the gates**

Run: `cd services/pipeline && uv run pytest && uv run ruff check .`
Expected: all PASS. This includes the existing `test_platform_get.py::test_platform_s3_access_pins_the_endpoint_and_raster_location_is_vsis3`, the keyed path.

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/storage/platform.py services/pipeline/src/pipeline/process/credentials.py services/pipeline/src/pipeline/cubes/icerepo.py services/pipeline/tests/test_platform_credentials.py
git commit -m "R-2: platform S3, STS, GDAL and Icechunk clients use the default credential chain without keys"
```

### Task 3: A signed S3 connection never inherits the pipeline's workload identity

**Files:**
- Modify: `services/pipeline/src/pipeline/connections/adapters/s3.py`: `S3Adapter.__init__`.
- Test: `services/pipeline/tests/test_adapters.py`

**Interfaces:**
- Produces: `S3Adapter(config, credentials)` raises `ValueError(S3_KEYS_REQUIRED)` when `config.anonymous` is false and `access_key_id` or `secret_access_key` is missing or blank. `S3_KEYS_REQUIRED = "access_key_id and secret_access_key are required unless config.anonymous is true"` is the app's message (`app/src/lib/connections/schemas.ts`). `build_adapter` already turns a `ValueError` into `AdapterBuildError`.

- [ ] **Step 1: Write the failing tests** in `tests/test_adapters.py`

```python
@pytest.mark.parametrize("creds", [{}, {"access_key_id": "AK"}, {"secret_access_key": "SK"},
                                   {"access_key_id": " ", "secret_access_key": ""}])
def test_a_signed_s3_connection_without_both_keys_is_refused(creds):
    """Without this, boto3 and rasterio fall back to the default chain -- on EKS,
    the pipeline's own role (R-2 Decision 4)."""
    with pytest.raises(ValueError, match="required unless config.anonymous is true"):
        S3Adapter({"bucket": "b"}, creds)

def test_an_anonymous_s3_connection_needs_no_keys():
    S3Adapter({"bucket": "noaa-goes19", "anonymous": True}, {})
```

Also add `test_build_adapter_refuses_a_signed_s3_row_without_keys` in `tests/test_build_adapter.py`. It uses that file's `_conn(...)` helper, which seals `creds`, with `{}` credentials on a non-anonymous `s3` row, and asserts `AdapterBuildError` with `match="required unless config.anonymous is true"`.

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_adapters.py tests/test_build_adapter.py -v`
Expected: the refusal tests FAIL (no exception raised).

- [ ] **Step 3: Implement the guard** in `S3Adapter.__init__`, after `parse_s3_config`. Run `grep -n "S3Adapter(\|adapter_for(" services/pipeline/tests/*.py` and give any signed-connection test that passes `{}` credentials explicit keys.

- [ ] **Step 4: Run the gates**

Run: `cd services/pipeline && uv run pytest && uv run ruff check .`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/connections/adapters/s3.py services/pipeline/tests/
git commit -m "R-2: a signed S3 connection without keys is refused, never signed with the pipeline's identity"
```

### Task 4: Credential cap at mint + the shared contract fixture

**Files:**
- Modify: `services/pipeline/src/pipeline/process/credentials.py`: add `CredentialBudgetExceeded` and the check in `mint_prefix_credentials`.
- Modify: `services/pipeline/src/pipeline/process/runner.py`: add an `except CredentialBudgetExceeded` → `dead` in `run_one`, beside the `SecretResolutionError` branch.
- Create: `tests/contract-fixtures/process-credential-budget.json`
- Modify: `tests/contract-fixtures/README.md`
- Test: `services/pipeline/tests/test_process_executor.py`, `services/pipeline/tests/test_process_triggers.py`, `services/pipeline/tests/test_contract_fixtures.py`

**Interfaces:**
- Consumes: Task 1's `Settings.process_max_credential_seconds: int | None`.
- Produces:

```python
class CredentialBudgetExceeded(Exception):   # NOT a RunCredentialsError subclass
    code: ClassVar[str] = "credential_budget_exceeded"
    def __init__(self, *, requested_seconds: int, max_seconds: int,
                 timeout_seconds: int, grace_seconds: int) -> None: ...
```

The message reads, exactly: `f"credential_budget_exceeded: run credentials would need {requested_seconds} s (timeout {timeout_seconds} s + grace {grace_seconds} s, STS minimum {MIN_DURATION_SECONDS} s) but PROCESS_MAX_CREDENTIAL_SECONDS is {max_seconds} s"`. It is raised by `mint_prefix_credentials` after prefix validation and before any STS call.

- [ ] **Step 1: Create the fixture** `tests/contract-fixtures/process-credential-budget.json` with exactly this content (R-1 creates the identical file):

```json
{
  "setting": "PROCESS_MAX_CREDENTIAL_SECONDS",
  "unset_means": "no limit",
  "grace_setting": "PROCESS_CREDENTIAL_GRACE_SECONDS",
  "grace_default_seconds": 300,
  "deploy_refusal": { "status": 422, "code": "credential_budget_exceeded" },
  "mint_refusal": { "error": "credential_budget_exceeded" },
  "cases": [
    { "name": "at budget", "max_credential_seconds": 3600, "max_queue_wait_seconds": 600, "timeout_seconds": 2700, "grace_seconds": 300, "deploy_allowed": true, "mint_allowed": true },
    { "name": "one over", "max_credential_seconds": 3600, "max_queue_wait_seconds": 600, "timeout_seconds": 2701, "grace_seconds": 300, "deploy_allowed": false, "mint_allowed": true },
    { "name": "mint over", "max_credential_seconds": 3600, "max_queue_wait_seconds": 0, "timeout_seconds": 3301, "grace_seconds": 300, "deploy_allowed": false, "mint_allowed": false },
    { "name": "unset", "max_credential_seconds": null, "max_queue_wait_seconds": 0, "timeout_seconds": 86400, "grace_seconds": 300, "deploy_allowed": true, "mint_allowed": true }
  ]
}
```

Check that `mint_allowed` holds with the queue wait treated as 0, which it does by construction:
- at budget: 2700 + 300 = 3000 ≤ 3600;
- one over: 3001 ≤ 3600;
- mint over: 3601 > 3600;
- unset: no limit.

- [ ] **Step 2: Write the failing tests**

In `tests/test_contract_fixtures.py`, build `Settings` **through `from_env` using the fixture's own variable names**, so the names are pinned too:

```python
BUDGET = _load("process-credential-budget.json")

class _Sts:
    def assume_role(self, **kwargs):
        return {"Credentials": {"AccessKeyId": "A", "SecretAccessKey": "S", "SessionToken": "T"}}

@pytest.mark.parametrize("case", BUDGET["cases"], ids=lambda c: c["name"])
def test_process_credential_budget_mint_cases(case):
    # Mint ignores max_queue_wait_seconds until K-4 (#12) adds it to the duration.
    cap = case["max_credential_seconds"]
    settings = Settings.from_env({BUDGET["setting"]: "" if cap is None else str(cap),
                                  BUDGET["grace_setting"]: str(case["grace_seconds"]),
                                  "STAGING_BUCKET": "stac-higher"})
    if case["mint_allowed"]:
        mint_run_credentials(settings, "run-1", case["timeout_seconds"], sts_client=_Sts())
    else:
        with pytest.raises(CredentialBudgetExceeded) as err:
            mint_run_credentials(settings, "run-1", case["timeout_seconds"], sts_client=_Sts())
        assert err.value.code == BUDGET["mint_refusal"]["error"]
        assert str(err.value).startswith(BUDGET["mint_refusal"]["error"])

def test_process_credential_budget_defaults_match_golden():
    assert Settings.from_env({}).process_credential_grace_seconds == BUDGET["grace_default_seconds"]
    assert Settings.from_env({}).process_max_credential_seconds is None  # unset_means: no limit
```

In `tests/test_process_executor.py`, using its `settings()` and `FakeSts` helpers:

```python
def test_mint_refuses_above_the_cap_and_never_calls_sts():
    sts = FakeSts({"Credentials": {"AccessKeyId": "A", "SecretAccessKey": "S", "SessionToken": "T"}})
    with pytest.raises(CredentialBudgetExceeded) as err:
        mint_run_credentials(settings(PROCESS_MAX_CREDENTIAL_SECONDS=3600), RUN, 3301, sts_client=sts)
    assert (err.value.requested_seconds, err.value.max_seconds) == (3601, 3600)
    assert "3601 s" in str(err.value) and "PROCESS_MAX_CREDENTIAL_SECONDS is 3600 s" in str(err.value)
    assert sts.kwargs is None   # FakeSts records kwargs only when assume_role is called

def test_mint_at_the_cap_is_allowed():
    sts = FakeSts({"Credentials": {"AccessKeyId": "A", "SecretAccessKey": "S", "SessionToken": "T"}})
    mint_run_credentials(settings(PROCESS_MAX_CREDENTIAL_SECONDS=3600), RUN, 3300, sts_client=sts)
    assert sts.kwargs["DurationSeconds"] == 3600

def test_scans_are_bounded_by_the_same_cap():
    with pytest.raises(CredentialBudgetExceeded):
        mint_prefix_credentials(settings(PROCESS_MAX_CREDENTIAL_SECONDS=900), session_name="stac-scan-x",
                                prefix="scans/i/s/", timeout_seconds=900, sts_client=FakeSts({}))

def test_the_budget_refusal_is_not_an_infrastructure_error():
    assert not issubclass(CredentialBudgetExceeded, RunCredentialsError)
```

In `tests/test_process_triggers.py`, using `_run`, `queued` and `FakeProcessRepo`:

```python
@pytest.mark.asyncio
async def test_a_run_over_the_credential_budget_dies_once_naming_the_budget():
    repo, executor = FakeProcessRepo(), MemoryExecutor(results=[ExitStatus(0)])
    run = queued(runtime={"kind": "inline_python", "timeout_seconds": 3301, "retry": {"max_attempts": 3}})
    result = await _run(run, executor, repo,
                        settings=Settings.from_env({"PROCESS_MAX_CREDENTIAL_SECONDS": "3600"}))
    assert result.status == "dead" and repo.finished[0]["status"] == "dead"
    assert result.error.startswith("credential_budget_exceeded")
    assert executor.launched == []
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_contract_fixtures.py tests/test_process_executor.py tests/test_process_triggers.py -k "budget or cap" -v`
Expected: FAIL with `ImportError: cannot import name 'CredentialBudgetExceeded'`.

- [ ] **Step 4: Implement** the class and the check in `mint_prefix_credentials`, using the arithmetic in Global Constraints. Then add the runner branch: `except CredentialBudgetExceeded as err: await _finish(repo, run, "dead", None, str(err), None, at, on_dead=on_dead); return RunResult(run.id, "dead", error=str(err))`. Update the module docstring's "Expiry is the run's timeout plus a grace window" paragraph: the deployment may cap it, and a refusal beats an expiry mid-run.

- [ ] **Step 5: Document the fixture** in `tests/contract-fixtures/README.md`. Insert this section **immediately before** the heading "## Why `app` and `pipeline` expectations can differ", exactly as written. R-1 adds the identical section in the identical place, so the rebase merges cleanly.

```markdown
## Additional fixture styles (R queue, R-1/R-2)

- `process-credential-budget.json` — a settings contract (no `style` marker). It pins the deployment cap `PROCESS_MAX_CREDENTIAL_SECONDS` (unset = no limit), the grace setting `PROCESS_CREDENTIAL_GRACE_SECONDS` and its default (300 s), the deploy refusal (422, `code` `credential_budget_exceeded`) and the mint refusal's error. Each `cases[]` entry gives the three budget inputs and the cap, with `deploy_allowed` (the app's deploy gate, `app/src/lib/processes/credential-budget.ts`: `max_queue_wait_seconds + timeout_seconds + grace ≤ cap`) and `mint_allowed` (the pipeline's STS mint, `pipeline/process/credentials.py`). The vitest consumer checks `deploy_allowed`; pytest checks `mint_allowed`.
```

- [ ] **Step 6: Run the gates**

Run: `cd services/pipeline && uv run pytest && uv run ruff check .`, then `npm run verify` from the repo root.
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add services/pipeline/src/pipeline/process/ services/pipeline/tests/ tests/contract-fixtures/process-credential-budget.json tests/contract-fixtures/README.md
git commit -m "R-2: refuse to mint run credentials above PROCESS_MAX_CREDENTIAL_SECONDS (dead run, shared fixture)"
```

### Task 5: cube-server — optional keys (default chain) and verified TLS

**Files:**
- Modify: `infra/cube-server/stac_higher_cube_server/settings.py`
- Modify: `infra/cube-server/stac_higher_cube_server/registry.py`: the `S3RepoOpener` docstring only, because `None` keys reach `ic.s3_storage` as they are.
- Modify: `infra/cube-server/README.md` "Env" table.
- Test: `infra/cube-server/tests/test_sinks.py` (settings tests), `infra/cube-server/tests/test_registry.py`, `infra/cube-server/tests/test_db_tls.py` (create).

**Interfaces:**
- Produces:
  - `Settings.s3_access_key_id: str | None`, `Settings.s3_secret_access_key: str | None` (`field(repr=False)`). Both stay positional, so the existing `Settings(...)` constructions in the tests still work.
  - `_REQUIRED = ("CUBE_DB_DSN", "CUBE_S3_ENDPOINT", "PLATFORM_ASSET_BUCKET")`.
  - A half-set pair raises `ValueError` naming `CUBE_S3_ACCESS_KEY_ID and CUBE_S3_SECRET_ACCESS_KEY`.

- [ ] **Step 1: Write the failing tests**

- **`test_sinks.py`.** Change `test_settings_names_every_missing_var` to iterate the three required names and assert that `CUBE_S3_ACCESS_KEY_ID` is **not** in the message. Add:
  - `test_settings_without_keys_use_the_default_chain`: keys absent, then `""`, both give `None`.
  - `test_settings_keys_must_be_set_together`: parametrized over each half, `pytest.raises(ValueError, match="CUBE_S3_ACCESS_KEY_ID and CUBE_S3_SECRET_ACCESS_KEY")`.
  - `test_settings_secret_never_prints`.
- **`test_registry.py`.** Add `test_keyless_opener_selects_icechunk_from_env(monkeypatch)`. Capture `stac_higher_cube_server.registry.ic.s3_storage` kwargs from `S3RepoOpener(<SETTINGS with both keys None>)._storage("c1")`. Assert both keys are `None` and `isinstance(ic.s3_credentials(access_key_id=None, secret_access_key=None), ic.S3Credentials.FromEnv)`.
- **`test_db_tls.py`.** A loopback fixture `tls_wanting_server` accepts, reads the 8-byte SSLRequest, answers `b"S"` and holds the socket for about a second; it yields the port. Then:

```python
def test_pg_sink_source_verifies_tls_from_the_libpq_environment(tls_wanting_server, monkeypatch):
    monkeypatch.setenv("PGSSLMODE", "verify-full")
    monkeypatch.setenv("PGSSLROOTCERT", "/nonexistent/rds-ca-bundle.pem")
    with pytest.raises(psycopg.OperationalError, match="/nonexistent/rds-ca-bundle.pem"):
        PgSinkSource(f"postgresql://u:p@127.0.0.1:{tls_wanting_server}/db").enabled_sinks()
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd infra/cube-server && uv run --extra dev pytest -q`
Expected: the settings tests FAIL (keys still required). The TLS and registry tests PASS already; they characterise libpq and Icechunk, and they stay as pins.

- [ ] **Step 3: Implement `settings.py`.** Read the keys with blank → `None` and add the pair check. Update the README rows:
  - `CUBE_S3_ACCESS_KEY_ID` / `CUBE_S3_SECRET_ACCESS_KEY`: "Optional. Set both or neither. Unset = the default AWS credential chain (Pod Identity / IRSA), whose role needs read on `assets/*/_cube/*` only. Compose sets `cube-reader`."
  - New row `PGSSLMODE` / `PGSSLROOTCERT`: "libpq's own TLS settings, read for `CUBE_DB_DSN`; `verify-full` + the RDS CA bundle path on AWS. The same keys work as DSN parameters. Unset = today's behaviour."

- [ ] **Step 4: Run the gates**

Run: `cd infra/cube-server && uv run --extra dev pytest -q && uv run --extra dev ruff check .`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add infra/cube-server/
git commit -m "R-2: cube-server S3 keys optional (default chain); libpq TLS env pinned by a test"
```

### Task 6: Pipeline TLS pins, library findings, deployment docs

**Files:**
- Test: `services/pipeline/tests/test_db_tls.py` (create)
- Modify: `docs/backend.md`: a new section after "App environment", plus the cube-server row in the local-stack table.
- Modify: `docs/FEATURES.md`: the Phase 8 section.
- Modify: `.env.example`: the pipeline staging and process blocks.
- Modify: `services/pipeline/README.md`: two rows, `PGSSLMODE` / `PGSSLROOTCERT`.

**Interfaces:**
- Consumes: Tasks 1–5 (names only, for the docs).

- [ ] **Step 1: Write the TLS pins** in `tests/test_db_tls.py`. Use the same `tls_wanting_server` fixture shape as Task 5, defined locally because the two packages do not share test code.
  - `test_database_url_connections_verify_tls_from_the_libpq_environment`: with `PGSSLMODE=verify-full` and `PGSSLROOTCERT=/nonexistent/rds-ca-bundle.pem`, `await psycopg.AsyncConnection.connect(Settings.from_env({"DATABASE_URL": url}).database_url)` raises `psycopg.OperationalError` matching that path. This is the call the listener, query-queue drainer, Procrastinate and both pools make.
  - `test_tls_parameters_in_the_dsn_work_without_env`: env cleared, `?sslmode=verify-full&sslrootcert=/nonexistent/x.pem` on the URL gives the same error naming `/nonexistent/x.pem`.
  - `test_pool_name_drops_tls_parameters`: `pool_name("postgresql://u:p@db.example:5432/postgis?sslmode=verify-full&sslrootcert=/etc/rds/ca-bundle.pem") == "db.example:5432/postgis"`.

Run: `cd services/pipeline && uv run pytest tests/test_db_tls.py -v`
Expected: PASS at once. These pin libpq behaviour for the pinned psycopg-binary, not new code. A future driver swap or DSN rewrite fails them.

- [ ] **Step 2: Re-confirm the library findings** on the rebased branch. Each result must match the table in Step 3; record any difference there instead.
  - `cd services/pipeline && uv pip show boto3 botocore obstore icechunk rasterio psycopg pypgstac | grep -E '^(Name|Version)'`
  - `grep -n "169.254.170.23\|AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE\|AWS_WEB_IDENTITY_TOKEN_FILE" .venv/lib/python3.12/site-packages/botocore/{utils,credentials}.py`
  - `strings -a .venv/lib/python3.12/site-packages/icechunk/_icechunk_python.abi3.so | grep -o 'AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE\|AWS_WEB_IDENTITY_TOKEN_FILE\|aws-config-[0-9.]*' | sort -u`
  - `strings -a .venv/lib/python3.12/site-packages/obstore/_obstore.abi3.so | grep -io 'aws_container_credentials_full_uri\|aws_container_authorization_token_file\|aws_web_identity_token_file\|object_store-[0-9.]*' | sort -u`
  - The titiler base: if `infra/titiler/Dockerfile` still pins `titiler-pgstac:1.7.2@sha256:29920c615edf…`, the finding stands. If the pin moved, pull the new amd64 manifest's largest pip layer anonymously from GHCR (no Docker) and read `rasterio.libs/libgdal-*.so.*`'s version suffix.

- [ ] **Step 3: Write `docs/backend.md` "## Deployment: S3 credentials and database TLS (R-2)"**, after "App environment". It has four parts.

  **Part 1: S3 credentials.** One paragraph: blank keys mean the default chain, keys come as a pair, compose keeps keys, and a signed user connection never uses the chain (Task 3). Then a table with columns *Workload · Client · Pinned version · Pod Identity (container credentials full URI + token file) · IRSA (web identity) · Evidence*:
  - **pipeline · boto3/botocore (platform S3, STS, and the credentials behind EXTRACT's GDAL reads):** 1.43.49 · yes · yes. Evidence: `ContainerProvider` allows `169.254.170.23` / `fd00:ec2::23` and reads `AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE`; `AssumeRoleWithWebIdentityProvider` is present.
  - **pipeline · rasterio 1.5.0 / GDAL (platform `/vsis3`):** credentials come from botocore through `AWSSession(session=…)`, so GDAL's own chain is not used and its version does not matter here.
  - **pipeline · Icechunk 2.3.0 (cube writer):** aws-config 1.8.15 · yes · yes. Evidence: `ecs.rs` allows `169.254.170.23` / `fd00:ec2::23` and reads the token file. `None` keys select `S3Credentials.FromEnv`; blank strings raise, hence the normalisation.
  - **pipeline · obstore 0.11.1 (object_store 0.14.1):** yes · yes (config keys `aws_container_credentials_full_uri`, `aws_container_authorization_token_file`, `aws_web_identity_token_file`). Used today only unsigned, for anonymous cube sources, so no platform credential reaches it.
  - **cube-server · Icechunk 2.3.0:** as the pipeline row.
  - **titiler · titiler-pgstac 1.7.2 base → rasterio 1.4.3 wheel, GDAL 3.9.3:** yes (≥ 3.9, OSGeo/gdal#9877; `libgdal` carries `AWS_CONTAINER_CREDENTIALS_FULL_URI` / `AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE`) · yes (`AssumeRoleWithWebIdentity`). The image has no boto3, so rasterio hands GDAL nothing and GDAL's own chain applies. The chart must not set `AWS_ACCESS_KEY_ID` for titiler on AWS.
  - **app:** R-1.

  Closing line: no workload needs the IRSA toggle for a library gap. The toggle remains for the role-chaining question (spec §8, R-11).

  **Part 2: Run credential budget.** One paragraph on `PROCESS_MAX_CREDENTIAL_SECONDS`: what it bounds, the dead-run refusal, scans included, the fixture, and that the queue wait is added by K-4.

  **Part 3: Database TLS.** Set `PGSSLMODE=verify-full` and `PGSSLROOTCERT=<path to the RDS CA bundle>`, or put both keys in the DSN as `?sslmode=verify-full&sslrootcert=…`. libpq reads them for every connection the pipeline makes: the async pool, Procrastinate, the LISTEN connection, the query-queue drainer, pypgstac's `PgstacDB` (it passes the DSN through), and `pypgstac migrate`, which with no `--dsn` reads `PG*` variables. The cube-server's `CUBE_DB_DSN` behaves the same. `sslrootcert=system` (libpq ≥ 16) uses the OS trust store instead. Unset = today's behaviour. Pinned by both `test_db_tls.py` files.

  **Part 4: the local-stack table.** In the cube-server row, mark the two keys as optional (default chain) and add `PGSSLMODE`/`PGSSLROOTCERT`.

- [ ] **Step 4: `services/pipeline/README.md`.** Add a `PGSSLMODE` / `PGSSLROOTCERT` row: "libpq's TLS settings for `DATABASE_URL` (all pipeline connections, pypgstac included); see `docs/backend.md` 'Deployment: S3 credentials and database TLS'."

- [ ] **Step 5: `.env.example`.**
  - Above `# STAGING_S3_ACCESS_KEY_ID=minioadmin`, add a comment: unset = the default AWS credential chain (set both or neither; compose sets the MinIO pair).
  - Under `# PROCESS_CREDENTIAL_GRACE_SECONDS=300`, add `# Longest run credential the pipeline mints; 3600 on AWS (role chaining). Unset = no limit.` and `# PROCESS_MAX_CREDENTIAL_SECONDS=`. Skip this line if R-1 already added it after a rebase.

- [ ] **Step 6: `docs/FEATURES.md`.** Under "## Phase 8", add `### Deployment artifacts (R queue)` with `Spec: docs/superpowers/specs/2026-11-02-deployment-artifacts-design.md, ADR 0024; epic #136.` and a `| Slice | Status | Notes |` table. If R-1 created the subsection first, add only the row. Row: `R-2 · Pipeline + cube-server: S3 credential chain, RDS TLS, credential cap at mint | ✅ |` with notes covering:
  - blank `STAGING_S3_*` / `CUBE_S3_*` keys mean the default chain for boto3, STS, GDAL (via botocore) and Icechunk, keys come as a pair, and compose is unchanged;
  - a signed connection without keys is refused;
  - `PROCESS_MAX_CREDENTIAL_SECONDS` refusal at mint (`credential_budget_exceeded`, dead run, scans too, fixture `process-credential-budget.json`);
  - TLS via libpq `PGSSLMODE`/`PGSSLROOTCERT` (no code);
  - the library findings in `backend.md`.

- [ ] **Step 7: Run every gate**

Run:
- `cd services/pipeline && uv run pytest && uv run ruff check .`
- `cd infra/cube-server && uv run --extra dev pytest -q && uv run --extra dev ruff check .`
- `npm run verify` from the repo root.

Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add services/pipeline/tests/test_db_tls.py services/pipeline/README.md docs/backend.md docs/FEATURES.md .env.example
git commit -m "R-2: pipeline TLS pins; workload-identity library findings and deployment docs"
```

---

## PR notes for the lead

Body starts `Closes #138`. It lists:
- the gates above;
- the six Decisions;
- the library findings table (same content as `backend.md`);
- that no lead-only step remains beyond review.

Follow-ups to file, not fixed here:
- `session_policy` hardcodes `arn:aws:s3:::`, which breaks GovCloud (spec §9).
- `build_sts_client` signs STS with `signature_version="s3v4"`, which is untested against real STS (R-11).
- After K-4, the mint duration must add `max_queue_wait_seconds`, and the fixture's `one over` case flips to `mint_allowed: false`.
