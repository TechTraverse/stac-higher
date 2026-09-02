"""Process executor boundary (M5-B, ADR 0013).

These tests are about the INVARIANTS, not about Docker. The real
DockerExecutor's HTTP layer is stubbed; what is asserted is the security
posture the ADR requires: no raw socket, an explicit network, complete-and-
only-intended environment, enforced limits, and reaping on every path.
"""

from __future__ import annotations

import json

import pytest

from pipeline.config import Settings
from pipeline.process.config import EnvEntry, ProcessRuntime, SecretRef
from pipeline.process.credentials import (
    RunCredentials,
    RunCredentialsError,
    mint_run_credentials,
    session_policy,
)
from pipeline.process.docker_executor import (
    CODE_ENV_VAR,
    SIGKILL_EXIT_CODE,
    DockerExecutor,
    UnsafeDockerHost,
    _demultiplex,
    assert_safe_docker_host,
    encode_code,
)
from pipeline.process.executor import ExecutorUnavailable, ExitStatus, RunHandle
from pipeline.process.launch import (
    SecretResolutionError,
    build_run_spec,
    execute_run,
    resolve_env,
)
from pipeline.process.logs import TRUNCATION_MARKER, cap, store_run_log
from pipeline.process.memory_executor import MemoryExecutor
from pipeline.storage.keys import (
    InvalidKeySegment,
    run_input_asset_key,
    run_input_manifest_key,
    run_inputs_prefix,
    run_log_key,
    run_staging_prefix,
)

RUN = "11111111-1111-4111-8111-111111111111"
PROC = "22222222-2222-4222-8222-222222222222"


def settings(**overrides) -> Settings:
    return Settings.from_env(
        {"STAGING_BUCKET": "stac-higher", **{k: str(v) for k, v in overrides.items()}}
    )


# ---------------------------------------------------------------------------
# the startup self-check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "unix:///var/run/docker.sock",
        "/var/run/docker.sock",
        "npipe:////./pipe/docker_engine",
    ],
)
def test_raw_docker_socket_is_refused(host):
    """A raw socket is unrestricted control of the host daemon, held by the
    same process that holds the DB URL and the master key. Failing at startup
    is the point: mounting the socket 'just for now' must not quietly work."""
    with pytest.raises(UnsafeDockerHost):
        assert_safe_docker_host(host)


def test_socket_proxy_host_is_accepted():
    # Returns None; the assertion that matters is that it does not raise.
    assert_safe_docker_host("tcp://docker-socket-proxy:2375")


def test_executor_refuses_to_construct_against_a_raw_socket():
    with pytest.raises(UnsafeDockerHost):
        DockerExecutor(docker_host="unix:///var/run/docker.sock")


# ---------------------------------------------------------------------------
# DockerExecutor create-time posture
# ---------------------------------------------------------------------------


class FakeApi:
    """Records Engine API calls and replays scripted responses."""

    def __init__(self, responses=None):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.responses = responses or {}

    def __call__(self, method, path, *, body=None, timeout=None, raw=False):
        self.calls.append((method, path, body))
        for key, value in self.responses.items():
            if key in path:
                if isinstance(value, Exception):
                    raise value
                return value
        return b"" if raw else {}

    def created_config(self) -> dict:
        return next(b for m, p, b in self.calls if "create" in p)


def executor_with(api) -> DockerExecutor:
    ex = DockerExecutor(docker_host="tcp://proxy:2375")
    ex._request = api  # type: ignore[method-assign]
    return ex


def test_launch_sets_limits_network_and_never_mounts():
    api = FakeApi({"create": {"Id": "c1"}})
    ex = executor_with(api)
    spec = build_run_spec(
        settings(),
        run_id=RUN,
        process_id=PROC,
        runtime=ProcessRuntime(kind="inline_python", memory_mb=256, timeout_seconds=60),
        code="print(1)",
        env={"USER_VAR": "x"},
        credentials=RunCredentials(
            "AK", "SK", "TOK", "stac-higher", run_staging_prefix(RUN), None, "us-east-1"
        ),
    )
    ex.launch(spec)

    host = api.created_config()["HostConfig"]
    assert host["Memory"] == 256 * 1024 * 1024
    # Explicit always — a missing NetworkMode would attach the run to the
    # daemon's default bridge, i.e. full outbound internet.
    assert host["NetworkMode"] == "none"
    assert host["RestartPolicy"] == {"Name": "no"}
    assert host["CapDrop"] == ["ALL"]
    assert "no-new-privileges" in host["SecurityOpt"]
    # AutoRemove is off: we read the exit code and logs AFTER exit, and the
    # ADR does not trust the daemon to reap.
    assert host["AutoRemove"] is False
    # The residual risk the socket proxy leaves open is bind mounts; our
    # executor must never ask for one.
    assert "Binds" not in host and "Mounts" not in host


