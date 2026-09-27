"""Docker Engine API executor (ADR 0013 slice-1 backend, spec §4).

Talks the Engine API over HTTP to a **least-privilege socket proxy**
(`tecnativa/docker-socket-proxy` with ``CONTAINERS=1 IMAGES=1 POST=1`` (IMAGES
since C-2, for pull-by-digest), everything else off — P9-A verified `exec` and
`volumes` return 403 there). The pipeline never
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
import datetime as dt
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

from pipeline.images.reference import is_image_digest, is_image_reference
from pipeline.process.executor import (
    Executor,
    ExecutorUnavailable,
    ExitStatus,
    ImagePullFailed,
    LaunchedRun,
    RegistryAuth,
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

#: Labels stamped on every run container. `RUN_ID_LABEL` is load-bearing:
#: it is how the M3-W-1 reaper finds containers a dead worker left behind,
#: and the only thing that distinguishes ours from a stranger's.
RUN_ID_LABEL = "stac-higher.run-id"
PROCESS_ID_LABEL = "stac-higher.process-id"
#: C-2: what a container is ("process" | "image_scan"), so the reaper can
#: judge a scan against image_scans instead of process_runs.
RUN_KIND_LABEL = "stac-higher.run-kind"
#: C-2: a scan container names the image it scans.
IMAGE_ID_LABEL = "stac-higher.image-id"
#: Spec §3.2 / §14 decision 1: every user image runs as the platform
#: runtime's `runner` uid, whatever its own USER says.
USER_IMAGE_USER = "10001:10001"
_CONTAINER_NAME_PREFIX = {"process": "stac-run-", "image_scan": "stac-scan-"}

#: Docker reports a SIGKILLed container as 137. It cannot distinguish OUR
#: timeout kill from any other SIGKILL, which is why `wait` tracks the kill
#: itself rather than inferring it from the code.
SIGKILL_EXIT_CODE = 137


class UnsafeDockerHost(ExecutorUnavailable):
    """`DOCKER_HOST` points at a raw socket instead of the socket proxy."""


class EngineHTTPError(ExecutorUnavailable):
    """The daemon ANSWERED with an HTTP error. Distinct from unreachable:
    a 404 on an image inspect means "pull it", and a refused pull is the
    run's problem (ImagePullFailed), not the daemon's."""

    def __init__(self, message: str, *, status: int, detail: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail


def encode_registry_auth(auth: RegistryAuth) -> str:
    """The Engine API's ``X-Registry-Auth`` value: base64url JSON."""
    doc = {"username": auth.username, "password": auth.password, "serveraddress": auth.server}
    return base64.urlsafe_b64encode(json.dumps(doc).encode("utf-8")).decode("ascii")


