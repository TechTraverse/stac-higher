"""Docker Engine API executor (ADR 0013 slice-1 backend, spec §4).

Talks the Engine API over HTTP to a **least-privilege socket proxy**
(`tecnativa/docker-socket-proxy` with ``CONTAINERS=1 POST=1`, everything else
off — P9-A verified `exec` and `volumes` return 403 there). The pipeline never
holds the raw socket, so the proxy is the auditable seam, and
:func:`assert_safe_docker_host` refuses to start against a raw socket at all.

Two deliberate departures from the obvious implementation, both narrowing the
residual risk ADR 0013 flagged (the proxy still permits arbitrary
``HostConfig`` on create):

1. **No bind mounts, ever.** ADR 0013 sketched mounting the revision's code
   read-only, but under docker-out-of-docker a bind path is resolved by the
   DAEMON, on the host — the pipeline container's filesystem is not the
   daemon's, so the mount would either fail or, worse, silently mount some
   unrelated host path. The code travels in the environment instead
   (base64, decoded by the image's entrypoint), which means the executor
   never needs the proxy to allow mounts and a compromised executor cannot
   ask for one.
2. **`NetworkMode` is always set explicitly**, defaulting to ``none``. A
   forgotten network field would otherwise attach runs to the daemon's
   default bridge — full outbound internet — which is exactly the egress
   boundary this executor exists to hold.

No docker SDK dependency: the Engine API calls used here are four plain HTTP
requests, and adding a client library for them would widen the dependency
surface of a security-critical component for no benefit.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

from pipeline.process.executor import (
    Executor,
    ExecutorUnavailable,
    ExitStatus,
    RunHandle,
    RunSpec,
)

logger = logging.getLogger(__name__)

#: Engine API version pinned in the URL path — the wire format we build
#: against, so a daemon upgrade cannot silently change field semantics.
API_VERSION = "v1.43"

#: Env var the platform runtime image's entrypoint decodes into the user's
#: module. See services/process-runtime/.
CODE_ENV_VAR = "STAC_HIGHER_PROCESS_CODE_B64"

#: Docker reports a SIGKILLed container as 137. It cannot distinguish OUR
#: timeout kill from any other SIGKILL, which is why `wait` tracks the kill
#: itself rather than inferring it from the code.
SIGKILL_EXIT_CODE = 137


class UnsafeDockerHost(ExecutorUnavailable):
    """`DOCKER_HOST` points at a raw socket instead of the socket proxy."""


def assert_safe_docker_host(docker_host: str) -> None:
    """Startup self-check (spec §14 risk list): refuse a raw-socket
    ``DOCKER_HOST``.

    Handing the pipeline the daemon socket directly would give any code path
    in the worker unrestricted control of the host's Docker — the exact
    exposure the socket proxy exists to remove. Failing loudly at startup is
    the point: a deployment that mounts the socket "just for now" must not
    quietly work.
    """
    scheme = urlparse(docker_host).scheme
    if scheme in ("unix", "npipe", ""):
        raise UnsafeDockerHost(
            f"DOCKER_HOST={docker_host!r} is a raw daemon socket. Point it at "
            "the least-privilege socket proxy (tcp://docker-socket-proxy:2375) "
            "— ADR 0013 requires the proxy to be the only client of the socket."
        )
    if scheme not in ("tcp", "http"):
        raise UnsafeDockerHost(
            f"DOCKER_HOST={docker_host!r} has an unsupported scheme {scheme!r}"
        )


@dataclass
class DockerExecutor(Executor):
    """One container per run, limits at create time, always reaped."""

    docker_host: str
    name: str = "docker"
    #: Per-request HTTP timeout. Deliberately NOT the run budget — `wait`
    #: long-polls the daemon in slices so a hung daemon surfaces promptly
    #: while a legitimately long run is not cut short.
    request_timeout_seconds: int = 30
    #: How long each /wait long-poll blocks before we re-check our own budget.
    poll_slice_seconds: int = 5

    def __post_init__(self) -> None:
        assert_safe_docker_host(self.docker_host)
        parsed = urlparse(self.docker_host)
        # tcp:// is docker's spelling of what is plain HTTP on the wire.
        self._base = f"http://{parsed.netloc}/{API_VERSION}"

    # -- Engine API plumbing ------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        timeout: int | None = None,
        raw: bool = False,
    ) -> bytes | dict | list:
        url = f"{self._base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(
                request, timeout=timeout or self.request_timeout_seconds
            ) as response:
                payload = response.read()
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")[:500]
            raise ExecutorUnavailable(
                f"docker {method} {path} failed: {err.code} {detail}"
            ) from err
        except (urllib.error.URLError, OSError) as err:
            raise ExecutorUnavailable(
                f"docker {method} {path} unreachable at {self.docker_host}: {err}"
            ) from err
        if raw:
            return payload
        return json.loads(payload) if payload else {}

    # -- Executor -----------------------------------------------------------

    def launch(self, spec: RunSpec) -> RunHandle:
        config = {
            "Image": spec.image,
            # Engine wants KEY=VALUE strings. `spec.env` is the COMPLETE
            # environment (ADR 0013) — nothing is inherited from the worker.
            "Env": [f"{k}={v}" for k, v in spec.env.items()],
            "HostConfig": {
                "Memory": spec.memory_mb * 1024 * 1024,
                # A run is batch work: no restart policy, ever. Restarting
                # user code that crashed would re-run side effects the ledger
                # already recorded as failed.
                "RestartPolicy": {"Name": "no"},
                # Explicit, always — see the module docstring.
                "NetworkMode": spec.network or "none",
                # AutoRemove is deliberately OFF: we need to read the exit
                # code and the logs AFTER exit, and ADR 0013 does not trust
                # the daemon to reap for us. `reap` is the contract.
                "AutoRemove": False,
                # Defence in depth against a container that escalates inside
                # its own namespace; costs nothing for our own image.
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
            },
            # Named so an operator looking at `docker ps` can tell what a
            # stray container was, and so the reap sweep can find orphans.
            "Labels": {
                "stac-higher.run-id": spec.run_id,
                "stac-higher.process-id": spec.process_id,
            },
        }
        if spec.cmd:
            config["Cmd"] = list(spec.cmd)

        created = self._request(
            "POST",
            f"/containers/create?name={urllib.parse.quote(f'stac-run-{spec.run_id}')}",
            body=config,
        )
        container_id = created.get("Id") if isinstance(created, dict) else None
        if not container_id:
            raise ExecutorUnavailable(f"docker create returned no Id: {created!r}")

        try:
            self._request("POST", f"/containers/{container_id}/start")
        except ExecutorUnavailable:
            # A created-but-unstarted container is exactly the orphan `reap`
            # exists for; leaving it behind would leak a container per failed
            # start.
            self.reap(RunHandle(id=container_id, backend=self.name))
            raise
        return RunHandle(id=container_id, backend=self.name)

    def wait(self, handle: RunHandle, timeout_seconds: int) -> ExitStatus:
        import time

        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # Long-poll in slices: the daemon blocks until exit, and we cap
            # each block so our own budget stays authoritative even if the
            # daemon never answers.
            slice_seconds = int(min(self.poll_slice_seconds, remaining)) or 1
            try:
                result = self._request(
                    "POST",
                    f"/containers/{handle.id}/wait",
                    timeout=slice_seconds + 1,
                )
            except ExecutorUnavailable as err:
                # A read timeout on a long-poll is the normal "still running"
                # answer, not a backend failure; anything else is real.
                if "timed out" not in str(err).lower():
                    return ExitStatus(exit_code=-1, error=str(err))
                continue
            if isinstance(result, dict) and "StatusCode" in result:
                return ExitStatus(exit_code=int(result["StatusCode"]))

        # Budget spent. Killing is what makes the limit real (ADR 0013:
        # limits are enforced by the executor, not trusted to the code).
        try:
            self._request("POST", f"/containers/{handle.id}/kill")
        except ExecutorUnavailable as err:
            logger.warning(
                "process run kill failed",
                extra={"run": handle.id, "error": str(err)},
            )
        return ExitStatus(exit_code=SIGKILL_EXIT_CODE, timed_out=True)

    def logs(self, handle: RunHandle, max_bytes: int) -> bytes:
        query = "stdout=1&stderr=1&timestamps=0"
        try:
            payload = self._request(
                "GET", f"/containers/{handle.id}/logs?{query}", raw=True
            )
        except ExecutorUnavailable as err:
            # Losing the log must never lose the run's VERDICT — the exit
            # status is already known by the time this is called.
            logger.warning(
                "process run logs unavailable",
                extra={"run": handle.id, "error": str(err)},
            )
            return b""
        assert isinstance(payload, bytes)
        return _demultiplex(payload)[:max_bytes]

    def reap(self, handle: RunHandle) -> None:
        # An already-exited container is the common case, not a failure.
        with contextlib.suppress(ExecutorUnavailable):
            self._request("POST", f"/containers/{handle.id}/kill")
        try:
            self._request("DELETE", f"/containers/{handle.id}?force=1&v=1")
        except ExecutorUnavailable as err:
            # Idempotent by contract: an already-gone container is success.
            logger.warning(
                "process run reap failed",
                extra={"run": handle.id, "error": str(err)},
            )


def _demultiplex(payload: bytes) -> bytes:
    """Strip Docker's 8-byte stream framing from a non-TTY log stream.

    Without a TTY the daemon frames each chunk as
    ``[stream_byte, 0,0,0, big-endian length]`` + payload. Handing those
    headers to an operator as "the log" would put binary noise in every line;
    a stream that is not framed (TTY mode, or an empty body) is returned as
    is rather than mangled.
    """
    out = bytearray()
    offset = 0
    length = len(payload)
    while offset + 8 <= length:
        stream = payload[offset]
        if stream not in (0, 1, 2):
            return payload  # not framed — leave it alone
        size = int.from_bytes(payload[offset + 4 : offset + 8], "big")
        offset += 8
        out += payload[offset : offset + size]
        offset += size
    return bytes(out) if out else payload


def encode_code(code: str) -> str:
    """Base64 the revision's source for the entrypoint to decode (see the
    module docstring on why the code travels in the environment)."""
    return base64.b64encode(code.encode("utf-8")).decode("ascii")
