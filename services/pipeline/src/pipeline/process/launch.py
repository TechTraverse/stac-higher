"""Assemble and run one process run (spec §4/§5, ADR 0013).

This is the module that turns "a revision" into "a container that ran", and
it is where the ADR 0013 invariants are actually made true:

- The run's environment is BUILT here, from scratch. It contains the
  revision's resolved env, the run-scoped storage credentials, and the code —
  and nothing else. There is no path by which the worker's own environment
  reaches the container.
- Credentials are minted BEFORE launch and their failure aborts the run, so
  there is no window where a container exists without a bounded credential.
- ``reap`` runs in a ``finally``. A crash between launch and reap would
  otherwise leak a container per run.

M5-C owns the ledger: this returns an outcome, it does not write
``process_runs``. Keeping the two apart is what lets the run path be tested
without a database and the ledger be tested without Docker.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pipeline.config import Settings
from pipeline.metrics import PROCESS_RUN_SECONDS
from pipeline.process.config import NETWORK_LEVELS, EnvEntry, ProcessRuntime
from pipeline.process.credentials import RunCredentials, mint_run_credentials
from pipeline.process.docker_executor import CODE_ENV_VAR, encode_code
from pipeline.process.executor import Executor, ExitStatus, RunSpec
from pipeline.process.logs import store_run_log

logger = logging.getLogger(__name__)


class SecretResolutionError(Exception):
    """A ``secret_ref`` env entry could not be resolved — the run must not
    start, because user code would otherwise silently see an unset variable
    where a credential was intended."""


class NetworkCapExceeded(Exception):
    """The revision asks for more network than this deployment permits —
    configuration, so the run dies rather than retries."""


def check_network_cap(runtime: ProcessRuntime, settings: Settings) -> None:
    """GOES spec §4: a revision above ``PROCESS_NETWORK_MAX`` never launches
    at a lower level silently — it fails, naming the level and the cap. The
    pipeline enforces this independently of the app's write gate."""
    if NETWORK_LEVELS.index(runtime.network_level) > NETWORK_LEVELS.index(
        settings.process_network_max
    ):
        raise NetworkCapExceeded(
            f"revision requests network level {runtime.network_level!r} but this "
            f"deployment allows at most {settings.process_network_max!r} (PROCESS_NETWORK_MAX)"
        )


@dataclass(frozen=True)
class RunOutcome:
    status: ExitStatus
    #: Object key of the captured log, or None when the log could not be
    #: stored (a lost log never loses the verdict — §9).
    log_ref: str | None
    credentials_prefix: str


def resolve_env(
    entries: tuple[EnvEntry, ...],
    resolve_secret,
) -> dict[str, str]:
    """Turn the §5.6 env envelope into concrete variables.

    ``resolve_secret(ref) -> str`` is injected rather than imported: secret
    material must decrypt only in the worker at job time (ROADMAP §5.2), and
    passing the resolver in keeps this module free of the master key entirely
    — it can be unit-tested with a stub that never touches crypto.
    """
    resolved: dict[str, str] = {}
    for entry in entries:
        if entry.secret_ref is not None:
            try:
                resolved[entry.name] = resolve_secret(entry.secret_ref)
            except Exception as err:  # all failures here are fatal by design
                # The REF is safe to name; the value never appears in a log.
                raise SecretResolutionError(
                    f"env {entry.name}: could not resolve secret_ref "
                    f"(connection {entry.secret_ref.connection_id}, "
                    f"key {entry.secret_ref.key}): {type(err).__name__}"
                ) from err
        else:
            resolved[entry.name] = entry.value or ""
    return resolved


def build_run_spec(
    settings: Settings,
    *,
    run_id: str,
    process_id: str,
    runtime: ProcessRuntime,
    code: str,
    env: dict[str, str],
    credentials: RunCredentials,
    extra_env: Mapping[str, str] | None = None,
) -> RunSpec:
    """The COMPLETE environment of a run, assembled in one place.

    Order matters: platform-controlled values are applied AFTER the revision's
    own env, so a revision cannot shadow its storage credentials, its code,
    or the input pointers (``STAC_HIGHER_INPUT_*``, passed as ``extra_env``
    — GOES spec §3.1) by declaring variables with those names.
    """
    run_env = dict(env)
    run_env.update(extra_env or {})  # platform-provided input pointers
    run_env.update(credentials.as_env())
    run_env[CODE_ENV_VAR] = encode_code(code)
    run_env["STAC_HIGHER_RUN_ID"] = run_id
    run_env["STAC_HIGHER_PROCESS_ID"] = process_id

    return RunSpec(
        run_id=run_id,
        process_id=process_id,
        # Slice 1 always runs the platform image: `container` runtimes are
        # refused at the app's write gate, so a revision carrying one is a
        # contract violation rather than something to honour here.
        image=settings.process_runtime_image,
        env=run_env,
        memory_mb=runtime.memory_mb,
        timeout_seconds=runtime.timeout_seconds,
        # Every accepted network profile level is realised as the deployment's
        # process network today: levels above `isolated` are refused by
        # `check_network_cap` until the egress proxy (GOES spec §11) exists.
        network=settings.process_network,
    )


def execute_run(
    executor: Executor,
    settings: Settings,
    storage_client,
    *,
    run_id: str,
    process_id: str,
    runtime: ProcessRuntime,
    code: str,
    env_entries: tuple[EnvEntry, ...],
    resolve_secret,
    sts_client=None,
    read_prefixes: Sequence[str] = (),
    extra_env: Mapping[str, str] | None = None,
) -> RunOutcome:
    """Mint credentials, launch, wait, capture the log, reap. Always reap.

    ``read_prefixes`` are the source collections' canonical prefixes the
    session policy grants read on; ``extra_env`` is the input env (§3.1).
    """
    env = resolve_env(env_entries, resolve_secret)
    credentials = mint_run_credentials(
        settings,
        run_id,
        runtime.timeout_seconds,
        sts_client=sts_client,
        read_prefixes=read_prefixes,
    )
    spec = build_run_spec(
        settings,
        run_id=run_id,
        process_id=process_id,
        runtime=runtime,
        code=code,
        env=env,
        credentials=credentials,
        extra_env=extra_env,
    )

    import time

    started = time.monotonic()
    handle = executor.launch(spec)
    try:
        status = executor.wait(handle, runtime.timeout_seconds)
        payload = executor.logs(handle, settings.process_log_max_bytes)
    finally:
        executor.reap(handle)
        # Observed even on the failure path: a timing histogram that only ever
        # sees successes would understate what runs actually cost.
        PROCESS_RUN_SECONDS.observe(time.monotonic() - started)

    log_ref = store_run_log(
        storage_client,
        settings.staging_bucket,
        process_id,
        run_id,
        payload,
        settings.process_log_max_bytes,
    )
    # Structured fields only — the run's own output is in the log OBJECT and
    # is never interpolated into a platform log line (ADR 0013).
    logger.info(
        "process run finished",
        extra={
            "run_id": run_id,
            "process_id": process_id,
            "exit_code": status.exit_code,
            "timed_out": status.timed_out,
            "log_ref": log_ref,
        },
    )
    return RunOutcome(
        status=status, log_ref=log_ref, credentials_prefix=credentials.prefix
    )