def _pull_stream_error(payload: bytes) -> str | None:
    """A pull answers 200 and reports failure INSIDE its JSON-lines progress
    stream. The first error line wins; non-JSON lines are ignored."""
    for line in payload.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        detail = event.get("errorDetail")
        if isinstance(detail, dict) and detail.get("message"):
            return str(detail["message"])[:500]
        if event.get("error"):
            return str(event["error"])[:500]
    return None


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
    #: A pull streams progress, so this bounds each socket read, not the
    #: whole pull. Generous: a first pull of a large user image is slow.
    pull_timeout_seconds: int = 900

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
        headers: dict[str, str] | None = None,
    ) -> bytes | dict | list:
        url = f"{self._base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(
                request, timeout=timeout or self.request_timeout_seconds
            ) as response:
                payload = response.read()
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")[:500]
            raise EngineHTTPError(
                f"docker {method} {path} failed: {err.code} {detail}",
                status=err.code,
                detail=detail,
            ) from err
        except (urllib.error.URLError, OSError) as err:
            raise ExecutorUnavailable(
                f"docker {method} {path} unreachable at {self.docker_host}: {err}"
            ) from err
        if raw:
            return payload
        return json.loads(payload) if payload else {}

    def _ensure_image(self, image: str, auth: RegistryAuth | None) -> None:
        """Spec §8.4: a user image runs only by digest, pulled if absent.

        The inspect is by the pinned name, so "present" means THIS digest is
        on the daemon; a tag of the same repository is irrelevant.
        """
        reference, sep, digest = image.partition("@")
        if not sep or not is_image_reference(reference) or not is_image_digest(digest):
            raise ImagePullFailed(
                f"refusing to launch a user image not pinned by digest: {image!r}"
            )
        try:
            self._request("GET", f"/images/{urllib.parse.quote(image, safe='/:@')}/json")
            return
        except EngineHTTPError as err:
            if err.status == 404:
                pass
            elif err.status in (400, 422):
                # The daemon answered with something other than "not found"
                # or an infrastructure fault -- a malformed reference or
                # digest it refuses to even look up. That is the image's
                # problem, not the daemon's, so it spends a pull attempt
                # like any other ImagePullFailed rather than requeuing
                # forever as an outage.
                raise ImagePullFailed(
                    f"inspecting {image!r} failed: {err.status} {err.detail}"
                ) from err
            else:
                raise
        query = urllib.parse.urlencode({"fromImage": reference, "tag": digest})
        headers = {"X-Registry-Auth": encode_registry_auth(auth)} if auth is not None else None
        try:
            payload = self._request(
                "POST",
                f"/images/create?{query}",
                headers=headers,
                timeout=self.pull_timeout_seconds,
                raw=True,
            )
        except EngineHTTPError as err:
            raise ImagePullFailed(
                f"pulling {reference}@{digest} failed: {err.status} {err.detail}"
            ) from err
        assert isinstance(payload, bytes)
        error = _pull_stream_error(payload)
        if error:
            raise ImagePullFailed(f"pulling {reference}@{digest} failed: {error}")

    # -- Executor -----------------------------------------------------------

    def launch(self, spec: RunSpec) -> RunHandle:
        if spec.user_image:
            self._ensure_image(spec.image, spec.registry_auth)
        labels = {RUN_ID_LABEL: spec.run_id, RUN_KIND_LABEL: spec.kind}
        if spec.kind == "image_scan":
            labels[IMAGE_ID_LABEL] = spec.process_id
        else:
            labels[PROCESS_ID_LABEL] = spec.process_id
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
            "Labels": labels,
        }
        if spec.user_image:
            # Spec §3.2: the platform's hardening, not the image's. The forced
            # uid has no home, so /tmp is its one writable directory.
            config["User"] = USER_IMAGE_USER
            config["HostConfig"]["Tmpfs"] = {
                "/tmp": f"rw,nosuid,nodev,size={spec.memory_mb}m"
            }
        if spec.entrypoint:
            config["Entrypoint"] = list(spec.entrypoint)
        if spec.cmd:
            config["Cmd"] = list(spec.cmd)

        name = f"{_CONTAINER_NAME_PREFIX.get(spec.kind, 'stac-run-')}{spec.run_id}"
        created = self._request(
            "POST",
            f"/containers/create?name={urllib.parse.quote(name)}",
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


    def list_launched(self) -> list[LaunchedRun]:
        # `all=1` because an EXITED container is the orphan we are hunting;
        # the default listing shows only running ones and would miss every
        # container a worker died before removing.
        filters = urllib.parse.quote(json.dumps({"label": [RUN_ID_LABEL]}))
        payload = self._request("GET", f"/containers/json?all=1&filters={filters}")
        entries: list[LaunchedRun] = []
        for entry in payload if isinstance(payload, list) else []:
            run_id = (entry.get("Labels") or {}).get(RUN_ID_LABEL)
            container_id = entry.get("Id")
            # The daemon filtered for us, but a container without OUR label is
            # somebody else's workload and must never be reaped on our say-so.
            if not run_id or not container_id:
                continue
            created = entry.get("Created")
            labels = entry.get("Labels") or {}
            entries.append(
                LaunchedRun(
                    handle=RunHandle(id=container_id, backend=self.name),
                    run_id=run_id,
                    created_at=(
                        dt.datetime.fromtimestamp(created, dt.UTC)
                        if isinstance(created, int | float)
                        else None
                    ),
                    kind=labels.get(RUN_KIND_LABEL) or "process",
                )
            )
        return entries


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