def test_a_failed_start_reaps_the_created_container():
    """Otherwise every failed start leaks a container."""
    api = FakeApi(
        {"create": {"Id": "c1"}, "start": ExecutorUnavailable("daemon said no")}
    )
    ex = executor_with(api)
    with pytest.raises(ExecutorUnavailable):
        ex.launch(
            build_run_spec(
                settings(),
                run_id=RUN,
                process_id=PROC,
                runtime=ProcessRuntime(kind="inline_python"),
                code="x",
                env={},
                credentials=RunCredentials(
                    "AK", "SK", "TOK", "b", run_staging_prefix(RUN), None, "r"
                ),
            )
        )
    assert any(m == "DELETE" for m, _p, _b in api.calls)


def test_wait_kills_and_reports_timed_out_when_the_budget_is_spent():
    # The daemon never answers with a StatusCode, so the budget expires.
    api = FakeApi({"wait": {}})
    ex = executor_with(api)
    ex.poll_slice_seconds = 1
    status = ex.wait(RunHandle(id="c1"), timeout_seconds=1)
    assert status.timed_out is True
    assert status.exit_code == SIGKILL_EXIT_CODE
    assert status.ok is False
    # The kill is what makes the limit real rather than advisory.
    assert any("kill" in p for _m, p, _b in api.calls)


def test_wait_returns_a_nonzero_exit_as_a_RESULT_not_an_error():
    api = FakeApi({"wait": {"StatusCode": 1}})
    status = executor_with(api).wait(RunHandle(id="c1"), timeout_seconds=30)
    assert status == ExitStatus(exit_code=1)
    assert status.ok is False


def test_logs_survive_an_unavailable_daemon():
    """A lost log must never lose the verdict — the exit status is already
    known by the time logs are read."""
    api = FakeApi({"logs": ExecutorUnavailable("gone")})
    assert executor_with(api).logs(RunHandle(id="c1"), 1024) == b""


def test_reap_is_idempotent_for_an_already_gone_container():
    api = FakeApi(
        {"kill": ExecutorUnavailable("no such container"),
         "c1?force": ExecutorUnavailable("no such container")}
    )
    executor_with(api).reap(RunHandle(id="c1"))  # must not raise


def test_log_framing_is_stripped_but_unframed_output_is_left_alone():
    framed = b"\x01\x00\x00\x00\x00\x00\x00\x05hello"
    assert _demultiplex(framed) == b"hello"
    assert _demultiplex(b"plain text") == b"plain text"


# ---------------------------------------------------------------------------
# the environment is complete AND minimal
# ---------------------------------------------------------------------------


def test_run_env_contains_only_intended_values():
    """ADR 0013's central invariant: user code never sees the platform's DB,
    master key, or object-store keys."""
    creds = RunCredentials(
        "AK", "SK", "TOK", "stac-higher", run_staging_prefix(RUN), None, "us-east-1"
    )
    spec = build_run_spec(
        settings(),
        run_id=RUN,
        process_id=PROC,
        runtime=ProcessRuntime(kind="inline_python"),
        code="print(1)",
        env={"TILE_SIZE": "512"},
        credentials=creds,
    )
    assert spec.env["TILE_SIZE"] == "512"
    assert spec.env["AWS_SESSION_TOKEN"] == "TOK"
    assert spec.env[CODE_ENV_VAR] == encode_code("print(1)")
    for forbidden in (
        "DATABASE_URL",
        "CREDENTIALS_MASTER_KEY",
        "STAGING_S3_ACCESS_KEY_ID",
        "STAGING_S3_SECRET_ACCESS_KEY",
    ):
        assert forbidden not in spec.env


