"""Probe a connection: run its adapter's ``test()`` and map the result to the
health/pin decisions both the drain and health-sweep jobs persist.

Two layers:

- :func:`run_adapter_test` — decrypt credentials, build the adapter, run
  ``test()``. Egress is enforced inside the adapter. Failures return a
  ``{ok: False, message}`` result; credentials/host keys are never logged or
  echoed.
- :func:`evaluate_test_outcome` — a **pure** function turning a protocol, the
  currently pinned host key, and a raw test result into a :class:`ProbeOutcome`
  (connection status, last_error, host key to pin, and the check ``result`` to
  store). TOFU lives here: an SSH-family host-key mismatch turns an otherwise-ok
  test into a hard failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pipeline.connections.adapters import TestResult
from pipeline.connections.adapters.tofu import TofuVerdict, evaluate_host_key
from pipeline.connections.build import AdapterBuildError, build_adapter
from pipeline.connections.egress import EgressBlocked
from pipeline.connections.repo import ConnectionRow

logger = logging.getLogger(__name__)

_SSH_FAMILY = frozenset({"ssh", "sftp"})


@dataclass
class ProbeOutcome:
    #: overall success after TOFU is applied
    ok: bool
    #: connection.status to persist ("ok" | "error")
    connection_status: str
    #: connection.last_error (None on success)
    last_error: str | None
    #: host key to pin on TOFU first-pin (else None)
    host_key_to_pin: str | None
    #: the jsonb to store in connection_checks.result
    result: TestResult


async def run_adapter_test(
    connection: ConnectionRow,
    master_key: bytes,
    allow_hosts: frozenset[str],
) -> TestResult:
    """Decrypt credentials, build the adapter, and run ``test()``.

    Never raises for an expected failure (bad key, blocked egress, adapter
    error) — those become ``{ok: False, message}``. Credentials never appear in
    the message or the logs.
    """
    try:
        adapter = build_adapter(connection, master_key, allow_hosts)
    except AdapterBuildError as exc:
        return {"ok": False, "message": str(exc)}

    try:
        return await adapter.test()
    except EgressBlocked as exc:
        return {"ok": False, "message": str(exc)}
    except Exception as exc:
        logger.warning(
            "adapter test raised",
            extra={"protocol": connection.protocol, "error_type": type(exc).__name__},
        )
        return {"ok": False, "message": f"test error: {type(exc).__name__}"}


def _fail(message: str, result: TestResult) -> ProbeOutcome:
    """A failed outcome: status=error, last_error=message, and the check result
    flipped to ``ok=False`` carrying ``message``."""
    failed: TestResult = {**result, "ok": False, "message": message}
    return ProbeOutcome(
        ok=False,
        connection_status="error",
        last_error=message,
        host_key_to_pin=None,
        result=failed,
    )


def evaluate_test_outcome(
    protocol: str,
    pinned_host_key: str | None,
    result: TestResult,
) -> ProbeOutcome:
    """Pure mapping from a raw test result to persisted health + pin decisions.

    - test failed              -> status=error, last_error=message.
    - test ok, non-SSH         -> status=ok.
    - test ok, SSH first-pin   -> status=ok, pin the observed key.
    - test ok, SSH match       -> status=ok.
    - test ok, SSH mismatch    -> HARD FAIL: status=error, result flipped to
                                  ok=False with the mismatch message.
    """
    if not result.get("ok"):
        return _fail(result.get("message", "connection test failed"), result)

    if protocol not in _SSH_FAMILY:
        return ProbeOutcome(
            ok=True,
            connection_status="ok",
            last_error=None,
            host_key_to_pin=None,
            result=result,
        )

    observed = result.get("host_key")
    if not observed:
        # SSH family must surface a host key; treat its absence as a failure
        # rather than silently pinning nothing.
        return _fail("SSH test returned no host key", result)

    decision = evaluate_host_key(pinned_host_key, observed)
    if decision.verdict is TofuVerdict.MISMATCH:
        return _fail(decision.message or "host key mismatch", result)

    return ProbeOutcome(
        ok=True,
        connection_status="ok",
        last_error=None,
        host_key_to_pin=decision.key_to_pin,  # set only on FIRST_PIN
        result=result,
    )


async def probe_connection(
    connection: ConnectionRow,
    master_key: bytes,
    allow_hosts: frozenset[str],
) -> ProbeOutcome:
    """Run the adapter test and evaluate its outcome (TOFU included)."""
    result = await run_adapter_test(connection, master_key, allow_hosts)
    return evaluate_test_outcome(connection.protocol, connection.host_key, result)
