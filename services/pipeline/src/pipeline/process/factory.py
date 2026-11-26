"""The process-run executor factory and its /health probe (K-5, spec §6).

``build_executor(settings)`` is the ONE place a process-run or image-scan
executor is built (``test_no_inline_executor_constructions``): the run path,
the reconciler, the watcher, the cancel listener, the reaper and the scan
drain all come through it. It does no I/O (K5-R18): only an unknown
``PROCESS_EXECUTOR`` fails startup (in ``Settings.from_env``); a missing API
URL fails the tick that needed the executor and shows in ``/health``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from pipeline.config import Settings
from pipeline.process.docker_executor import DockerExecutor
from pipeline.process.executor import Executor

#: How long /health trusts one probe (the readiness probe asks every 10 s).
EXECUTOR_PROBE_TTL_SECONDS = 30.0
#: One probe's budget.
EXECUTOR_PROBE_TIMEOUT_SECONDS = 5.0
_ERROR_CHARS = 300


def build_executor(settings: Settings) -> Executor:
    """``DockerExecutor`` or ``KubernetesExecutor`` per ``PROCESS_EXECUTOR``.
    The Kubernetes module is imported only when selected."""
    if settings.process_executor == "kubernetes":
        from pipeline.process.kubernetes_executor import KubernetesExecutor

        return KubernetesExecutor.from_settings(settings)
    return DockerExecutor(docker_host=settings.docker_host)


class ExecutorProbe:
    """``/health``'s ``executor`` key: ``{name, reachable, error}`` (K5-R17).
    Informational, like ``image_policy``: an unreachable executor never makes
    the pipeline unhealthy. Never raises; cached for ``ttl_seconds``."""

    def __init__(
        self,
        settings: Settings,
        *,
        ttl_seconds: float = EXECUTOR_PROBE_TTL_SECONDS,
        timeout_seconds: float = EXECUTOR_PROBE_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        build: Callable[[Settings], Executor] = build_executor,
    ) -> None:
        self._settings = settings
        self._ttl = ttl_seconds
        self._timeout = timeout_seconds
        self._clock = clock
        self._build = build
        self._cached: tuple[float, dict[str, Any]] | None = None

    async def __call__(self) -> dict[str, Any]:
        now = self._clock()
        if self._cached is not None and now - self._cached[0] < self._ttl:
            return self._cached[1]
        error: str | None = None
        try:
            executor = self._build(self._settings)
            await asyncio.wait_for(asyncio.to_thread(executor.probe), self._timeout)
        except TimeoutError:
            error = f"the executor probe timed out after {self._timeout:g}s"
        except Exception as err:
            error = str(err)[:_ERROR_CHARS] or type(err).__name__
        result = {"name": self._settings.process_executor, "reachable": error is None,
                  "error": error}
        self._cached = (now, result)
        return result