def test_a_revision_cannot_shadow_its_own_credentials_or_code():
    """Platform values are applied last, so a hostile revision declaring
    AWS_SESSION_TOKEN cannot swap in one of its own."""
    spec = build_run_spec(
        settings(),
        run_id=RUN,
        process_id=PROC,
        runtime=ProcessRuntime(kind="inline_python"),
        code="real",
        env={"AWS_SESSION_TOKEN": "attacker", CODE_ENV_VAR: encode_code("evil")},
        credentials=RunCredentials(
            "AK", "SK", "REAL", "b", run_staging_prefix(RUN), None, "r"
        ),
    )
    assert spec.env["AWS_SESSION_TOKEN"] == "REAL"
    assert spec.env[CODE_ENV_VAR] == encode_code("real")


def test_slice_1_always_runs_the_platform_image():
    """A `container` revision is refused at the app's write gate, so one
    reaching here is a contract violation, not something to honour."""
    spec = build_run_spec(
        settings(),
        run_id=RUN,
        process_id=PROC,
        runtime=ProcessRuntime(kind="container", image="ghcr.io/evil/x:1"),
        code="print(1)",
        env={},
        credentials=RunCredentials(
            "AK", "SK", "TOK", "b", run_staging_prefix(RUN), None, "r"
        ),
    )
    assert spec.image == settings().process_runtime_image


def test_secret_refs_resolve_through_the_injected_resolver():
    entries = (
        EnvEntry(name="PLAIN", value="1"),
        EnvEntry(name="TOKEN", secret_ref=SecretRef(connection_id=PROC, key="password")),
    )
    env = resolve_env(entries, lambda ref: f"secret-for-{ref.key}")
    assert env == {"PLAIN": "1", "TOKEN": "secret-for-password"}


def test_an_unresolvable_secret_aborts_the_run_without_leaking_the_value():
    entries = (
        EnvEntry(name="TOKEN", secret_ref=SecretRef(connection_id=PROC, key="password")),
    )

    def boom(_ref):
        raise ValueError("super-secret-plaintext")

    with pytest.raises(SecretResolutionError) as err:
        resolve_env(entries, boom)
    # The ref is safe to name; the underlying value never is.
    assert "password" in str(err.value)
    assert "super-secret-plaintext" not in str(err.value)


# ---------------------------------------------------------------------------
# run-scoped credentials
# ---------------------------------------------------------------------------


def test_session_policy_is_bounded_to_the_run_prefix():
    prefix = run_staging_prefix(RUN)
    policy = session_policy("stac-higher", prefix)
    objects, listing = policy["Statement"]
    assert objects["Resource"] == [f"arn:aws:s3:::stac-higher/{prefix}*"]
    # ListBucket is evaluated on the bucket, so it must be CONDITIONED on the
    # prefix or a run could enumerate the whole bucket.
    assert listing["Resource"] == ["arn:aws:s3:::stac-higher"]
    assert listing["Condition"]["StringLike"]["s3:prefix"] == [f"{prefix}*"]
    # Nothing grants access outside the prefix.
    assert not any(
        r.endswith("/*") and prefix not in r
        for st in policy["Statement"]
        for r in st["Resource"]
    )


class FakeSts:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.kwargs = None

    def assume_role(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


def test_credentials_are_minted_for_the_run_prefix_with_a_bounded_lifetime():
    sts = FakeSts(
        {"Credentials": {"AccessKeyId": "A", "SecretAccessKey": "S", "SessionToken": "T"}}
    )
    creds = mint_run_credentials(settings(), RUN, 900, sts_client=sts)
    assert creds.prefix == run_staging_prefix(RUN)
    # timeout + grace, so a run using its full budget can finish an upload.
    assert sts.kwargs["DurationSeconds"] == 900 + 300
    assert RUN in sts.kwargs["RoleSessionName"]
    policy = json.loads(sts.kwargs["Policy"])
    assert creds.prefix in policy["Statement"][0]["Resource"][0]


def test_sts_failure_never_falls_back_to_platform_keys():
    """The tempting fallback would make the boundary a lie in exactly the
    environment where it is first exercised (spec §5)."""
    sts = FakeSts(error=RuntimeError("no STS here"))
    with pytest.raises(RunCredentialsError) as err:
        mint_run_credentials(settings(), RUN, 60, sts_client=sts)
    assert "NOT a fallback" in str(err.value)


def test_incomplete_sts_response_refuses_to_start_the_run():
    sts = FakeSts({"Credentials": {"AccessKeyId": "A", "SecretAccessKey": "S"}})
    with pytest.raises(RunCredentialsError) as err:
        mint_run_credentials(settings(), RUN, 60, sts_client=sts)
    assert "SessionToken" in str(err.value)


# ---------------------------------------------------------------------------
# logs + keys
# ---------------------------------------------------------------------------


def test_log_cap_includes_the_marker_so_the_ceiling_is_real():
    capped = cap(b"x" * 5000, 200)
    assert len(capped) == 200
    assert capped.endswith(TRUNCATION_MARKER)


def test_short_logs_are_untouched():
    assert cap(b"hello", 1024) == b"hello"


class FakeStore:
    def __init__(self, fail=False):
        self.fail = fail
        self.written: list[tuple[str, bytes]] = []

    def put_object(self, **kwargs):
        if self.fail:
            raise RuntimeError("bucket gone")
        self.written.append((kwargs["Key"], kwargs["Body"]))


def test_store_run_log_returns_the_key_for_log_ref():
    store = FakeStore()
    key = store_run_log(store, "b", PROC, RUN, b"out", 1024)
    assert key == run_log_key(PROC, RUN) == f"logs/runs/{PROC}/{RUN}.log"
    assert store.written[0][1] == b"out"


def test_a_failed_log_write_does_not_fail_the_run():
    assert store_run_log(FakeStore(fail=True), "b", PROC, RUN, b"out", 1024) is None


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", "."])
def test_run_keys_reject_traversal(bad):
    with pytest.raises(InvalidKeySegment):
        run_staging_prefix(bad)
    with pytest.raises(InvalidKeySegment):
        run_log_key(PROC, bad)


# ---------------------------------------------------------------------------
# the full launch path
# ---------------------------------------------------------------------------


def test_execute_run_reaps_even_when_the_run_fails():
    executor = MemoryExecutor(results=[ExitStatus(exit_code=1)], log_output=b"traceback")
    store = FakeStore()
    sts = FakeSts(
        {"Credentials": {"AccessKeyId": "A", "SecretAccessKey": "S", "SessionToken": "T"}}
    )
    outcome = execute_run(
        executor,
        settings(),
        store,
        run_id=RUN,
        process_id=PROC,
        runtime=ProcessRuntime(kind="inline_python", timeout_seconds=60),
        code="raise SystemExit(1)",
        env_entries=(),
        resolve_secret=lambda ref: "",
        sts_client=sts,
    )
    assert outcome.status.exit_code == 1
    assert outcome.log_ref == run_log_key(PROC, RUN)
    assert outcome.credentials_prefix == run_staging_prefix(RUN)
    # A crash between launch and reap would leak a container per run.
    assert executor.reaped == [f"mem-{RUN}"]


def test_execute_run_never_launches_when_credentials_fail():
    """There must be no window in which a container exists without a bounded
    credential."""
    executor = MemoryExecutor()
    with pytest.raises(RunCredentialsError):
        execute_run(
            executor,
            settings(),
            FakeStore(),
            run_id=RUN,
            process_id=PROC,
            runtime=ProcessRuntime(kind="inline_python"),
            code="print(1)",
            env_entries=(),
            resolve_secret=lambda ref: "",
            sts_client=FakeSts(error=RuntimeError("no sts")),
        )
    assert executor.launched == []


# ---------------------------------------------------------------------------
# the run's inputs area (GOES spec §3)
# ---------------------------------------------------------------------------


def test_input_keys_nest_under_the_run_prefix():
    assert run_inputs_prefix(RUN) == f"staging/runs/{RUN}/inputs/"
    assert run_input_manifest_key(RUN, "b1") == f"staging/runs/{RUN}/inputs/b1/manifest.json"
    assert (
        run_input_asset_key(RUN, "b1", "item-1", "OR_ABI.nc")
        == f"staging/runs/{RUN}/inputs/b1/item-1/OR_ABI.nc"
    )


def test_input_asset_key_sanitizes_the_filename_and_refuses_bad_ids():
    assert run_input_asset_key(RUN, "b1", "i", "../x y.nc").endswith("/i/x_y.nc")
    with pytest.raises(InvalidKeySegment):
        run_input_asset_key(RUN, "b1", "a/b", "f.nc")
    with pytest.raises(InvalidKeySegment):
        run_input_manifest_key(RUN, "b 1")
