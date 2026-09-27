# C-2 · Scanner Image, Scan Drain and Digest-Pinned Launch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make user images real. A platform-built scanner image (Syft + Grype, DB baked) scans an image as a platform run through the `Executor` seam, a one-minute drain turns `image_scans` rows into verdicts and image statuses, and a kind 2/3 run launches only on an approved-or-flagged, fresh, digest-equal image, pulled by digest and run as uid 10001 with a `/tmp` tmpfs.

**Architecture:** Everything is pipeline-side plus one new image. The executor seam grows four `RunSpec` fields (`kind`, `user_image`, `entrypoint`, `registry_auth`), and `DockerExecutor` enforces the user-image posture and pulls by digest through the Engine API behind the socket proxy's new `IMAGES=1`. The launch path's `resolve_run_image` re-checks the `container_images` row at launch (the C-1 guard's body becomes the §8.4 check). The drain claims one pending scan (a deployment-wide cap of `IMAGE_SCAN_CONCURRENCY`, default 1), launches `services/image-scanner` with run-scoped STS credentials for `scans/{image_id}/{scan_id}/`, reads `result.json` back as untrusted data, runs C-1's `evaluate()`, and transitions the image. The scanner is stdlib Python + boto3 around the two binaries; its `top` list is chosen by the policy's rules so C-1's `evaluate()` can name what blocks. **No app code changes, no contract-fixture change, no DDL.**

**Tech Stack:** Python 3.12 stdlib + pytest + ruff (pipeline and scanner); the Docker Engine API over the socket proxy; Syft 1.52.0 and Grype 0.119.0 release binaries; compose; GitHub Actions `containers.yml`.

**Spec:** `docs/superpowers/specs/2026-09-13-container-images-scanning-design.md` §6, §8.1, §8.4, §8.5, §11 and the C-2 line in §15 (§2 and §14 are settled; do not reopen them). ADR `docs/decisions/0021-user-images-scan-then-approve.md`. Epic #56, issue #51. Merged predecessor: `docs/superpowers/plans/2026-09-27-c1-image-contracts.md` (read its "Decisions made in this plan", especially 1, 2, 8, 11 and 17).

## Global Constraints

- **Worktree:** `.claude/worktrees/c2-scanner-drain`, branch `feat/c2-scanner-drain` (GitHub issue #51, epic #56), off `main` at `af82cab` (C-1 merged). `npm install` is done. Never work in the main checkout or another worktree.
- **Gates:** every task ends with `npm run verify` from the worktree root AND `cd services/pipeline && uv run pytest -q && uv run ruff check .`. Teammates never run e2e, the dev server, Docker (image builds included), or push.
- **Python text must avoid en/em dashes and other ambiguous unicode** in strings, comments and docstrings (ruff RUF001-RUF003). Use `-`, `->`, `<=`, `>=`, `x`. The `§` sign is fine (it is already used throughout `pipeline/images/`).
- **No DDL.** Migration 030 (C-1) already has every column C-2 writes. 029 is reserved for K-3 (#11), 031 for K-4 (#12). If a task seems to need DDL, STOP and report it: the lead reserves numbers.
- **No new npm or Python dependency.** Syft and Grype live only in `services/image-scanner/`. The scanner installs `boto3==1.43.49`, the exact pin the platform runtime image already carries (`services/process-runtime/Dockerfile`); Task 6 pins them equal with a test.
- **No app change.** Nothing under `app/` is edited. C-3 (#52) runs in parallel and owns `app/src/lib/images/*`, the routes and the UI.
- **No contract-fixture change.** `tests/contract-fixtures/*` stay as C-1 left them. A failed scan keeps the C-1 shape: `{version, kind, reference, tag, error}` (Decision 1).
- **Structured logging:** constant messages, data in `extra={...}`. Never log, echo or put in an error message a registry password, token or the `X-Registry-Auth` header.
- **Spec vocabulary, verbatim:** image statuses `pending | scanning | approved | rejected | flagged | revoked | scan_failed`; scan kinds `admission | rescan`; scan statuses `pending | running | done | failed`; launch statuses `approved | flagged`; gate reasons `image_not_approved | image_stale | image_group_mismatch | image_digest_mismatch`.
- **The forced user is `10001:10001`** for kinds 2 and 3; `command` maps to Docker `Cmd` only, never `Entrypoint` or `User` (spec §3, §14 decisions 1 and 4).
- **Commit trailer.** Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  ```

## Review Focus

The inputs and failure modes a person will meet that the spec implies but no happy-path test exercises. Each line names the task that carries the test.

1. **A kind 2/3 process whose image went `flagged` keeps running; one whose image is stale, revoked or re-pointed dies with a reason.** Decision 4 of the spec: a Sunday-night CVE must not halt production, but the FedRAMP window is a hard line (Task 5 tests "flagged still launches", "stale dies", "revoked dies", "digest mismatch dies").
2. **A scanner that lies.** A result naming another reference, an `sbom_ref`/`findings_ref` outside its own scan prefix, or another platform must fail the scan, never approve the row (Task 7 test "a scanner that reports another reference is not believed").
3. **A scanner that crashes, is OOM-killed or times out leaves no `result.json`.** The scan row must still end `failed` with an identity-bearing result the C-1 readers accept, and the image must go `scan_failed`, not sit in `scanning` (Task 7 test "no result.json still fails the scan with a readable result").
4. **A registry that redirects blob reads to a CDN must not receive the bearer token there, and an `http://` redirect or token realm is refused** (Task 6 tests on `_SafeRedirect` and the token realm).
5. **The orphan reaper must never kill a scan in flight.** Scanner containers carry a run-kind label and are judged against `image_scans`, not `process_runs` (Task 1 test "a running scan container is left alone").

## File map

| File | Task | Responsibility |
|---|---|---|
| `services/pipeline/src/pipeline/process/executor.py` | 1 | `RunSpec.kind/user_image/entrypoint/registry_auth`, `RegistryAuth`, `ImagePullFailed`, `LaunchedRun.kind` |
| `services/pipeline/src/pipeline/process/docker_executor.py` | 1 | forced uid + tmpfs, entrypoint, pull by digest, run-kind labels, `EngineHTTPError` |
| `services/pipeline/src/pipeline/process/memory_executor.py` | 1 | `LaunchedRun.kind` |
| `services/pipeline/src/pipeline/process/reaper.py` | 1 | judge scan containers against `image_scans` |
| `services/pipeline/src/pipeline/process/runtime_entrypoint.py.txt` (new) | 2 | byte-identical copy of `services/process-runtime/entrypoint.py` |
| `services/pipeline/src/pipeline/process/runner_source.py` (new) | 2 | the kind-2 bootstrap |
| `services/pipeline/src/pipeline/config.py` | 3 | seven new settings |
| `services/pipeline/src/pipeline/storage/keys.py` | 3 | `image_scan_prefix`, `image_scan_log_key` |
| `services/pipeline/src/pipeline/process/credentials.py` | 3 | `mint_prefix_credentials` |
| `services/pipeline/src/pipeline/process/logs.py` | 3 | `store_log` |
| `services/pipeline/src/pipeline/images/repo.py` (new) | 4 | `ImagesRepo` seam + `PgImagesRepo` |
| `services/pipeline/src/pipeline/images/registry_auth.py` (new) | 4 | pull-credential resolution |
| `services/pipeline/src/pipeline/images/scan_result.py` | 4 | `scan_result_to_json`, `failure_result` |
| `services/pipeline/src/pipeline/process/launch.py` | 5 | `resolve_run_image`, the §8.4 `check_user_image_launchable`, `ResolvedImage` |
| `services/pipeline/src/pipeline/process/runner.py` | 5 | the launch path wired in |
| `services/pipeline/src/pipeline/jobs/process.py` | 5 | images repo + master key into `run_one`; scan statuses into the reaper |
| `services/image-scanner/**` (new) | 6 | the scanner image and `stac_higher_scanner` package |
| `services/process-runtime/docker-bake.hcl`, `.github/workflows/containers.yml` | 6 | build the scanner |
| `services/pipeline/src/pipeline/images/scan_launch.py` (new) | 7 | one scan as a platform run |
| `services/pipeline/src/pipeline/images/drain.py` (new) | 7 | the drain's pure-ish logic |
| `services/pipeline/src/pipeline/jobs/image_scans.py` (new), `main.py` | 7 | wiring |
| `services/pipeline/src/pipeline/health.py` | 8 | `image_policy` key |
| `docker-compose.yml`, `.env.example` | 8 | `IMAGES=1`, `scanner-egress`, env |
| `docs/processes.md`, `docs/backend.md`, `docs/ISSUES.md`, `docs/FEATURES.md`, `services/pipeline/README.md` | 9 | docs |

---

### Task 0: Precondition (read-only)

- [ ] From the worktree root, run each command and check its result. If any check fails, STOP and report.
  - `git branch --show-current` prints `feat/c2-scanner-drain`.
  - `git merge-base --is-ancestor af82cab HEAD && echo ok` prints `ok`.
  - `grep -n 'name: "030_container_images"' app/src/lib/db/migrate.ts` shows one line, and `grep -c '"031_' app/src/lib/db/migrate.ts` prints `0`.
  - `grep -n "def check_user_image_launchable" services/pipeline/src/pipeline/process/launch.py` shows the C-1 guard.
  - `ls services/image-scanner 2>/dev/null` prints nothing.
  - `grep -n "IMAGES=0" docker-compose.yml` shows the socket proxy line.

---

### Task 1: The executor seam learns user images and scans

**Files:**
- Modify: `services/pipeline/src/pipeline/process/executor.py`
- Modify: `services/pipeline/src/pipeline/process/docker_executor.py`
- Modify: `services/pipeline/src/pipeline/process/memory_executor.py`
- Modify: `services/pipeline/src/pipeline/process/reaper.py`
- Modify: `services/pipeline/tests/test_process_executor.py` (the `FakeApi` helper only)
- Create: `services/pipeline/tests/test_docker_executor_user_images.py`
- Modify: `services/pipeline/tests/test_process_reaper.py` (append)

**Interfaces:**
- Produces (`pipeline.process.executor`): `RUN_KINDS = ("process", "image_scan")`; `class ImagePullFailed(ExecutorError)`; `@dataclass(frozen=True) class RegistryAuth(username: str, password: str (repr=False), server: str = "")`; `RunSpec` gains `kind: str = "process"`, `user_image: bool = False`, `entrypoint: tuple[str, ...] = ()`, `registry_auth: RegistryAuth | None = None`; `LaunchedRun` gains `kind: str = "process"`.
- Produces (`pipeline.process.docker_executor`): `RUN_KIND_LABEL = "stac-higher.run-kind"`, `IMAGE_ID_LABEL = "stac-higher.image-id"`, `USER_IMAGE_USER = "10001:10001"`, `class EngineHTTPError(ExecutorUnavailable)` with `.status: int` and `.detail: str`, `encode_registry_auth(auth: RegistryAuth) -> str`, `DockerExecutor.pull_timeout_seconds: int = 900`.
- Produces (`pipeline.process.reaper`): `process_reap_tick(*, executor, repo, scan_statuses=None, max_age_seconds=..., now=None)`, where `scan_statuses: Callable[[Sequence[str]], Awaitable[dict[str, str]]] | None`.

- [ ] **Step 1: Let the existing `FakeApi` accept headers**

In `services/pipeline/tests/test_process_executor.py`, replace the `FakeApi` class with:
```python
class FakeApi:
    """Records Engine API calls and replays scripted responses."""

    def __init__(self, responses=None):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.headers: list[dict | None] = []
        self.responses = responses or {}

    def __call__(self, method, path, *, body=None, timeout=None, raw=False, headers=None):
        self.calls.append((method, path, body))
        self.headers.append(headers)
        for key, value in self.responses.items():
            if key in path:
                if isinstance(value, Exception):
                    raise value
                return value
        return b"" if raw else {}

    def created_config(self) -> dict:
        return next(b for m, p, b in self.calls if "create" in p)
```

- [ ] **Step 2: Write the failing executor tests**

Create `services/pipeline/tests/test_docker_executor_user_images.py`:
```python
"""The executor's user-image posture and pull-by-digest (C-2, container-images
spec §3.2, §8.4, §8.5).

The Engine API is stubbed. What is asserted is the platform's hardening,
which applies whatever the image says: uid 10001, a /tmp tmpfs, the
entrypoint only when the platform sets one, and never a run by tag.
"""

from __future__ import annotations

import base64
import io
import json
import urllib.error
import urllib.request

import pytest

from pipeline.process.docker_executor import (
    IMAGE_ID_LABEL,
    PROCESS_ID_LABEL,
    RUN_ID_LABEL,
    RUN_KIND_LABEL,
    USER_IMAGE_USER,
    DockerExecutor,
    EngineHTTPError,
    encode_registry_auth,
)
from pipeline.process.executor import (
    ExecutorUnavailable,
    ImagePullFailed,
    RegistryAuth,
    RunSpec,
)

DIGEST = "sha256:" + "a" * 64
PINNED = f"ghcr.io/example/tool@{DIGEST}"


class Engine:
    """A scripted Engine API: images present or absent, pulls that succeed or fail."""

    def __init__(
        self,
        *,
        has_image: bool = True,
        pull_payload: bytes = b'{"status":"Pulling from example/tool"}\n{"status":"Digest: ok"}\n',
        pull_error: Exception | None = None,
        inspect_error: Exception | None = None,
    ):
        self.has_image = has_image
        self.pull_payload = pull_payload
        self.pull_error = pull_error
        self.inspect_error = inspect_error
        self.calls: list[dict] = []

    def __call__(self, method, path, *, body=None, timeout=None, raw=False, headers=None):
        self.calls.append(
            {"method": method, "path": path, "body": body, "headers": headers, "timeout": timeout}
        )
        if path.startswith("/images/create"):
            if self.pull_error is not None:
                raise self.pull_error
            self.has_image = True
            return self.pull_payload
        if path.startswith("/images/"):
            if self.inspect_error is not None:
                raise self.inspect_error
            if self.has_image:
                return {"Id": "sha256:" + "f" * 64}
            raise EngineHTTPError(
                "docker GET /images failed: 404 no such image", status=404, detail="no such image"
            )
        if path.startswith("/containers/create"):
            return {"Id": "c1"}
        return b"" if raw else {}

    def created(self) -> dict:
        return next(c["body"] for c in self.calls if c["path"].startswith("/containers/create"))

    def paths(self) -> list[str]:
        return [c["path"] for c in self.calls]


def executor(engine: Engine) -> DockerExecutor:
    ex = DockerExecutor(docker_host="tcp://proxy:2375")
    ex._request = engine  # type: ignore[method-assign]
    return ex


def spec(**overrides) -> RunSpec:
    base = {
        "run_id": "11111111-1111-4111-8111-111111111111",
        "process_id": "22222222-2222-4222-8222-222222222222",
        "image": PINNED,
        "memory_mb": 256,
        "user_image": True,
    }
    base.update(overrides)
    return RunSpec(**base)


def test_a_user_image_runs_as_10001_with_a_tmpfs_whatever_the_image_says():
    engine = Engine()
    executor(engine).launch(spec())
    config = engine.created()
    assert config["User"] == USER_IMAGE_USER == "10001:10001"
    host = config["HostConfig"]
    assert host["Tmpfs"] == {"/tmp": "rw,nosuid,nodev,size=256m"}
    # The platform hardening still applies, and still no mounts.
    assert host["CapDrop"] == ["ALL"]
    assert "no-new-privileges" in host["SecurityOpt"]
    assert "Binds" not in host and "Mounts" not in host
    assert config["Image"] == PINNED


def test_a_platform_image_keeps_its_own_user_and_gets_no_tmpfs():
    engine = Engine()
    executor(engine).launch(spec(image="stac-higher-process-runtime:local", user_image=False))
    config = engine.created()
    assert "User" not in config
    assert "Tmpfs" not in config["HostConfig"]
    # A platform image is never inspected or pulled through the proxy.
    assert not any(p.startswith("/images/") for p in engine.paths())


def test_the_entrypoint_is_set_only_when_the_platform_sets_one_and_cmd_is_cmd():
    engine = Engine()
    executor(engine).launch(spec(entrypoint=("python3", "-c", "pass")))
    assert engine.created()["Entrypoint"] == ["python3", "-c", "pass"]
    assert "Cmd" not in engine.created()

    engine = Engine()
    executor(engine).launch(spec(cmd=("tool", "--run")))
    assert engine.created()["Cmd"] == ["tool", "--run"]
    # `command` never reaches Entrypoint (spec §14 decision 4).
    assert "Entrypoint" not in engine.created()


def test_a_user_image_not_pinned_by_digest_is_refused_before_anything_is_created():
    engine = Engine()
    with pytest.raises(ImagePullFailed, match="not pinned by digest"):
        executor(engine).launch(spec(image="ghcr.io/example/tool:latest"))
    assert engine.calls == []


def test_an_image_the_daemon_already_has_is_not_pulled():
    engine = Engine(has_image=True)
    executor(engine).launch(spec())
    assert not any(p.startswith("/images/create") for p in engine.paths())


def test_an_absent_image_is_pulled_by_digest_with_the_registry_auth():
    engine = Engine(has_image=False)
    ex = executor(engine)
    ex.launch(spec(registry_auth=RegistryAuth("robot", "s3cret", "ghcr.io")))
    pull = next(c for c in engine.calls if c["path"].startswith("/images/create"))
    assert pull["method"] == "POST"
    assert "fromImage=ghcr.io%2Fexample%2Ftool" in pull["path"]
    assert f"tag={DIGEST.replace(':', '%3A')}" in pull["path"]
    assert pull["timeout"] == ex.pull_timeout_seconds
    auth = json.loads(base64.urlsafe_b64decode(pull["headers"]["X-Registry-Auth"]))
    assert auth == {"username": "robot", "password": "s3cret", "serveraddress": "ghcr.io"}
    # The pull happens before the container is created.
    assert engine.paths().index(pull["path"]) < next(
        i for i, p in enumerate(engine.paths()) if p.startswith("/containers/create")
    )


def test_an_anonymous_pull_sends_no_auth_header():
    engine = Engine(has_image=False)
    executor(engine).launch(spec())
    pull = next(c for c in engine.calls if c["path"].startswith("/images/create"))
    assert pull["headers"] is None


def test_a_pull_whose_stream_reports_an_error_fails_the_pull_and_creates_nothing():
    engine = Engine(
        has_image=False,
        pull_payload=b'{"status":"Pulling"}\n{"errorDetail":{"message":"manifest unknown"},'
        b'"error":"manifest unknown"}\n',
    )
    with pytest.raises(ImagePullFailed, match="manifest unknown"):
        executor(engine).launch(spec())
    assert not any(p.startswith("/containers/create") for p in engine.paths())


def test_a_pull_the_daemon_refuses_is_a_pull_failure_not_an_outage():
    engine = Engine(
        has_image=False,
        pull_error=EngineHTTPError("docker POST failed: 404", status=404, detail="not found"),
    )
    with pytest.raises(ImagePullFailed):
        executor(engine).launch(spec())


def test_an_unreachable_daemon_is_an_outage_not_a_pull_failure():
    engine = Engine(inspect_error=ExecutorUnavailable("docker GET unreachable"))
    with pytest.raises(ExecutorUnavailable) as err:
        executor(engine).launch(spec())
    assert not isinstance(err.value, ImagePullFailed)


def test_a_process_container_is_labelled_with_its_kind_and_process():
    engine = Engine()
    executor(engine).launch(spec())
    labels = engine.created()["Labels"]
    assert labels[RUN_KIND_LABEL] == "process"
    assert labels[PROCESS_ID_LABEL] == "22222222-2222-4222-8222-222222222222"
    assert IMAGE_ID_LABEL not in labels


def test_a_scan_container_is_labelled_and_named_as_a_scan():
    engine = Engine()
    executor(engine).launch(
        spec(image="stac-higher-image-scanner:local", user_image=False, kind="image_scan")
    )
    labels = engine.created()["Labels"]
    assert labels[RUN_KIND_LABEL] == "image_scan"
    assert labels[RUN_ID_LABEL] == "11111111-1111-4111-8111-111111111111"
    assert labels[IMAGE_ID_LABEL] == "22222222-2222-4222-8222-222222222222"
    assert PROCESS_ID_LABEL not in labels
    create = next(c for c in engine.calls if c["path"].startswith("/containers/create"))
    assert "name=stac-scan-11111111" in create["path"]


def test_list_launched_reads_the_kind_and_defaults_old_containers_to_process():
    ex = DockerExecutor(docker_host="tcp://proxy:2375")
    listing = [
        {"Id": "a", "Labels": {RUN_ID_LABEL: "r1", RUN_KIND_LABEL: "image_scan"}, "Created": 1},
        {"Id": "b", "Labels": {RUN_ID_LABEL: "r2"}, "Created": 1},
    ]
    ex._request = lambda method, path, **kw: listing  # type: ignore[method-assign]
    kinds = {entry.run_id: entry.kind for entry in ex.list_launched()}
    assert kinds == {"r1": "image_scan", "r2": "process"}


def test_registry_auth_never_prints_its_password():
    auth = RegistryAuth("robot", "s3cret", "ghcr.io")
    assert "s3cret" not in repr(auth)
    decoded = json.loads(base64.urlsafe_b64decode(encode_registry_auth(auth)))
    assert decoded["password"] == "s3cret"


def test_engine_http_errors_carry_their_status(monkeypatch):
    def boom(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "nf", {}, io.BytesIO(b"no such image"))

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    ex = DockerExecutor(docker_host="tcp://proxy:2375")
    with pytest.raises(EngineHTTPError) as err:
        ex._request("GET", "/images/x/json")
    assert err.value.status == 404
    assert "no such image" in err.value.detail
    assert isinstance(err.value, ExecutorUnavailable)


def test_request_headers_reach_the_wire(monkeypatch):
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def capture(request, timeout):
        seen["auth"] = request.get_header("X-registry-auth")
        return Resp(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", capture)
    ex = DockerExecutor(docker_host="tcp://proxy:2375")
    ex._request("POST", "/images/create?fromImage=x", headers={"X-Registry-Auth": "abc"})
    assert seen["auth"] == "abc"
```

Run: `cd services/pipeline && uv run pytest tests/test_docker_executor_user_images.py -q`
Expected: FAIL at import (`IMAGE_ID_LABEL`, `EngineHTTPError`, `ImagePullFailed`, `RegistryAuth` are not defined).

- [ ] **Step 3: Extend the seam types**

In `services/pipeline/src/pipeline/process/executor.py`:

(a) After the `PRIORITIES` line add:
```python
#: What a run is (C-2): a process run, judged against ``process_runs``, or an
#: image scan, judged against ``image_scans``. The reaper keeps them apart.
RUN_KINDS = ("process", "image_scan")
```

(b) After `class RunTimeout` add:
```python
class ImagePullFailed(ExecutorError):
    """The daemon refused or could not pull a user image by digest (C-2,
    spec §8.4). A per-run OUTCOME, not an outage: the registry may have
    deleted the manifest or revoked the credential, so the caller spends an
    attempt rather than requeueing forever."""


@dataclass(frozen=True)
class RegistryAuth:
    """A pull credential for one registry, resolved at launch (spec §8.4) and
    handed only to the daemon. ``password`` never appears in a repr, a log
    line or an error message."""

    username: str
    password: str = field(repr=False)
    server: str = ""
```

(c) In `RunSpec`, replace the `cmd` field and its comment with:
```python
    #: Docker ``Cmd`` override: a kind-3 revision's ``command``. Empty = the
    #: image's own. It never overrides ``Entrypoint`` or ``User``.
    cmd: tuple[str, ...] = ()
```
and append after `priority: str = "triggered"`:
```python
    #: One of RUN_KINDS. A scan's ``process_id`` carries the IMAGE id.
    kind: str = "process"
    #: C-2 (spec §3.2/§8.4): True for kinds 2-3. The backend then refuses an
    #: image not pinned by digest, pulls it if absent, and forces the
    #: platform's hardening whatever the image says: uid 10001, a /tmp tmpfs.
    user_image: bool = False
    #: Entrypoint override. Only the kind-2 bootstrap sets one; empty = the
    #: image's own.
    entrypoint: tuple[str, ...] = ()
    #: Pull credential for a user image; None = anonymous.
    registry_auth: RegistryAuth | None = None
```

(d) In `LaunchedRun`, append after `created_at`:
```python
    #: One of RUN_KINDS, from the backend's own label. A container launched
    #: before C-2 carries no kind label and reads as a process run.
    kind: str = "process"
```

- [ ] **Step 4: The Docker backend**

In `services/pipeline/src/pipeline/process/docker_executor.py`:

(a) Replace the import block from `from pipeline.process.executor import (` through its closing `)` with:
```python
from pipeline.images.reference import is_image_digest
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
```

(b) After `PROCESS_ID_LABEL = "stac-higher.process-id"` add:
```python
#: C-2: what a container is ("process" | "image_scan"), so the reaper can
#: judge a scan against image_scans instead of process_runs.
RUN_KIND_LABEL = "stac-higher.run-kind"
#: C-2: a scan container names the image it scans.
IMAGE_ID_LABEL = "stac-higher.image-id"
#: Spec §3.2 / §14 decision 1: every user image runs as the platform
#: runtime's `runner` uid, whatever its own USER says.
USER_IMAGE_USER = "10001:10001"
_CONTAINER_NAME_PREFIX = {"process": "stac-run-", "image_scan": "stac-scan-"}
```

(c) After `class UnsafeDockerHost` add:
```python
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
```

(d) In the `DockerExecutor` dataclass, after `poll_slice_seconds: int = 5` add:
```python
    #: A pull streams progress, so this bounds each socket read, not the
    #: whole pull. Generous: a first pull of a large user image is slow.
    pull_timeout_seconds: int = 900
```

(e) Replace `_request` with:
```python
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
        if not sep or not is_image_digest(digest):
            raise ImagePullFailed(
                f"refusing to launch a user image not pinned by digest: {image!r}"
            )
        try:
            self._request("GET", f"/images/{urllib.parse.quote(image, safe='/:@')}/json")
            return
        except EngineHTTPError as err:
            if err.status != 404:
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
```

(f) In `launch`, insert as its first statements:
```python
        if spec.user_image:
            self._ensure_image(spec.image, spec.registry_auth)
        labels = {RUN_ID_LABEL: spec.run_id, RUN_KIND_LABEL: spec.kind}
        if spec.kind == "image_scan":
            labels[IMAGE_ID_LABEL] = spec.process_id
        else:
            labels[PROCESS_ID_LABEL] = spec.process_id
```
replace the `"Labels": {...}` entry of `config` with `"Labels": labels,`, and replace the block
```python
        if spec.cmd:
            config["Cmd"] = list(spec.cmd)

        created = self._request(
            "POST",
            f"/containers/create?name={urllib.parse.quote(f'stac-run-{spec.run_id}')}",
            body=config,
        )
```
with:
```python
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
```

(g) In `list_launched`, replace the `entries.append(LaunchedRun(...))` call with:
```python
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
```

(h) Update the module docstring's first paragraph: replace ``(`tecnativa/docker-socket-proxy` with ``CONTAINERS=1 POST=1`, everything else`` with ``(`tecnativa/docker-socket-proxy` with ``CONTAINERS=1 IMAGES=1 POST=1`` (IMAGES since C-2, for pull-by-digest), everything else``.

- [ ] **Step 5: The memory executor reports the kind**

In `services/pipeline/src/pipeline/process/memory_executor.py`, replace `list_launched`'s body with:
```python
        # Launched-minus-reaped is the fake's honest analogue of "resources
        # the backend still holds", which is what the reaper reconciles.
        return [
            LaunchedRun(
                handle=RunHandle(id=f"mem-{spec.run_id}", backend=self.name),
                run_id=spec.run_id,
                kind=spec.kind,
            )
            for spec in self.launched
            if f"mem-{spec.run_id}" not in self.reaped
        ]
```

- [ ] **Step 6: Write the failing reaper tests**

Append to `services/pipeline/tests/test_process_reaper.py`:
```python
# ---------------------------------------------------------------------------
# C-2: scan containers are judged against image_scans, never process_runs
# ---------------------------------------------------------------------------


def launched_scan(scan_id: str, *, age_seconds: int = 10):
    return LaunchedRun(
        handle=RunHandle(id=f"c-{scan_id}", backend="fake"),
        run_id=scan_id,
        created_at=NOW - dt.timedelta(seconds=age_seconds),
        kind="image_scan",
    )


class ScanLedger:
    def __init__(self, statuses):
        self.statuses = statuses
        self.asked: list[str] = []

    async def __call__(self, scan_ids):
        self.asked = list(scan_ids)
        return {s: self.statuses[s] for s in scan_ids if s in self.statuses}


@pytest.mark.asyncio
async def test_a_running_scan_container_is_left_alone():
    executor = RecordingExecutor([launched_scan("s1")])
    repo = LedgerRepo({})
    ledger = ScanLedger({"s1": "running"})
    result = await process_reap_tick(
        executor=executor, repo=repo, scan_statuses=ledger, now=NOW
    )
    assert result.reaped == 0
    # The process ledger was never asked about a scan id.
    assert repo.asked == []
    assert ledger.asked == ["s1"]


@pytest.mark.asyncio
async def test_a_finished_or_unknown_scan_container_is_reaped():
    executor = RecordingExecutor([launched_scan("s1"), launched_scan("s2")])
    ledger = ScanLedger({"s1": "done"})
    result = await process_reap_tick(
        executor=executor, repo=LedgerRepo({}), scan_statuses=ledger, now=NOW
    )
    assert result.reaped == 2


@pytest.mark.asyncio
async def test_without_a_scan_ledger_scan_containers_are_never_judged():
    """The process reaper cannot know a scan's status, so it must not guess:
    no row in process_runs would otherwise read as 'orphan'."""
    executor = RecordingExecutor([launched_scan("s1"), launched("r1")])
    result = await process_reap_tick(executor=executor, repo=LedgerRepo({}), now=NOW)
    assert result.reaped == 1
    assert "reap:c-s1" not in executor.order
```

Run: `cd services/pipeline && uv run pytest tests/test_process_reaper.py -q`
Expected: FAIL (`process_reap_tick() got an unexpected keyword argument 'scan_statuses'`).

- [ ] **Step 7: The kind-aware reaper**

In `services/pipeline/src/pipeline/process/reaper.py`:

(a) Add to the imports:
```python
from collections.abc import Awaitable, Callable, Sequence
```

(b) Replace `process_reap_tick`'s signature and everything from `statuses = await repo.run_statuses(...)` to the `continue` after the live check with:
```python
async def process_reap_tick(
    *,
    executor: Executor,
    repo: ProcessRepo,
    scan_statuses: Callable[[Sequence[str]], Awaitable[dict[str, str]]] | None = None,
    max_age_seconds: int = DEFAULT_REAP_MAX_AGE_SECONDS,
    now: dt.datetime | None = None,
) -> ReapResult:
```
(keep the unchanged body down to `if not launched: return ReapResult()`), then:
```python
    # C-2: a scan container has no process_runs row by design. It is judged
    # against image_scans when the caller can read that ledger, and left
    # alone when it cannot: a missing row must never read as "orphan".
    process_ids = [e.run_id for e in launched if e.kind != "image_scan"]
    scan_ids = [e.run_id for e in launched if e.kind == "image_scan"]
    statuses = await repo.run_statuses(process_ids)
    scans = (
        await scan_statuses(scan_ids) if scan_ids and scan_statuses is not None else {}
    )

    reaped = failed = 0
    for entry in launched:
        if entry.kind == "image_scan":
            if scan_statuses is None:
                continue
            status = scans.get(entry.run_id)
        else:
            status = statuses.get(entry.run_id)
        aged = (
            entry.created_at is not None
            and (at - entry.created_at).total_seconds() > max_age_seconds
        )
        if status == LIVE_STATUS and not aged:
            continue
```
and in the two `logger.warning` calls below, replace `"run_status": statuses.get(entry.run_id),` with `"run_status": status,` and add `"kind": entry.kind,` to both `extra` dicts.

- [ ] **Step 8: Run the tests**

Run: `cd services/pipeline && uv run pytest tests/test_docker_executor_user_images.py tests/test_process_reaper.py tests/test_process_executor.py -q`
Expected: PASS.

- [ ] **Step 9: Gates and commit**

Run `npm run verify` (worktree root) and `cd services/pipeline && uv run pytest -q && uv run ruff check .`. All green.
```bash
git add services/pipeline/src/pipeline/process/executor.py services/pipeline/src/pipeline/process/docker_executor.py services/pipeline/src/pipeline/process/memory_executor.py services/pipeline/src/pipeline/process/reaper.py services/pipeline/tests/test_process_executor.py services/pipeline/tests/test_docker_executor_user_images.py services/pipeline/tests/test_process_reaper.py
git commit -m "feat(pipeline): executor seam for user images and scans: uid 10001, tmpfs, pull by digest, run-kind labels (C-2)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 2: The kind-2 runner bootstrap

Spec §3.1: for kind 2 the platform's runner travels the way the code does, in the environment. The pipeline ships a byte-identical copy of `services/process-runtime/entrypoint.py` and a hash test pins the two together. The copy is a `.txt` data file, not a `.py` module: a verbatim `.py` copy would be linted by the pipeline's ruff, and its `# noqa: S102` / `# noqa: BLE001` directives name rules this project does not enable, so RUF100 would flag them (Decision 18).

**Files:**
- Create: `services/pipeline/src/pipeline/process/runtime_entrypoint.py.txt` (a `cp`, never hand-typed)
- Create: `services/pipeline/src/pipeline/process/runner_source.py`
- Create: `services/pipeline/tests/test_runner_bootstrap.py`

**Interfaces:**
- Produces (`pipeline.process.runner_source`): `RUNNER_ENV_VAR = "STAC_HIGHER_RUNNER_B64"`, `RUNNER_SOURCE_PATH: Path`, `BOOTSTRAP: str`, `BOOTSTRAP_ENTRYPOINT: tuple[str, str, str]` (`("python3", "-c", BOOTSTRAP)`), `runner_source_b64() -> str`.

- [ ] **Step 1: Write the failing test**

Create `services/pipeline/tests/test_runner_bootstrap.py`:
```python
"""The kind-2 bootstrap (container-images spec §3.1).

A kind-2 image carries its own Python and libraries; the platform injects
its runner and the revision's code through the environment, never a mount.
These tests run the bootstrap for real, in a subprocess, with this
interpreter standing in for the image's python3.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from pipeline.process.docker_executor import CODE_ENV_VAR, encode_code
from pipeline.process.runner_source import (
    BOOTSTRAP,
    BOOTSTRAP_ENTRYPOINT,
    RUNNER_ENV_VAR,
    RUNNER_SOURCE_PATH,
    runner_source_b64,
)

REPO = Path(__file__).resolve().parents[3]
RUNTIME_ENTRYPOINT = REPO / "services" / "process-runtime" / "entrypoint.py"


def test_the_shipped_runner_is_byte_identical_to_the_runtime_images():
    """Kind 2 must behave exactly as the platform image does (exit codes
    0/1/2, the code popped from the environment). A drifted copy would make
    the same revision behave differently on a user image."""
    ours = hashlib.sha256(RUNNER_SOURCE_PATH.read_bytes()).hexdigest()
    theirs = hashlib.sha256(RUNTIME_ENTRYPOINT.read_bytes()).hexdigest()
    assert ours == theirs, (
        "services/pipeline/src/pipeline/process/runtime_entrypoint.py.txt drifted from "
        "services/process-runtime/entrypoint.py; re-copy it with cp"
    )


def test_the_bootstrap_is_one_python3_line_naming_the_runner_variable():
    assert BOOTSTRAP_ENTRYPOINT == ("python3", "-c", BOOTSTRAP)
    assert "\n" not in BOOTSTRAP
    assert RUNNER_ENV_VAR in BOOTSTRAP


def _bootstrap(tmp_path, *, code: str | None):
    env = {"PATH": os.environ.get("PATH", ""), RUNNER_ENV_VAR: runner_source_b64()}
    if code is not None:
        env[CODE_ENV_VAR] = encode_code(code)
    return subprocess.run(
        [sys.executable, "-c", BOOTSTRAP],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
        check=False,
    )


def test_the_bootstrap_runs_the_code_and_hides_both_payloads(tmp_path):
    code = (
        "import os\n"
        "print('hello from kind 2')\n"
        "print(sorted(k for k in os.environ if k.startswith('STAC_HIGHER')))\n"
    )
    done = _bootstrap(tmp_path, code=code)
    assert done.returncode == 0, done.stderr
    assert "hello from kind 2" in done.stdout
    # The runner popped itself and the runner popped the code: a process that
    # dumps its environment prints neither back into its log.
    assert "[]" in done.stdout


def test_a_raising_process_exits_1_with_its_traceback(tmp_path):
    done = _bootstrap(tmp_path, code="raise ValueError('boom')")
    assert done.returncode == 1
    assert "ValueError: boom" in done.stderr


def test_missing_code_is_the_platform_error_exit_2(tmp_path):
    done = _bootstrap(tmp_path, code=None)
    assert done.returncode == 2
```

Run: `cd services/pipeline && uv run pytest tests/test_runner_bootstrap.py -q`
Expected: FAIL (`No module named 'pipeline.process.runner_source'`).

- [ ] **Step 2: Copy the runner**

From the worktree root:
```bash
cp services/process-runtime/entrypoint.py services/pipeline/src/pipeline/process/runtime_entrypoint.py.txt
```

- [ ] **Step 3: The bootstrap module**

Create `services/pipeline/src/pipeline/process/runner_source.py`:
```python
"""The kind-2 bootstrap (C-2, container-images spec §3.1).

``inline_python_on_image`` runs the revision's code on a USER image that
carries no platform package. The platform's runner (the platform runtime
image's ``entrypoint.py``) travels the same way the code does, in the
environment, because the executor never mounts anything (ADR 0013):

- ``STAC_HIGHER_RUNNER_B64`` is base64 of the runner's source.
- The executor sets ``Entrypoint: ["python3", "-c", BOOTSTRAP]``. The one
  line pops the runner from the environment, decodes it and executes it as
  ``__main__``; the runner then pops ``STAC_HIGHER_PROCESS_CODE_B64`` and
  behaves exactly as on the platform image (exit codes 0/1/2).

The runner source ships as ``runtime_entrypoint.py.txt`` beside this
module, a byte-identical copy that ``tests/test_runner_bootstrap.py`` pins to
``services/process-runtime/entrypoint.py`` by sha256. It is a data file on
purpose: the pipeline never imports it.

Author requirement (``docs/processes.md``): ``python3`` >= 3.10 on PATH.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

RUNNER_ENV_VAR = "STAC_HIGHER_RUNNER_B64"
RUNNER_SOURCE_PATH = Path(__file__).with_name("runtime_entrypoint.py.txt")

#: One line, no shell. The filename given to compile() is the platform
#: image's path, so a traceback reads the same on both images.
BOOTSTRAP = (
    "import base64,os;"
    "s=base64.b64decode(os.environ.pop('STAC_HIGHER_RUNNER_B64')).decode('utf-8');"
    "exec(compile(s,'/opt/stac-higher/entrypoint.py','exec'),{'__name__':'__main__'})"
)
BOOTSTRAP_ENTRYPOINT = ("python3", "-c", BOOTSTRAP)


@lru_cache(maxsize=1)
def runner_source_b64() -> str:
    """Base64 of the runner, read once per process."""
    return base64.b64encode(RUNNER_SOURCE_PATH.read_bytes()).decode("ascii")
```

- [ ] **Step 4: Run the tests**

Run: `cd services/pipeline && uv run pytest tests/test_runner_bootstrap.py -q`
Expected: PASS.

- [ ] **Step 5: Gates and commit**

Run `npm run verify` and `cd services/pipeline && uv run pytest -q && uv run ruff check .`.
```bash
git add services/pipeline/src/pipeline/process/runtime_entrypoint.py.txt services/pipeline/src/pipeline/process/runner_source.py services/pipeline/tests/test_runner_bootstrap.py
git commit -m "feat(pipeline): kind-2 runner bootstrap, the runtime entrypoint shipped by value and pinned by hash (C-2)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Settings, scan keys, prefix credentials and a generic log writer

**Files:**
- Modify: `services/pipeline/src/pipeline/config.py`
- Modify: `services/pipeline/src/pipeline/storage/keys.py`
- Modify: `services/pipeline/src/pipeline/process/credentials.py`
- Modify: `services/pipeline/src/pipeline/process/logs.py`
- Modify: `services/pipeline/tests/test_config.py` (append)
- Create: `services/pipeline/tests/test_image_scan_plumbing.py`

**Interfaces:**
- Produces (`Settings`): `image_scanner_image: str` (`IMAGE_SCANNER_IMAGE`, default `"stac-higher-image-scanner:local"`), `process_scanner_network: str` (`PROCESS_SCANNER_NETWORK`, default `"none"`), `image_scanner_db_update: bool` (`IMAGE_SCANNER_DB_UPDATE`, default `True`), `grype_db_update_url: str | None` (`GRYPE_DB_UPDATE_URL`), `registry_dockerhub_user: str | None` (`REGISTRY_DOCKERHUB_USER`), `registry_dockerhub_token: str | None` (`REGISTRY_DOCKERHUB_TOKEN`, `repr=False`), `image_scan_concurrency: int` (`IMAGE_SCAN_CONCURRENCY`, default 1, >= 1).
- Produces (`pipeline.storage.keys`): `SCANS_PREFIX = "scans"`, `image_scan_prefix(image_id: str, scan_id: str) -> str` (`"scans/{image_id}/{scan_id}/"`), `image_scan_log_key(image_id: str, scan_id: str) -> str` (`"scans/{image_id}/{scan_id}/log"`).
- Produces (`pipeline.process.credentials`): `mint_prefix_credentials(settings, *, session_name: str, prefix: str, timeout_seconds: int, sts_client=None, read_prefixes: Sequence[str] = ()) -> RunCredentials`. `mint_run_credentials` keeps its signature and delegates.
- Produces (`pipeline.process.logs`): `store_log(client, bucket: str, key: str, payload: bytes, max_bytes: int, *, context: Mapping[str, str]) -> str | None`. `store_run_log` keeps its signature and delegates.

- [ ] **Step 1: Write the failing tests**

Append to `services/pipeline/tests/test_config.py`:
```python
# ---------------------------------------------------------------------------
# C-2: the scanner, the scan drain and the Docker Hub credential
# ---------------------------------------------------------------------------


def test_c2_settings_defaults():
    s = Settings.from_env({})
    assert s.image_scanner_image == "stac-higher-image-scanner:local"
    # No scanner network in code: a deployment that has not decided on
    # scanner egress gets scans that fail, never scans on the default bridge.
    assert s.process_scanner_network == "none"
    assert s.image_scanner_db_update is True
    assert s.grype_db_update_url is None
    assert s.registry_dockerhub_user is None and s.registry_dockerhub_token is None
    assert s.image_scan_concurrency == 1


def test_c2_settings_parse_and_blank_means_unset():
    s = Settings.from_env(
        {
            "IMAGE_SCANNER_IMAGE": "ghcr.io/org/scanner:20260927",
            "PROCESS_SCANNER_NETWORK": "stac-higher_scanner-egress",
            "IMAGE_SCANNER_DB_UPDATE": "false",
            "GRYPE_DB_UPDATE_URL": " https://mirror.example/listing.json ",
            "REGISTRY_DOCKERHUB_USER": "robot",
            "REGISTRY_DOCKERHUB_TOKEN": "dckr_pat_x",
            "IMAGE_SCAN_CONCURRENCY": "2",
        }
    )
    assert s.image_scanner_image == "ghcr.io/org/scanner:20260927"
    assert s.process_scanner_network == "stac-higher_scanner-egress"
    assert s.image_scanner_db_update is False
    assert s.grype_db_update_url == "https://mirror.example/listing.json"
    assert (s.registry_dockerhub_user, s.registry_dockerhub_token) == ("robot", "dckr_pat_x")
    assert s.image_scan_concurrency == 2
    blank = Settings.from_env(
        {"REGISTRY_DOCKERHUB_USER": "  ", "GRYPE_DB_UPDATE_URL": "", "PROCESS_SCANNER_NETWORK": ""}
    )
    assert blank.registry_dockerhub_user is None
    assert blank.grype_db_update_url is None
    assert blank.process_scanner_network == "none"


def test_the_docker_hub_token_never_prints():
    s = Settings.from_env(
        {"REGISTRY_DOCKERHUB_USER": "robot", "REGISTRY_DOCKERHUB_TOKEN": "dckr_pat_x"}
    )
    assert "dckr_pat_x" not in repr(s)


def test_image_scan_concurrency_must_be_positive():
    with pytest.raises(ValueError, match="IMAGE_SCAN_CONCURRENCY"):
        Settings.from_env({"IMAGE_SCAN_CONCURRENCY": "0"})
```
(If `pytest` is not yet imported in `test_config.py`, add `import pytest` at the top with the other imports.)

Create `services/pipeline/tests/test_image_scan_plumbing.py`:
```python
"""C-2 plumbing: the scan key layout, prefix-scoped STS credentials and the
generic log writer (container-images spec §6.2, §8.1)."""

from __future__ import annotations

import json

import pytest

from pipeline.config import Settings
from pipeline.process.credentials import mint_prefix_credentials, mint_run_credentials
from pipeline.process.logs import TRUNCATION_MARKER, store_log
from pipeline.storage.keys import (
    InvalidKeySegment,
    image_scan_log_key,
    image_scan_prefix,
)

IMAGE = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f"
SCAN = "0d9e8f7a-6b5c-4d3e-9f2a-1b0c9d8e7f6a"


class FakeSts:
    def __init__(self):
        self.kwargs = None

    def assume_role(self, **kwargs):
        self.kwargs = kwargs
        return {"Credentials": {"AccessKeyId": "A", "SecretAccessKey": "S", "SessionToken": "T"}}


class FakeStore:
    def __init__(self, fail=False):
        self.fail = fail
        self.written: list[tuple[str, bytes]] = []

    def put_object(self, **kwargs):
        if self.fail:
            raise RuntimeError("bucket gone")
        self.written.append((kwargs["Key"], kwargs["Body"]))


def test_the_scan_prefix_and_log_key():
    assert image_scan_prefix(IMAGE, SCAN) == f"scans/{IMAGE}/{SCAN}/"
    assert image_scan_log_key(IMAGE, SCAN) == f"scans/{IMAGE}/{SCAN}/log"


@pytest.mark.parametrize("bad", ["", "..", "a/b", ".hidden"])
def test_scan_keys_refuse_traversal(bad):
    with pytest.raises(InvalidKeySegment):
        image_scan_prefix(bad, SCAN)
    with pytest.raises(InvalidKeySegment):
        image_scan_prefix(IMAGE, bad)


def test_prefix_credentials_bound_the_session_to_the_scan_prefix():
    sts = FakeSts()
    creds = mint_prefix_credentials(
        Settings.from_env({}),
        session_name=f"stac-scan-{SCAN}",
        prefix=image_scan_prefix(IMAGE, SCAN),
        timeout_seconds=900,
        sts_client=sts,
        read_prefixes=[f"scans/{IMAGE}/older-scan/"],
    )
    assert creds.prefix == f"scans/{IMAGE}/{SCAN}/"
    assert sts.kwargs["RoleSessionName"] == f"stac-scan-{SCAN}"[:64]
    policy = json.loads(sts.kwargs["Policy"])
    write = policy["Statement"][0]["Resource"]
    assert write == [f"arn:aws:s3:::stac-higher/scans/{IMAGE}/{SCAN}/*"]
    read = policy["Statement"][2]["Resource"]
    assert read == [f"arn:aws:s3:::stac-higher/scans/{IMAGE}/older-scan/*"]
    assert creds.as_env()["STAC_HIGHER_OUTPUT_PREFIX"] == f"scans/{IMAGE}/{SCAN}/"


def test_run_credentials_are_unchanged_by_the_refactor():
    sts = FakeSts()
    creds = mint_run_credentials(Settings.from_env({}), "run-1", 60, sts_client=sts)
    assert creds.prefix == "staging/runs/run-1/"
    assert sts.kwargs["RoleSessionName"] == "stac-run-run-1"
    assert sts.kwargs["DurationSeconds"] == 900


def test_store_log_caps_and_returns_the_key():
    store = FakeStore()
    key = image_scan_log_key(IMAGE, SCAN)
    assert store_log(store, "b", key, b"x" * 500, 200, context={"scan_id": SCAN}) == key
    written = store.written[0][1]
    assert len(written) == 200 and written.endswith(TRUNCATION_MARKER)


def test_store_log_failure_returns_none():
    assert store_log(FakeStore(fail=True), "b", "k", b"x", 10, context={}) is None
```

Run: `cd services/pipeline && uv run pytest tests/test_config.py tests/test_image_scan_plumbing.py -q`
Expected: FAIL (unknown settings attributes; `image_scan_prefix`, `mint_prefix_credentials`, `store_log` do not exist).

- [ ] **Step 2: Settings**

In `services/pipeline/src/pipeline/config.py`:

(a) At the end of the module docstring's env list (after the `PROCESS_HARDWARE_PROFILES_FILE` bullet) add:
```
- ``IMAGE_SCANNER_IMAGE`` / ``PROCESS_SCANNER_NETWORK`` / ``IMAGE_SCANNER_DB_UPDATE``
  / ``GRYPE_DB_UPDATE_URL`` / ``IMAGE_SCAN_CONCURRENCY`` -- the image scanner
  (C-2, container-images spec §6, §11); ``REGISTRY_DOCKERHUB_USER`` /
  ``REGISTRY_DOCKERHUB_TOKEN`` -- the optional deployment Docker Hub
  credential (spec §5, ISSUES I-125).
```

(b) After `DEFAULT_PROCESS_INPUT_STAGE_CONCURRENCY = 4` add:
```python
#: C-2 (spec §6.1): the platform-built scanner image (services/image-scanner).
DEFAULT_IMAGE_SCANNER_IMAGE = "stac-higher-image-scanner:local"
#: C-2 (spec §11): the scanner's network. `none` in code means a scan cannot
#: reach a registry and fails, the right default for a deployment that has
#: not decided on scanner egress; compose sets its `scanner-egress` network.
DEFAULT_PROCESS_SCANNER_NETWORK = "none"
#: C-2: scans running at once across the deployment. A scan holds a worker
#: slot for up to the policy timeout until K-4 (ISSUES I-124).
DEFAULT_IMAGE_SCAN_CONCURRENCY = 1
```

(c) After `_parse_flush_seconds` add:
```python
def _parse_scan_concurrency(raw: str | None) -> int:
    value = int(raw) if raw not in (None, "") else DEFAULT_IMAGE_SCAN_CONCURRENCY
    if value < 1:
        raise ValueError(f"IMAGE_SCAN_CONCURRENCY must be >= 1, got {value}")
    return value


def _optional(raw: str | None) -> str | None:
    """Blank means unset: compose passes `${VAR:-}` as an empty string."""
    value = (raw or "").strip()
    return value or None
```

(d) In `Settings`, after `process_input_stage_concurrency: int = DEFAULT_PROCESS_INPUT_STAGE_CONCURRENCY` add:
```python
    #: C-2 image scanner (spec §6, §11) -- see the DEFAULT_IMAGE_SCANNER_* constants.
    image_scanner_image: str = DEFAULT_IMAGE_SCANNER_IMAGE
    process_scanner_network: str = DEFAULT_PROCESS_SCANNER_NETWORK
    #: Refresh the Grype DB at scan start (spec §6.1); false for air-gap.
    image_scanner_db_update: bool = True
    #: Grype's DB listing URL; None = Anchore's. An air-gapped mirror goes here.
    grype_db_update_url: str | None = None
    #: The optional deployment Docker Hub credential (spec §5, I-125).
    registry_dockerhub_user: str | None = None
    registry_dockerhub_token: str | None = field(default=None, repr=False)
    image_scan_concurrency: int = DEFAULT_IMAGE_SCAN_CONCURRENCY
```

(e) In `from_env`, after the `process_input_stage_concurrency=int(...)` argument add:
```python
            image_scanner_image=env.get("IMAGE_SCANNER_IMAGE", DEFAULT_IMAGE_SCANNER_IMAGE),
            process_scanner_network=_optional(env.get("PROCESS_SCANNER_NETWORK"))
            or DEFAULT_PROCESS_SCANNER_NETWORK,
            image_scanner_db_update=_parse_bool(env.get("IMAGE_SCANNER_DB_UPDATE"), True),
            grype_db_update_url=_optional(env.get("GRYPE_DB_UPDATE_URL")),
            registry_dockerhub_user=_optional(env.get("REGISTRY_DOCKERHUB_USER")),
            registry_dockerhub_token=_optional(env.get("REGISTRY_DOCKERHUB_TOKEN")),
            image_scan_concurrency=_parse_scan_concurrency(env.get("IMAGE_SCAN_CONCURRENCY")),
```

- [ ] **Step 3: Scan keys**

In `services/pipeline/src/pipeline/storage/keys.py`, after `LOGS_PREFIX = "logs"` add:
```python
#: C-2 (container-images spec §6.2/§8.3): one scan's objects -- the SBOM
#: pair, the full Grype findings, result.json and the scanner's log. Platform
#: bytes like logs/: never catalog assets, never asset_gc (ADR 0011).
SCANS_PREFIX = "scans"
```
and at the end of the module add:
```python
def image_scan_prefix(image_id: str, scan_id: str) -> str:
    """``scans/{image_id}/{scan_id}/`` -- the ONLY place a scanner run may
    write. Its STS credential is bounded to exactly this prefix (spec §6.2),
    and the drain believes only object keys under it."""
    for field, value in (("image id", image_id), ("scan id", scan_id)):
        if not _SAFE_IDENTITY.match(value):
            raise InvalidKeySegment(f"{field} is not a safe path segment: {value!r}")
    return f"{SCANS_PREFIX}/{image_id}/{scan_id}/"


def image_scan_log_key(image_id: str, scan_id: str) -> str:
    """``scans/{image_id}/{scan_id}/log`` -- the scanner run's captured log,
    referenced from ``image_scans.log_ref`` (spec §8.1)."""
    return f"{image_scan_prefix(image_id, scan_id)}log"
```

- [ ] **Step 4: Prefix credentials**

In `services/pipeline/src/pipeline/process/credentials.py`, replace the whole `mint_run_credentials` function with:
```python
def mint_prefix_credentials(
    settings: Settings,
    *,
    session_name: str,
    prefix: str,
    timeout_seconds: int,
    sts_client=None,
    read_prefixes: Sequence[str] = (),
) -> RunCredentials:
    """Mint credentials good only for ``prefix`` plus read access to
    ``read_prefixes``. A process run's prefix is ``staging/runs/{run_id}/``;
    an image scan's is ``scans/{image_id}/{scan_id}/`` (C-2, spec §6.2).

    Raises :class:`RunCredentialsError` on any STS failure -- the caller must
    fail the run rather than start it with wider access.
    """
    if len(read_prefixes) > MAX_READ_PREFIXES:
        raise RunCredentialsError(
            f"run would need {len(read_prefixes)} read prefixes; the inline session "
            f"policy supports at most {MAX_READ_PREFIXES} source collections"
        )
    bucket = settings.staging_bucket
    duration = max(
        MIN_DURATION_SECONDS, timeout_seconds + settings.process_credential_grace_seconds
    )
    client = sts_client if sts_client is not None else build_sts_client(settings)

    try:
        response = client.assume_role(
            RoleArn=settings.process_sts_role_arn,
            # Session names are surfaced in access logs; the run or scan id
            # makes an S3 audit trail directly attributable to its row.
            RoleSessionName=session_name[:64],
            Policy=json.dumps(session_policy(bucket, prefix, read_prefixes)),
            DurationSeconds=duration,
        )
    # Deliberately broad: the contract here is "any STS failure means the run
    # does not start". Enumerating boto's exception types would let an
    # unanticipated one (a transport error, a stubbed client, a
    # misconfiguration surfacing as AttributeError) escape as itself, and a
    # caller that expected RunCredentialsError would then handle it as
    # something else -- the one outcome this module exists to prevent.
    except Exception as err:
        raise RunCredentialsError(
            "could not mint run-scoped storage credentials via STS "
            f"({type(err).__name__}: {err}). The platform's own keys are NOT a "
            "fallback -- a deployment whose object store lacks STS cannot run "
            "processes (spec §5)."
        ) from err

    creds = (response or {}).get("Credentials") or {}
    missing = [
        field
        for field in ("AccessKeyId", "SecretAccessKey", "SessionToken")
        if not creds.get(field)
    ]
    if missing:
        raise RunCredentialsError(
            f"STS response is missing {', '.join(missing)} -- refusing to start "
            "a run with incomplete credentials"
        )

    return RunCredentials(
        access_key_id=creds["AccessKeyId"],
        secret_access_key=creds["SecretAccessKey"],
        session_token=creds["SessionToken"],
        bucket=bucket,
        prefix=prefix,
        endpoint_url=settings.process_run_s3_endpoint or settings.staging_s3_endpoint,
        region=settings.staging_s3_region,
    )


def mint_run_credentials(
    settings: Settings,
    run_id: str,
    timeout_seconds: int,
    *,
    sts_client=None,
    read_prefixes: Sequence[str] = (),
) -> RunCredentials:
    """Mint credentials good only for ``staging/runs/{run_id}/`` plus read
    access to ``read_prefixes`` (the source collections' canonical prefixes)."""
    return mint_prefix_credentials(
        settings,
        session_name=f"stac-run-{run_id}",
        prefix=run_staging_prefix(run_id),
        timeout_seconds=timeout_seconds,
        sts_client=sts_client,
        read_prefixes=read_prefixes,
    )
```
(The error texts are the originals with `--` for the em dash; no test matches on the dash itself: `grep -n "match=" services/pipeline/tests/test_process_executor.py` shows only `"read prefixes"` for this module.)

- [ ] **Step 5: The generic log writer**

In `services/pipeline/src/pipeline/process/logs.py`:

(a) Add `from collections.abc import Mapping` to the imports.

(b) Replace `store_run_log` with:
```python
def store_log(
    client,
    bucket: str,
    key: str,
    payload: bytes,
    max_bytes: int,
    *,
    context: Mapping[str, str],
) -> str | None:
    """Write a capped log object and return its key, or ``None`` when the
    write fails: a lost log must never lose the verdict, which is already
    known by the time this is called. ``context`` names the run or scan in
    the platform's own warning (ids only, never the content)."""
    try:
        put_object(client, bucket, key, cap(payload, max_bytes), content_type="text/plain")
    except Exception as err:  # any store failure is non-fatal here (see above)
        logger.warning(
            "run log could not be stored",
            extra={**context, "key": key, "error": str(err)},
        )
        return None
    return key


def store_run_log(
    client,
    bucket: str,
    process_id: str,
    run_id: str,
    payload: bytes,
    max_bytes: int,
) -> str | None:
    """Write the capped log and return its key for ``process_runs.log_ref``."""
    return store_log(
        client,
        bucket,
        run_log_key(process_id, run_id),
        payload,
        max_bytes,
        context={"run_id": run_id, "process_id": process_id},
    )
```
Then run `grep -rn "process run log could not be stored" services/pipeline/tests`: if a test asserts the old message, change that assertion to `"run log could not be stored"`.

- [ ] **Step 6: Run the tests**

Run: `cd services/pipeline && uv run pytest tests/test_config.py tests/test_image_scan_plumbing.py tests/test_process_executor.py -q`
Expected: PASS.

- [ ] **Step 7: Gates and commit**

Run `npm run verify` and `cd services/pipeline && uv run pytest -q && uv run ruff check .`.
```bash
git add services/pipeline/src/pipeline/config.py services/pipeline/src/pipeline/storage/keys.py services/pipeline/src/pipeline/process/credentials.py services/pipeline/src/pipeline/process/logs.py services/pipeline/tests/test_config.py services/pipeline/tests/test_image_scan_plumbing.py
git commit -m "feat(pipeline): scanner settings, scans/ key layout, prefix-scoped STS minting, generic log writer (C-2)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 4: The images repository, pull-credential resolution and the canonical result

**Files:**
- Create: `services/pipeline/src/pipeline/images/repo.py`
- Create: `services/pipeline/src/pipeline/images/registry_auth.py`
- Modify: `services/pipeline/src/pipeline/images/scan_result.py` (append two functions)
- Create: `services/pipeline/tests/_images_fake.py`
- Create: `services/pipeline/tests/test_image_registry_auth.py`
- Create: `services/pipeline/tests/test_image_repo_fake.py`
- Create: `services/pipeline/tests/test_integration_images_repo.py` (DB-gated; skips without `DATABASE_URL`)

**Interfaces:**
- Produces (`pipeline.images.repo`): `ImageRow` (frozen: `id, reference, tag_at_add, status, digest=None, platform_digest=None, platform=None, size_bytes=None, config=None, sbom_ref=None, last_scanned_at=None, registry_connection_id=None, exception_expires_at=None`), `ClaimedScan(id, kind, requested_by, image: ImageRow)`, `RegistryCredentialRow(connection_id, protocol, config, credentials: bytes | None, deleted: bool)`, `STALLED_SCAN_ERROR: str`, the ABC `ImagesRepo` with the async methods listed in Step 3, and `PgImagesRepo(database_url)`.
- Produces (`pipeline.images.registry_auth`): `RegistryCredentialGone(Exception)`, `RegistryAuthUnavailable(Exception)`, `async resolve_registry_auth(image: ImageRow, *, repo: ImagesRepo, settings: Settings, master_key: bytes | None) -> RegistryAuth | None`.
- Produces (`pipeline.images.scan_result`): `scan_result_to_json(result: ScanResult) -> dict[str, Any]`, `failure_result(kind: str, reference: str, tag: str, error: str) -> dict[str, Any]`, `ERROR_MAX_CHARS = 1000`.
- Produces (tests): `tests/_images_fake.py::FakeImagesRepo` with `add_image(**fields) -> ImageRow`, `add_scan(scan_id, image_id, kind="admission", *, requested_at=None, status="pending", started_at=None)`, `add_credential(row)`, and the recorded state `scans: dict[str, dict]`, `verdicts: dict[str, dict]`, `last_scan_ids: dict[str, str]`, `deleted_images: list[str]`.
- Consumes: `RegistryAuth` (Task 1), `Settings.registry_dockerhub_user/_token` (Task 3).

- [ ] **Step 1: Write the failing tests**

Create `services/pipeline/tests/test_image_repo_fake.py`:
```python
"""The behavioural contract of the images repository, pinned on the fake
(the Pg SQL is `# pragma: no cover` by convention and exercised by the
DB-gated test_integration_images_repo.py)."""

from __future__ import annotations

import datetime as dt

import pytest

from _images_fake import FakeImagesRepo
from pipeline.images.scan_result import (
    ERROR_MAX_CHARS,
    failure_result,
    parse_scan_result,
    scan_result_to_json,
)

NOW = dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.UTC)
IMG = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f"


def repo_with(status="pending", **image):
    repo = FakeImagesRepo()
    repo.add_image(
        id=IMG, reference="docker.io/library/python", tag_at_add="3.12-slim", status=status, **image
    )
    return repo


@pytest.mark.asyncio
async def test_claim_takes_the_oldest_pending_and_marks_an_admission_scanning():
    repo = repo_with()
    repo.add_scan("s2", IMG, requested_at=NOW)
    repo.add_scan("s1", IMG, requested_at=NOW - dt.timedelta(minutes=1))
    claimed = await repo.claim_pending_scan(max_running=1)
    assert claimed is not None and claimed.id == "s1"
    assert claimed.image.status == "scanning"
    assert repo.scans["s1"]["status"] == "running"


@pytest.mark.asyncio
async def test_claim_respects_the_deployment_wide_cap():
    repo = repo_with()
    repo.add_scan("s0", IMG, status="running", started_at=NOW)
    repo.add_scan("s1", IMG)
    assert await repo.claim_pending_scan(max_running=1) is None
    assert (await repo.claim_pending_scan(max_running=2)).id == "s1"


@pytest.mark.asyncio
async def test_a_rescan_claim_leaves_the_visible_status_alone():
    repo = repo_with(status="approved", digest="sha256:" + "a" * 64)
    repo.add_scan("s1", IMG, kind="rescan")
    claimed = await repo.claim_pending_scan(max_running=1)
    assert claimed.image.status == "approved"


@pytest.mark.asyncio
async def test_stalled_scans_fail_with_an_identity_bearing_result():
    repo = repo_with(status="scanning")
    repo.add_scan("s1", IMG, status="running", started_at=NOW - dt.timedelta(hours=2))
    repo.add_scan("s2", IMG, status="running", started_at=NOW)
    assert await repo.fail_stalled_scans(started_before=NOW - dt.timedelta(hours=1)) == 1
    assert repo.scans["s1"]["status"] == "failed"
    assert repo.scans["s2"]["status"] == "running"
    # The C-1 readers accept what the sweep writes (Decision 1).
    parsed = parse_scan_result(repo.scans["s1"]["result"])
    assert parsed.error and parsed.reference == "docker.io/library/python"
    assert repo.images[IMG].status == "scan_failed"


def test_failure_result_is_the_c1_shape_and_is_bounded():
    doc = failure_result("admission", "ghcr.io/example/tool", "1.0", "x" * 5000)
    assert set(doc) == {"version", "kind", "reference", "tag", "error"}
    assert len(doc["error"]) == ERROR_MAX_CHARS
    assert parse_scan_result(doc).error is not None
    assert failure_result("rescan", "ghcr.io/example/tool", "1.0", "   ")["error"] == "scan failed"


def test_the_canonical_result_round_trips_the_fixture_document():
    import json
    from pathlib import Path

    fixture = json.loads(
        (Path(__file__).resolve().parents[3] / "tests/contract-fixtures/image-scan-result.json")
        .read_text()
    )
    doc = {**fixture["document"], "unknown_key": [1, 2]}
    parsed = parse_scan_result(doc)
    canonical = scan_result_to_json(parsed)
    assert "unknown_key" not in canonical
    assert parse_scan_result(canonical) == parsed
    assert canonical["top"][0]["id"] == "CVE-2026-1234"
```

Create `services/pipeline/tests/test_image_registry_auth.py`:
```python
"""Pull-credential resolution (C-2, container-images spec §5, §8.4).

The credential that reaches the daemon or the scanner is the image's own
group credential, or the deployment's Docker Hub one for a docker.io image
that has none, or nothing (anonymous). A credential is never sent to a host
it was not configured for."""

from __future__ import annotations

import json

import pytest

from _images_fake import FakeImagesRepo
from pipeline.config import Settings
from pipeline.connections.envelope import load_master_key, seal
from pipeline.images.registry_auth import (
    RegistryAuthUnavailable,
    RegistryCredentialGone,
    resolve_registry_auth,
)
from pipeline.images.repo import ImageRow, RegistryCredentialRow

KEY = load_master_key({"CREDENTIALS_MASTER_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="})
CONN = "5a4b3c2d-1e0f-4a9b-8c7d-6e5f4a3b2c1d"


def image(reference="ghcr.io/example/tool", connection=None) -> ImageRow:
    return ImageRow(
        id="7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f",
        reference=reference,
        tag_at_add="1.0",
        status="approved",
        registry_connection_id=connection,
    )


def credential(*, host="ghcr.io", deleted=False, secrets=None) -> RegistryCredentialRow:
    payload = secrets if secrets is not None else {"username": "robot", "password": "s3cret"}
    return RegistryCredentialRow(
        connection_id=CONN,
        protocol="registry",
        config={"host": host},
        credentials=seal(json.dumps(payload), KEY),
        deleted=deleted,
    )


def repo_with(row: RegistryCredentialRow | None) -> FakeImagesRepo:
    repo = FakeImagesRepo()
    if row is not None:
        repo.add_credential(row)
    return repo


@pytest.mark.asyncio
async def test_a_group_credential_is_decrypted_for_its_own_host():
    auth = await resolve_registry_auth(
        image(connection=CONN),
        repo=repo_with(credential()),
        settings=Settings.from_env({}),
        master_key=KEY,
    )
    assert (auth.username, auth.password, auth.server) == ("robot", "s3cret", "ghcr.io")


@pytest.mark.asyncio
async def test_a_docker_hub_credential_matches_every_hub_alias():
    auth = await resolve_registry_auth(
        image(reference="docker.io/org/tool", connection=CONN),
        repo=repo_with(credential(host="index.docker.io")),
        settings=Settings.from_env({}),
        master_key=KEY,
    )
    assert auth is not None and auth.server == "docker.io"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [
        None,
        credential(deleted=True),
        credential(host="quay.io"),
        credential(secrets={"username": "x"}),
    ],
    ids=["missing", "soft-deleted", "another-host", "no-password"],
)
async def test_an_unusable_group_credential_is_gone_never_anonymous(row):
    """C-1 Review Focus 4, at launch: a deleted credential must not turn a
    private image into a public pull, and a credential for another host must
    never be sent to this one."""
    with pytest.raises(RegistryCredentialGone):
        await resolve_registry_auth(
            image(connection=CONN),
            repo=repo_with(row),
            settings=Settings.from_env({}),
            master_key=KEY,
        )


@pytest.mark.asyncio
async def test_no_master_key_is_infrastructure_not_a_verdict():
    with pytest.raises(RegistryAuthUnavailable, match="CREDENTIALS_MASTER_KEY"):
        await resolve_registry_auth(
            image(connection=CONN),
            repo=repo_with(credential()),
            settings=Settings.from_env({}),
            master_key=None,
        )


@pytest.mark.asyncio
async def test_the_deployment_docker_hub_credential_covers_only_docker_io():
    hub = Settings.from_env({"REGISTRY_DOCKERHUB_USER": "robot", "REGISTRY_DOCKERHUB_TOKEN": "pat"})
    repo = repo_with(None)
    auth = await resolve_registry_auth(
        image(reference="docker.io/library/python"), repo=repo, settings=hub, master_key=None
    )
    assert (auth.username, auth.password, auth.server) == ("robot", "pat", "docker.io")
    assert (
        await resolve_registry_auth(image(), repo=repo, settings=hub, master_key=None) is None
    )
    assert (
        await resolve_registry_auth(
            image(reference="docker.io/library/python"),
            repo=repo,
            settings=Settings.from_env({}),
            master_key=None,
        )
        is None
    )
```

Run: `cd services/pipeline && uv run pytest tests/test_image_repo_fake.py tests/test_image_registry_auth.py -q`
Expected: FAIL (`No module named '_images_fake'` / `pipeline.images.repo`).

- [ ] **Step 2: The canonical result and the failure document**

Append to `services/pipeline/src/pipeline/images/scan_result.py`:
```python
# ---------------------------------------------------------------------------
# C-2: what the drain STORES in image_scans.result. The scanner's document is
# untrusted, so the stored copy is rebuilt from the parsed ScanResult (unknown
# keys dropped, types normalized), never the raw bytes.
# ---------------------------------------------------------------------------

ERROR_MAX_CHARS = 1000


def failure_result(kind: str, reference: str, tag: str, error: str) -> dict[str, Any]:
    """A failed scan's document (Decision 1): its identity plus ``error``,
    the shape both C-1 readers accept."""
    message = (error or "").strip() or "scan failed"
    return {
        "version": SCAN_RESULT_VERSION,
        "kind": kind,
        "reference": reference,
        "tag": tag,
        "error": message[:ERROR_MAX_CHARS],
    }


def _finding_json(f: Finding) -> dict[str, Any]:
    return {
        "id": f.id,
        "severity": f.severity,
        "package": f.package,
        "version": f.version,
        "fixed_in": f.fixed_in,
        "kev": f.kev,
        "epss": f.epss,
        "risk": f.risk,
        "published_at": f.published_at,
    }


def scan_result_to_json(result: ScanResult) -> dict[str, Any]:
    """The canonical section 6.4 document for a parsed result."""
    if result.error is not None:
        return failure_result(result.kind, result.reference, result.tag, result.error)
    return {
        "version": SCAN_RESULT_VERSION,
        "kind": result.kind,
        "reference": result.reference,
        "tag": result.tag,
        "digest": result.digest,
        "platform_digest": result.platform_digest,
        "platform": dict(result.platform or {}),
        "size_bytes": result.size_bytes,
        "config": dict(result.config or {}),
        "scanner": dict(result.scanner or {}),
        "sbom_ref": result.sbom_ref,
        "findings_ref": result.findings_ref,
        "counts": dict(result.counts),
        "fixed_counts": dict(result.fixed_counts),
        "kev": list(result.kev),
        "max_risk": result.max_risk,
        "top": [_finding_json(f) for f in result.top],
        "tag_drift": result.tag_drift,
        "error": None,
    }
```

- [ ] **Step 3: The repository seam**

Create `services/pipeline/src/pipeline/images/repo.py`:
```python
"""Repository seam over ``container_images`` + ``image_scans`` (C-2,
container-images spec §4, §8.1, §8.4).

The pipeline reads and writes ROWS here and never DDL (ADR 0001; migration
030 is the app's). The drain and the launch path depend on the ABC, so
their logic is tested against ``tests/_images_fake.py``;
:class:`PgImagesRepo` is the psycopg implementation, exercised by the
DB-gated ``tests/test_integration_images_repo.py``.
"""

from __future__ import annotations

import abc
import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pipeline.images.scan_result import ScanResult

#: The error a stall sweep writes (a scan still `running` long after its
#: timeout: the worker that ran it is gone).
STALLED_SCAN_ERROR = "the scan stalled: the worker running it was lost before it finished"
#: Serialises claims across workers so the running-scan cap holds.
CLAIM_LOCK_KEY = "stac_higher.image_scans.claim"


@dataclass(frozen=True)
class ImageRow:
    """The columns of one ``container_images`` row the pipeline reads."""

    id: str
    reference: str
    tag_at_add: str
    status: str
    digest: str | None = None
    platform_digest: str | None = None
    platform: dict[str, Any] | None = None
    size_bytes: int | None = None
    config: dict[str, Any] | None = None
    sbom_ref: str | None = None
    last_scanned_at: dt.datetime | None = None
    registry_connection_id: str | None = None
    exception_expires_at: dt.datetime | None = None


@dataclass(frozen=True)
class ClaimedScan:
    """A scan row the drain now owns (status flipped to ``running``), with its
    image as it was AFTER the claim."""

    id: str
    kind: str
    requested_by: str
    image: ImageRow


@dataclass(frozen=True)
class RegistryCredentialRow:
    connection_id: str
    protocol: str
    config: dict[str, Any]
    #: raw credential envelope (bytea) -- decrypted only at launch.
    credentials: bytes | None
    deleted: bool


class ImagesRepo(abc.ABC):
    """DB access the scan drain and the launch path depend on."""

    @abc.abstractmethod
    async def get_image(self, image_id: str) -> ImageRow | None:
        """The row by id, or None."""

    @abc.abstractmethod
    async def claim_pending_scan(self, *, max_running: int) -> ClaimedScan | None:
        """Claim the oldest pending scan unless ``max_running`` scans already
        run (deployment-wide). Flips it to ``running`` with ``started_at``,
        and an ADMISSION scan's image from ``pending``/``scan_failed`` to
        ``scanning`` (a rescan leaves the visible status alone, spec §8.1)."""

    @abc.abstractmethod
    async def fail_stalled_scans(self, *, started_before: dt.datetime) -> int:
        """Fail every ``running`` scan started before the cutoff with
        :data:`STALLED_SCAN_ERROR` (an identity-bearing result), flip its
        image from ``pending``/``scanning`` to ``scan_failed``; return how
        many scans were failed."""

    @abc.abstractmethod
    async def finish_scan(
        self,
        scan_id: str,
        *,
        status: str,
        result: dict[str, Any],
        findings_ref: str | None,
        log_ref: str | None,
        executor_handle: str | None,
    ) -> None:
        """Write the terminal scan row (``done``/``failed``) with ``finished_at``."""

    @abc.abstractmethod
    async def record_admission(
        self,
        image_id: str,
        *,
        scan_id: str,
        result: ScanResult,
        verdict: dict[str, Any],
        status: str,
        at: dt.datetime,
    ) -> None:
        """Spec §8.1: fill digest, platform_digest, platform, size_bytes,
        config, sbom_ref, verdict, status, last_scan_id, last_scanned_at."""

    @abc.abstractmethod
    async def record_rescan(
        self,
        image_id: str,
        *,
        scan_id: str,
        verdict: dict[str, Any],
        status: str,
        at: dt.datetime,
    ) -> None:
        """Spec §8.1: only verdict, status, last_scan_id, last_scanned_at."""

    @abc.abstractmethod
    async def find_image_by_digest(
        self, reference: str, digest: str, *, exclude_id: str
    ) -> ImageRow | None:
        """Another row with the same ``(reference, digest)`` (spec §9.1 dedup)."""

    @abc.abstractmethod
    async def merge_admission(
        self,
        *,
        provisional_id: str,
        existing_id: str,
        scan_id: str,
        verdict: dict[str, Any],
        status: str,
        at: dt.datetime,
    ) -> None:
        """Spec §9.1 dedup, atomically: re-point the scan at the existing row,
        record the verdict there (its own SBOM stays), delete the provisional
        row while it is still ``pending``/``scanning``/``scan_failed``."""

    @abc.abstractmethod
    async def mark_image_scan_failed(self, image_id: str) -> None:
        """``pending``/``scanning`` -> ``scan_failed``; any other status stays."""

    @abc.abstractmethod
    async def scan_statuses(self, scan_ids: Sequence[str]) -> dict[str, str]:
        """``{scan_id: status}`` for the reaper; unknown ids are absent."""

    @abc.abstractmethod
    async def get_registry_credential(self, connection_id: str) -> RegistryCredentialRow | None:
        """The connection behind an image's pull credential, soft-deleted
        ones included (``deleted`` says so), or None."""


_IMAGE_COLUMNS = (
    "id::text, reference, tag_at_add, status, digest, platform_digest, platform,"
    " size_bytes, config, sbom_ref, last_scanned_at, registry_connection_id::text,"
    " exception_expires_at"
)


def _to_image(row: Sequence[Any]) -> ImageRow:
    return ImageRow(
        id=row[0],
        reference=row[1],
        tag_at_add=row[2],
        status=row[3],
        digest=row[4],
        platform_digest=row[5],
        platform=row[6],
        size_bytes=int(row[7]) if row[7] is not None else None,
        config=row[8],
        sbom_ref=row[9],
        last_scanned_at=row[10],
        registry_connection_id=row[11],
        exception_expires_at=row[12],
    )


@dataclass
class PgImagesRepo(ImagesRepo):
    """psycopg-backed repo over the process-wide async pool (M3-B)."""

    database_url: str

    async def _connect(self):  # pragma: no cover - thin pool wrapper
        from pipeline.db.pool import get_async_pool

        return (await get_async_pool(self.database_url)).connection()

    async def get_image(self, image_id: str) -> ImageRow | None:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"SELECT {_IMAGE_COLUMNS} FROM stac_higher.container_images"
                " WHERE id = %s::uuid",
                (image_id,),
            )
            row = await cur.fetchone()
        return _to_image(row) if row else None

    async def claim_pending_scan(  # pragma: no cover
        self, *, max_running: int
    ) -> ClaimedScan | None:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                # Held to the end of this transaction: two workers cannot
                # both count "0 running" and both claim.
                await cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (CLAIM_LOCK_KEY,))
                await cur.execute(
                    "SELECT count(*) FROM stac_higher.image_scans WHERE status = 'running'"
                )
                (running,) = await cur.fetchone()
                if running >= max_running:
                    await conn.commit()
                    return None
                await cur.execute(
                    "SELECT id::text, image_id::text, kind, requested_by"
                    " FROM stac_higher.image_scans WHERE status = 'pending'"
                    " ORDER BY requested_at FOR UPDATE SKIP LOCKED LIMIT 1"
                )
                claimed = await cur.fetchone()
                if claimed is None:
                    await conn.commit()
                    return None
                scan_id, image_id, kind, requested_by = claimed
                await cur.execute(
                    "UPDATE stac_higher.image_scans SET status = 'running', started_at = now()"
                    " WHERE id = %s::uuid",
                    (scan_id,),
                )
                if kind == "admission":
                    await cur.execute(
                        "UPDATE stac_higher.container_images"
                        " SET status = 'scanning', updated_at = now()"
                        " WHERE id = %s::uuid AND status IN ('pending', 'scan_failed')",
                        (image_id,),
                    )
                await cur.execute(
                    f"SELECT {_IMAGE_COLUMNS} FROM stac_higher.container_images"
                    " WHERE id = %s::uuid",
                    (image_id,),
                )
                image_row = await cur.fetchone()
            await conn.commit()
        return ClaimedScan(
            id=scan_id, kind=kind, requested_by=requested_by, image=_to_image(image_row)
        )

    async def fail_stalled_scans(  # pragma: no cover
        self, *, started_before: dt.datetime
    ) -> int:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "WITH stalled AS ("
                "  UPDATE stac_higher.image_scans s"
                "     SET status = 'failed', finished_at = now(),"
                "         result = jsonb_build_object("
                "           'version', 1, 'kind', s.kind, 'reference', ci.reference,"
                "           'tag', ci.tag_at_add, 'error', %s::text)"
                "    FROM stac_higher.container_images ci"
                "   WHERE ci.id = s.image_id AND s.status = 'running'"
                "     AND s.started_at < %s"
                "  RETURNING s.image_id"
                "), flipped AS ("
                "  UPDATE stac_higher.container_images"
                "     SET status = 'scan_failed', updated_at = now()"
                "   WHERE id IN (SELECT image_id FROM stalled)"
                "     AND status IN ('pending', 'scanning')"
                "  RETURNING 1"
                ") SELECT count(*) FROM stalled",
                (STALLED_SCAN_ERROR, started_before),
            )
            (count,) = await cur.fetchone()
            await conn.commit()
        return int(count)

    async def finish_scan(  # pragma: no cover
        self,
        scan_id: str,
        *,
        status: str,
        result: dict[str, Any],
        findings_ref: str | None,
        log_ref: str | None,
        executor_handle: str | None,
    ) -> None:
        from psycopg.types.json import Json

        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.image_scans SET status = %s, result = %s,"
                " findings_ref = %s, log_ref = %s, executor_handle = %s, finished_at = now()"
                " WHERE id = %s::uuid",
                (status, Json(result), findings_ref, log_ref, executor_handle, scan_id),
            )
            await conn.commit()

    async def record_admission(  # pragma: no cover
        self,
        image_id: str,
        *,
        scan_id: str,
        result: ScanResult,
        verdict: dict[str, Any],
        status: str,
        at: dt.datetime,
    ) -> None:
        from psycopg.types.json import Json

        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.container_images SET digest = %s, platform_digest = %s,"
                " platform = %s, size_bytes = %s, config = %s, sbom_ref = %s, verdict = %s,"
                " status = %s, last_scan_id = %s::uuid, last_scanned_at = %s,"
                " updated_at = now() WHERE id = %s::uuid",
                (
                    result.digest,
                    result.platform_digest,
                    Json(result.platform),
                    result.size_bytes,
                    Json(result.config),
                    result.sbom_ref,
                    Json(verdict),
                    status,
                    scan_id,
                    at,
                    image_id,
                ),
            )
            await conn.commit()

    async def record_rescan(  # pragma: no cover
        self,
        image_id: str,
        *,
        scan_id: str,
        verdict: dict[str, Any],
        status: str,
        at: dt.datetime,
    ) -> None:
        from psycopg.types.json import Json

        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.container_images SET verdict = %s, status = %s,"
                " last_scan_id = %s::uuid, last_scanned_at = %s, updated_at = now()"
                " WHERE id = %s::uuid",
                (Json(verdict), status, scan_id, at, image_id),
            )
            await conn.commit()

    async def find_image_by_digest(  # pragma: no cover
        self, reference: str, digest: str, *, exclude_id: str
    ) -> ImageRow | None:
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"SELECT {_IMAGE_COLUMNS} FROM stac_higher.container_images"
                " WHERE reference = %s AND digest = %s AND id <> %s::uuid",
                (reference, digest, exclude_id),
            )
            row = await cur.fetchone()
        return _to_image(row) if row else None

    async def merge_admission(  # pragma: no cover
        self,
        *,
        provisional_id: str,
        existing_id: str,
        scan_id: str,
        verdict: dict[str, Any],
        status: str,
        at: dt.datetime,
    ) -> None:
        from psycopg.types.json import Json

        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.image_scans SET image_id = %s::uuid WHERE id = %s::uuid",
                (existing_id, scan_id),
            )
            await conn.execute(
                "UPDATE stac_higher.container_images SET verdict = %s, status = %s,"
                " last_scan_id = %s::uuid, last_scanned_at = %s, updated_at = now()"
                " WHERE id = %s::uuid",
                (Json(verdict), status, scan_id, at, existing_id),
            )
            await conn.execute(
                "DELETE FROM stac_higher.container_images WHERE id = %s::uuid"
                " AND status IN ('pending', 'scanning', 'scan_failed')",
                (provisional_id,),
            )
            await conn.commit()

    async def mark_image_scan_failed(self, image_id: str) -> None:  # pragma: no cover
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.container_images SET status = 'scan_failed',"
                " updated_at = now() WHERE id = %s::uuid AND status IN ('pending', 'scanning')",
                (image_id,),
            )
            await conn.commit()

    async def scan_statuses(  # pragma: no cover
        self, scan_ids: Sequence[str]
    ) -> dict[str, str]:
        ids = list(scan_ids)
        if not ids:
            return {}
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id::text, status FROM stac_higher.image_scans"
                " WHERE id = ANY(%s::uuid[])",
                (ids,),
            )
            rows = await cur.fetchall()
        return {row[0]: row[1] for row in rows}

    async def get_registry_credential(  # pragma: no cover
        self, connection_id: str
    ) -> RegistryCredentialRow | None:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id::text, protocol, config, credentials, deleted_at IS NOT NULL"
                " FROM stac_higher.connections WHERE id = %s::uuid",
                (connection_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return RegistryCredentialRow(
            connection_id=row[0],
            protocol=row[1],
            config=row[2] or {},
            credentials=bytes(row[3]) if row[3] is not None else None,
            deleted=bool(row[4]),
        )
```

- [ ] **Step 4: The fake**

Create `services/pipeline/tests/_images_fake.py`:
```python
"""In-memory ImagesRepo for C-2 unit tests: the behavioural contract of
pipeline/images/repo.py, which its Pg SQL must match."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from pipeline.images.repo import (
    STALLED_SCAN_ERROR,
    ClaimedScan,
    ImageRow,
    ImagesRepo,
    RegistryCredentialRow,
)
from pipeline.images.scan_result import ScanResult, failure_result


@dataclass
class FakeImagesRepo(ImagesRepo):
    images: dict[str, ImageRow] = field(default_factory=dict)
    scans: dict[str, dict[str, Any]] = field(default_factory=dict)
    credentials: dict[str, RegistryCredentialRow] = field(default_factory=dict)
    verdicts: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_scan_ids: dict[str, str] = field(default_factory=dict)
    deleted_images: list[str] = field(default_factory=list)
    clock: dt.datetime = field(
        default_factory=lambda: dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.UTC)
    )

    # -- test set-up --------------------------------------------------------

    def add_image(self, **fields: Any) -> ImageRow:
        row = ImageRow(**fields)
        self.images[row.id] = row
        return row

    def add_scan(
        self,
        scan_id: str,
        image_id: str,
        kind: str = "admission",
        *,
        requested_at: dt.datetime | None = None,
        status: str = "pending",
        started_at: dt.datetime | None = None,
    ) -> None:
        self.scans[scan_id] = {
            "id": scan_id,
            "image_id": image_id,
            "kind": kind,
            "status": status,
            "requested_by": "user-1",
            "requested_at": requested_at or self.clock,
            "started_at": started_at,
            "result": None,
            "findings_ref": None,
            "log_ref": None,
            "executor_handle": None,
        }

    def add_credential(self, row: RegistryCredentialRow) -> None:
        self.credentials[row.connection_id] = row

    def _set(self, image_id: str, **changes: Any) -> None:
        self.images[image_id] = replace(self.images[image_id], **changes)

    # -- ImagesRepo ---------------------------------------------------------

    async def get_image(self, image_id: str) -> ImageRow | None:
        return self.images.get(image_id)

    async def claim_pending_scan(self, *, max_running: int) -> ClaimedScan | None:
        if sum(1 for s in self.scans.values() if s["status"] == "running") >= max_running:
            return None
        pending = sorted(
            (s for s in self.scans.values() if s["status"] == "pending"),
            key=lambda s: s["requested_at"],
        )
        if not pending:
            return None
        scan = pending[0]
        scan["status"] = "running"
        scan["started_at"] = self.clock
        image = self.images[scan["image_id"]]
        if scan["kind"] == "admission" and image.status in ("pending", "scan_failed"):
            self._set(image.id, status="scanning")
        return ClaimedScan(
            id=scan["id"],
            kind=scan["kind"],
            requested_by=scan["requested_by"],
            image=self.images[image.id],
        )

    async def fail_stalled_scans(self, *, started_before: dt.datetime) -> int:
        count = 0
        for scan in self.scans.values():
            if scan["status"] != "running" or scan["started_at"] >= started_before:
                continue
            image = self.images[scan["image_id"]]
            scan["status"] = "failed"
            scan["result"] = failure_result(
                scan["kind"], image.reference, image.tag_at_add, STALLED_SCAN_ERROR
            )
            if image.status in ("pending", "scanning"):
                self._set(image.id, status="scan_failed")
            count += 1
        return count

    async def finish_scan(
        self,
        scan_id: str,
        *,
        status: str,
        result: dict[str, Any],
        findings_ref: str | None,
        log_ref: str | None,
        executor_handle: str | None,
    ) -> None:
        self.scans[scan_id].update(
            status=status,
            result=result,
            findings_ref=findings_ref,
            log_ref=log_ref,
            executor_handle=executor_handle,
        )

    async def record_admission(
        self,
        image_id: str,
        *,
        scan_id: str,
        result: ScanResult,
        verdict: dict[str, Any],
        status: str,
        at: dt.datetime,
    ) -> None:
        self._set(
            image_id,
            digest=result.digest,
            platform_digest=result.platform_digest,
            platform=result.platform,
            size_bytes=result.size_bytes,
            config=result.config,
            sbom_ref=result.sbom_ref,
            status=status,
            last_scanned_at=at,
        )
        self.verdicts[image_id] = verdict
        self.last_scan_ids[image_id] = scan_id

    async def record_rescan(
        self,
        image_id: str,
        *,
        scan_id: str,
        verdict: dict[str, Any],
        status: str,
        at: dt.datetime,
    ) -> None:
        self._set(image_id, status=status, last_scanned_at=at)
        self.verdicts[image_id] = verdict
        self.last_scan_ids[image_id] = scan_id

    async def find_image_by_digest(
        self, reference: str, digest: str, *, exclude_id: str
    ) -> ImageRow | None:
        for row in self.images.values():
            if row.id != exclude_id and row.reference == reference and row.digest == digest:
                return row
        return None

    async def merge_admission(
        self,
        *,
        provisional_id: str,
        existing_id: str,
        scan_id: str,
        verdict: dict[str, Any],
        status: str,
        at: dt.datetime,
    ) -> None:
        self.scans[scan_id]["image_id"] = existing_id
        self._set(existing_id, status=status, last_scanned_at=at)
        self.verdicts[existing_id] = verdict
        self.last_scan_ids[existing_id] = scan_id
        if self.images[provisional_id].status in ("pending", "scanning", "scan_failed"):
            del self.images[provisional_id]
            self.deleted_images.append(provisional_id)

    async def mark_image_scan_failed(self, image_id: str) -> None:
        image = self.images.get(image_id)
        if image is not None and image.status in ("pending", "scanning"):
            self._set(image_id, status="scan_failed")

    async def scan_statuses(self, scan_ids: Sequence[str]) -> dict[str, str]:
        return {s: self.scans[s]["status"] for s in scan_ids if s in self.scans}

    async def get_registry_credential(self, connection_id: str) -> RegistryCredentialRow | None:
        return self.credentials.get(connection_id)
```

- [ ] **Step 5: Pull-credential resolution**

Create `services/pipeline/src/pipeline/images/registry_auth.py`:
```python
"""Which credential pulls a user image (C-2, container-images spec §5, §8.4).

Resolved at launch, handed only to the daemon (``X-Registry-Auth``) or the
scanner's environment, never to user code (ADR 0021):

1. The image row names a group ``registry`` connection: that connection's
   ``{username, password}``, decrypted here. A deleted connection, one of
   another protocol, one configured for a DIFFERENT registry host, or one
   without both secrets is :class:`RegistryCredentialGone` -- never a silent
   fall-back to an anonymous pull (C-1 Review Focus 4), and never a
   credential sent to a host it was not configured for.
2. Otherwise a ``docker.io`` image uses the optional deployment credential
   (``REGISTRY_DOCKERHUB_USER`` / ``_TOKEN``, ISSUES I-125).
3. Otherwise anonymous (``None``).

A missing master key or an undecryptable envelope is
:class:`RegistryAuthUnavailable`: infrastructure, not the run's fault.
"""

from __future__ import annotations

from pipeline.config import Settings
from pipeline.connections.build import AdapterBuildError, decrypt_credentials
from pipeline.connections.registry import (
    REGISTRY_PROTOCOL,
    RegistryConfigError,
    parse_registry_config,
    registry_api_host,
)
from pipeline.connections.repo import ConnectionRow
from pipeline.images.reference import registry_host
from pipeline.images.repo import ImageRow, ImagesRepo
from pipeline.process.executor import RegistryAuth

DOCKER_HUB = "docker.io"


class RegistryCredentialGone(Exception):
    """The image's group credential can no longer be used to pull it."""


class RegistryAuthUnavailable(Exception):
    """The credential exists but this worker cannot decrypt it now."""


async def resolve_registry_auth(
    image: ImageRow,
    *,
    repo: ImagesRepo,
    settings: Settings,
    master_key: bytes | None,
) -> RegistryAuth | None:
    host = registry_host(image.reference)
    if image.registry_connection_id:
        connection_id = image.registry_connection_id
        row = await repo.get_registry_credential(connection_id)
        if row is None or row.deleted or row.protocol != REGISTRY_PROTOCOL:
            raise RegistryCredentialGone(
                f"the registry credential {connection_id} for {image.reference} was deleted"
            )
        try:
            configured = parse_registry_config(row.config).host
        except RegistryConfigError as err:
            raise RegistryCredentialGone(
                f"the registry credential {connection_id} is not usable: {err}"
            ) from err
        if registry_api_host(configured) != registry_api_host(host):
            raise RegistryCredentialGone(
                f"the registry credential {connection_id} is for {configured}, "
                f"the image is on {host}"
            )
        if master_key is None:
            raise RegistryAuthUnavailable(
                "CREDENTIALS_MASTER_KEY is not set, so the credential of a private image "
                "cannot be decrypted"
            )
        try:
            secrets = decrypt_credentials(
                ConnectionRow(
                    id=row.connection_id,
                    name="",
                    protocol=row.protocol,
                    config=row.config,
                    credentials=row.credentials,
                    host_key=None,
                ),
                master_key,
            )
        except AdapterBuildError as err:
            raise RegistryAuthUnavailable(
                f"registry credential {connection_id}: {err}"
            ) from err
        username = secrets.get("username")
        password = secrets.get("password")
        if not (
            isinstance(username, str) and username and isinstance(password, str) and password
        ):
            raise RegistryCredentialGone(
                f"the registry credential {connection_id} has no username and password"
            )
        return RegistryAuth(username=username, password=password, server=host)
    if (
        host == DOCKER_HUB
        and settings.registry_dockerhub_user
        and settings.registry_dockerhub_token
    ):
        return RegistryAuth(
            username=settings.registry_dockerhub_user,
            password=settings.registry_dockerhub_token,
            server=DOCKER_HUB,
        )
    return None
```

- [ ] **Step 6: The DB-gated integration test (skips here; the lead runs it)**

Create `services/pipeline/tests/test_integration_images_repo.py`:
```python
"""PgImagesRepo against a migrated database -- auto-skips unless DATABASE_URL
is set AND migration 030 has been applied (hit any app API route once).

    DATABASE_URL=postgresql://username:password@localhost:5433/postgis \\
        uv run pytest tests/test_integration_images_repo.py
"""

from __future__ import annotations

import datetime as dt
import os
import uuid

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set -- skipping DB integration tests"
)

DIGEST = "sha256:" + "c" * 64


@pytest.fixture
async def seeded():
    import psycopg

    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        cur = await conn.execute("SELECT to_regclass('stac_higher.container_images')")
        if (await cur.fetchone())[0] is None:
            pytest.skip("migration 030 is not applied to this database")
        reference = f"ghcr.io/itest/img-{uuid.uuid4().hex[:12]}"
        cur = await conn.execute(
            "INSERT INTO stac_higher.container_images (reference, tag_at_add, added_by)"
            " VALUES (%s, '1.0', 'itest') RETURNING id::text",
            (reference,),
        )
        (image_id,) = await cur.fetchone()
        cur = await conn.execute(
            "INSERT INTO stac_higher.image_scans (image_id, kind, requested_by)"
            " VALUES (%s::uuid, 'admission', 'itest') RETURNING id::text",
            (image_id,),
        )
        (scan_id,) = await cur.fetchone()
    yield reference, image_id, scan_id
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        await conn.execute(
            "DELETE FROM stac_higher.container_images WHERE reference = %s", (reference,)
        )
    from pipeline.db.pool import close_pools

    await close_pools()


async def test_claim_record_and_finish_round_trip(seeded):
    from pipeline.images.repo import PgImagesRepo
    from pipeline.images.scan_result import SEVERITIES, parse_scan_result

    reference, image_id, scan_id = seeded
    repo = PgImagesRepo(DATABASE_URL)
    # Other pending scans on a shared database may be older than ours; drain
    # until ours is claimed, releasing the others back to pending.
    claimed = await repo.claim_pending_scan(max_running=1000)
    assert claimed is not None
    assert claimed.id == scan_id or claimed.image.reference != reference
    if claimed.id != scan_id:
        pytest.skip("another pending scan exists on this database; run on a quiet stack")
    assert claimed.image.status == "scanning"
    result = parse_scan_result(
        {
            "version": 1, "kind": "admission", "reference": reference, "tag": "1.0",
            "digest": DIGEST, "platform_digest": DIGEST,
            "platform": {"os": "linux", "architecture": "amd64"}, "size_bytes": 10,
            "config": {"user": "", "entrypoint": None, "cmd": None},
            "scanner": {"syft": "1", "grype": "1", "db_built_at": "2026-09-27T00:00:00Z"},
            "sbom_ref": f"scans/{image_id}/{scan_id}/sbom.syft.json",
            "findings_ref": f"scans/{image_id}/{scan_id}/findings.grype.json",
            "counts": dict.fromkeys(SEVERITIES, 0),
            "fixed_counts": dict.fromkeys(SEVERITIES, 0),
            "kev": [], "max_risk": 0.0, "top": [], "tag_drift": None, "error": None,
        }
    )
    now = dt.datetime.now(dt.UTC)
    await repo.record_admission(
        image_id, scan_id=scan_id, result=result, verdict={"pass": True}, status="approved", at=now
    )
    await repo.finish_scan(
        scan_id, status="done", result={"version": 1}, findings_ref=result.findings_ref,
        log_ref=None, executor_handle="c1",
    )
    row = await repo.get_image(image_id)
    assert (row.status, row.digest, row.last_scanned_at is not None) == ("approved", DIGEST, True)
    assert await repo.scan_statuses([scan_id]) == {scan_id: "done"}
    assert await repo.find_image_by_digest(reference, DIGEST, exclude_id=str(uuid.uuid4())) == row
    assert await repo.fail_stalled_scans(started_before=now - dt.timedelta(days=3650)) == 0
```

- [ ] **Step 7: Run the tests**

Run: `cd services/pipeline && uv run pytest tests/test_image_repo_fake.py tests/test_image_registry_auth.py tests/test_integration_images_repo.py -q`
Expected: PASS (the integration test reports SKIPPED without `DATABASE_URL`).

- [ ] **Step 8: Gates and commit**

Run `npm run verify` and `cd services/pipeline && uv run pytest -q && uv run ruff check .`. If ruff's E501 flags a long line in the integration test's literal dict, wrap it without changing its content.
```bash
git add services/pipeline/src/pipeline/images/repo.py services/pipeline/src/pipeline/images/registry_auth.py services/pipeline/src/pipeline/images/scan_result.py services/pipeline/tests/_images_fake.py services/pipeline/tests/test_image_repo_fake.py services/pipeline/tests/test_image_registry_auth.py services/pipeline/tests/test_integration_images_repo.py
git commit -m "feat(pipeline): images repository, pull-credential resolution, canonical scan result (C-2)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The digest-pinned launch path

**Files:**
- Modify: `services/pipeline/src/pipeline/process/launch.py`
- Modify: `services/pipeline/src/pipeline/process/runner.py`
- Modify: `services/pipeline/src/pipeline/jobs/process.py`
- Modify: `services/pipeline/src/pipeline/process/config.py` (module docstring line only)
- Modify: `services/pipeline/tests/test_process_triggers.py` (replace the C-1 test, extend `_run`, append)
- Modify: `services/pipeline/tests/test_process_executor.py` (append)

**Interfaces:**
- Consumes: `ImagesRepo`, `ImageRow`, `resolve_registry_auth`, `RegistryCredentialGone`, `RegistryAuthUnavailable` (Task 4); `RegistryAuth`, `ImagePullFailed`, `RunSpec.user_image/entrypoint/registry_auth` (Task 1); `RUNNER_ENV_VAR`, `BOOTSTRAP_ENTRYPOINT`, `runner_source_b64` (Task 2); `load_image_policy`, `ImagePolicy`, `ImagePolicyError` (C-1).
- Produces (`pipeline.process.launch`): `@dataclass(frozen=True) ResolvedImage(image: str, user_image: bool = False, registry_auth: RegistryAuth | None = None)`; `class ImagePolicyUnavailable(Exception)`; `check_user_image_launchable(runtime: ProcessRuntime, row: ImageRow | None, *, scan_window_days: int, now: dt.datetime) -> None` (raises `ImageUnusable`); `async resolve_run_image(runtime, settings, *, repo: ImagesRepo | None, policy: ImagePolicy | None, master_key: bytes | None, now: dt.datetime) -> ResolvedImage`; `build_run_spec(..., code: str | None, ..., image: ResolvedImage | None = None)`; `execute_run(..., code: str | None, ..., image: ResolvedImage | None = None)`. `resolve_runtime_image` is unchanged (it still refuses kinds 2-3).
- Produces (`pipeline.process.runner.run_one`): new keyword arguments `images_repo: ImagesRepo | None = None`, `image_policy: ImagePolicy | None = None`, `master_key: bytes | None = None`.

- [ ] **Step 1: Write the failing launch tests**

In `services/pipeline/tests/test_process_triggers.py`:

(a) Add these imports, keeping the block isort-sorted (ruff I001): `from _images_fake import FakeImagesRepo` after `from _dispatch_fake import FakeDispatchRepo`; `from pipeline.images.repo import RegistryCredentialRow` after `from pipeline.dispatcher.repo import ItemEvent`; `from pipeline.process.runner_source import BOOTSTRAP_ENTRYPOINT, RUNNER_ENV_VAR` after `from pipeline.process.runner import run_one`; and change `from pipeline.process.executor import ExecutorUnavailable, ExitStatus` to:
```python
from pipeline.process.executor import ExecutorUnavailable, ExitStatus, ImagePullFailed
```

(b) Replace the `_run` helper with:
```python
async def _run(
    run, executor, repo, *, storage_client=None, fetch_remote=None, settings=None, **kwargs
):
    return await run_one(
        run,
        repo=repo,
        executor=executor,
        settings=settings or Settings.from_env({}),
        storage_client=storage_client or FakeStore(),
        resolve_secret=lambda ref: "x",
        now=NOW,
        sts_client=FakeSts(),
        fetch_remote=fetch_remote,
        **kwargs,
    )
```

(c) Delete the whole C-1 test `test_a_user_image_revision_dies_before_anything_launches` (its `@pytest.mark.asyncio` and `@pytest.mark.parametrize` decorators included) and put in its place:
```python
# ---------------------------------------------------------------------------
# C-2: the digest-pinned launch path (container-images spec §8.4)
# ---------------------------------------------------------------------------

IMG = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f"
IMG_DIGEST = "sha256:" + "a" * 64
IMG_REF = "ghcr.io/example/tool"


def user_runtime(kind: str, **extra) -> dict:
    return {
        "kind": kind,
        "image": {"id": IMG, "reference": IMG_REF, "digest": IMG_DIGEST},
        "retry": {"max_attempts": 3},
        **extra,
    }


def images_with(**overrides) -> FakeImagesRepo:
    fields = {
        "id": IMG,
        "reference": IMG_REF,
        "tag_at_add": "1.0",
        "status": "approved",
        "digest": IMG_DIGEST,
        "last_scanned_at": NOW - dt.timedelta(days=1),
    }
    fields.update(overrides)
    images = FakeImagesRepo()
    images.add_image(**fields)
    return images


@pytest.mark.asyncio
async def test_a_kind_2_run_launches_its_pinned_digest_with_the_bootstrap():
    executor = MemoryExecutor()
    result = await _run(
        queued(runtime=user_runtime("inline_python_on_image"), code="print(1)"),
        executor,
        FakeProcessRepo(),
        images_repo=images_with(),
    )
    assert result.status == "succeeded"
    spec = executor.launched[0]
    assert spec.image == f"{IMG_REF}@{IMG_DIGEST}"
    assert spec.user_image is True
    assert spec.entrypoint == BOOTSTRAP_ENTRYPOINT
    assert RUNNER_ENV_VAR in spec.env and "STAC_HIGHER_PROCESS_CODE_B64" in spec.env
    # The forced uid has no home: /tmp is the platform's default.
    assert spec.env["HOME"] == "/tmp"
    assert spec.registry_auth is None


@pytest.mark.asyncio
async def test_a_kind_3_run_carries_no_code_and_its_command_is_cmd():
    executor = MemoryExecutor()
    result = await _run(
        queued(runtime=user_runtime("container", command=["tool", "--run"]), code=None),
        executor,
        FakeProcessRepo(),
        images_repo=images_with(),
    )
    assert result.status == "succeeded"
    spec = executor.launched[0]
    assert spec.cmd == ("tool", "--run")
    assert spec.entrypoint == ()
    assert "STAC_HIGHER_PROCESS_CODE_B64" not in spec.env
    assert RUNNER_ENV_VAR not in spec.env


@pytest.mark.asyncio
async def test_a_flagged_image_still_launches():
    """Spec decision 4: a rescan that newly fails blocks deploys, never runs."""
    executor = MemoryExecutor()
    result = await _run(
        queued(runtime=user_runtime("container"), code=None),
        executor,
        FakeProcessRepo(),
        images_repo=images_with(status="flagged"),
    )
    assert result.status == "succeeded"
    assert len(executor.launched) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"status": "revoked"}, "image_not_approved"),
        ({"status": "rejected"}, "image_not_approved"),
        ({"status": "scanning", "digest": None}, "image_not_approved"),
        ({"digest": "sha256:" + "b" * 64}, "image_digest_mismatch"),
        ({"reference": "ghcr.io/example/other"}, "image_digest_mismatch"),
        ({"last_scanned_at": NOW - dt.timedelta(days=30, seconds=1)}, "image_stale"),
        ({"last_scanned_at": None}, "image_stale"),
    ],
    ids=["revoked", "rejected", "scanning", "digest", "reference", "stale", "never-scanned"],
)
async def test_an_unusable_image_kills_the_run_before_anything_launches(overrides, reason):
    repo = FakeProcessRepo()
    executor = MemoryExecutor()
    result = await _run(
        queued(runtime=user_runtime("inline_python_on_image"), code="print(1)"),
        executor,
        repo,
        images_repo=images_with(**overrides),
    )
    assert result.status == "dead"
    assert repo.finished[0]["error"].startswith(reason)
    assert executor.launched == []


@pytest.mark.asyncio
async def test_the_staleness_boundary_is_inclusive():
    """Mirrors the app gate (C-1 Review Focus 3): exactly the window is fresh."""
    executor = MemoryExecutor()
    result = await _run(
        queued(runtime=user_runtime("container"), code=None),
        executor,
        FakeProcessRepo(),
        images_repo=images_with(last_scanned_at=NOW - dt.timedelta(days=30)),
    )
    assert result.status == "succeeded"


@pytest.mark.asyncio
async def test_an_image_missing_from_the_registry_dies_not_approved():
    repo = FakeProcessRepo()
    result = await _run(
        queued(runtime=user_runtime("container"), code=None),
        MemoryExecutor(),
        repo,
        images_repo=FakeImagesRepo(),
    )
    assert result.status == "dead"
    assert repo.finished[0]["error"].startswith("image_not_approved")


@pytest.mark.asyncio
async def test_a_deleted_registry_credential_dies_as_a_group_mismatch():
    images = images_with(registry_connection_id="5a4b3c2d-1e0f-4a9b-8c7d-6e5f4a3b2c1d")
    images.add_credential(
        RegistryCredentialRow(
            connection_id="5a4b3c2d-1e0f-4a9b-8c7d-6e5f4a3b2c1d",
            protocol="registry",
            config={"host": "ghcr.io"},
            credentials=b"x",
            deleted=True,
        )
    )
    repo = FakeProcessRepo()
    result = await _run(
        queued(runtime=user_runtime("container"), code=None),
        MemoryExecutor(),
        repo,
        images_repo=images,
        master_key=b"k" * 32,
    )
    assert result.status == "dead"
    assert repo.finished[0]["error"].startswith("image_group_mismatch")


@pytest.mark.asyncio
async def test_an_unreadable_policy_requeues_a_user_image_run_without_spending_an_attempt(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("PROCESS_IMAGE_POLICY_FILE", str(tmp_path / "missing.json"))
    repo = FakeProcessRepo()
    executor = MemoryExecutor()
    result = await _run(
        queued(runtime=user_runtime("container"), code=None),
        executor,
        repo,
        images_repo=images_with(),
    )
    assert result.status == "queued"
    assert executor.launched == []


@pytest.mark.asyncio
async def test_an_inline_run_never_reads_the_image_policy(monkeypatch, tmp_path):
    monkeypatch.setenv("PROCESS_IMAGE_POLICY_FILE", str(tmp_path / "missing.json"))
    result = await _run(queued(), MemoryExecutor(), FakeProcessRepo())
    assert result.status == "succeeded"


@pytest.mark.asyncio
async def test_a_failed_pull_spends_an_attempt():
    """A registry that lost the manifest will not find it again on the next
    tick: the run retries on its budget and then dies, never requeues forever."""
    repo = FakeProcessRepo()
    executor = MemoryExecutor(launch_error=ImagePullFailed("pulling x failed: manifest unknown"))
    result = await _run(
        queued(runtime=user_runtime("container"), code=None),
        executor,
        repo,
        images_repo=images_with(),
    )
    assert result.status == "failed"
    assert "manifest unknown" in repo.finished[0]["error"]
```

(The `queued()` default has `attempts=1` and the runtime's `max_attempts` is 3, so `outcome_transition` answers `failed`, the retryable status, not `dead`.)

Append to `services/pipeline/tests/test_process_executor.py`:
```python
# ---------------------------------------------------------------------------
# C-2: the run spec of a user image
# ---------------------------------------------------------------------------


def test_a_revision_may_set_its_own_home_on_a_user_image():
    from pipeline.process.launch import ResolvedImage

    runtime = parse_process_runtime(
        {
            "kind": "container",
            "image": {
                "id": "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f",
                "reference": "ghcr.io/example/tool",
                "digest": "sha256:" + "a" * 64,
            },
        }
    )
    spec = build_run_spec(
        settings(),
        run_id=RUN,
        process_id=PROC,
        runtime=runtime,
        code=None,
        env={"HOME": "/work"},
        credentials=RunCredentials("AK", "SK", "TOK", "b", run_staging_prefix(RUN), None, "r"),
        image=ResolvedImage(image="ghcr.io/example/tool@sha256:" + "a" * 64, user_image=True),
    )
    assert spec.env["HOME"] == "/work"
    assert spec.user_image is True
    assert CODE_ENV_VAR not in spec.env


def test_a_platform_image_gets_no_home_default():
    spec = build_run_spec(
        settings(),
        run_id=RUN,
        process_id=PROC,
        runtime=ProcessRuntime(kind="inline_python"),
        code="print(1)",
        env={},
        credentials=RunCredentials("AK", "SK", "TOK", "b", run_staging_prefix(RUN), None, "r"),
    )
    assert "HOME" not in spec.env
    assert spec.user_image is False and spec.entrypoint == ()
```

Run: `cd services/pipeline && uv run pytest tests/test_process_triggers.py tests/test_process_executor.py -q`
Expected: FAIL (`run_one() got an unexpected keyword argument 'images_repo'`, `ResolvedImage` missing).

- [ ] **Step 2: The launch module**

In `services/pipeline/src/pipeline/process/launch.py`:

(a) Replace the import block (from `import logging` through `from pipeline.process.logs import store_run_log`) with:
```python
import datetime as dt
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pipeline.config import Settings
from pipeline.images.policy import ImagePolicy, ImagePolicyError, load_image_policy
from pipeline.images.registry_auth import RegistryCredentialGone, resolve_registry_auth
from pipeline.images.repo import ImageRow, ImagesRepo
from pipeline.images.status import LAUNCH_STATUSES
from pipeline.metrics import PROCESS_RUN_SECONDS
from pipeline.process.config import NETWORK_LEVELS, USER_IMAGE_KINDS, EnvEntry, ProcessRuntime
from pipeline.process.credentials import RunCredentials, mint_run_credentials
from pipeline.process.docker_executor import CODE_ENV_VAR, encode_code
from pipeline.process.executor import Executor, ExitStatus, RegistryAuth, RunSpec
from pipeline.process.hardware import (
    HardwareProfile,
    HardwareProfileError,
    HardwareProfileSet,
    check_hardware_bounds,
)
from pipeline.process.logs import store_run_log
from pipeline.process.runner_source import (
    BOOTSTRAP_ENTRYPOINT,
    RUNNER_ENV_VAR,
    runner_source_b64,
)
```

(b) Replace the whole block from `class ImageUnusable(Exception):` through the end of the C-1 `check_user_image_launchable` function (the `USER_IMAGE_LAUNCH_UNAVAILABLE` constant included) with:
```python
class ImageUnusable(Exception):
    """A revision on a user-supplied image (kinds 2-3) that must not launch
    (spec §8.4, ADR 0021). The message starts with the gate reason
    (``image_not_approved`` | ``image_digest_mismatch`` | ``image_stale`` |
    ``image_group_mismatch``), the app's deploy-gate vocabulary, and lands in
    ``process_runs.error``. The run dies; it never falls back to another
    image."""


class ImagePolicyUnavailable(Exception):
    """A kind 2/3 run needs the policy's scan window and the policy cannot be
    read: the deployment's fault, so the run is requeued, not killed."""


@dataclass(frozen=True)
class ResolvedImage:
    """What a run launches on. A platform alias resolves to a platform image;
    a user image to ``{reference}@{digest}``, never a tag (spec §8.4)."""

    image: str
    user_image: bool = False
    registry_auth: RegistryAuth | None = None


def check_user_image_launchable(
    runtime: ProcessRuntime,
    row: ImageRow | None,
    *,
    scan_window_days: int,
    now: dt.datetime,
) -> None:
    """Spec §8.4, the launch-time half of the dual enforcement (the app's
    deploy gate is the other). Same order as the gate: exists -> status ->
    reference/digest -> fresh. ``flagged`` launches (spec decision 4);
    staleness blocks even an exception (spec §4.3)."""
    pinned = f"{runtime.image_reference}@{runtime.image_digest}"
    if row is None:
        raise ImageUnusable(
            f"image_not_approved: image {runtime.image_id} ({pinned}) is not in the registry"
        )
    if row.status not in LAUNCH_STATUSES:
        raise ImageUnusable(
            f"image_not_approved: {pinned} is {row.status}; a run launches only on an "
            "approved or flagged image"
        )
    if row.reference != runtime.image_reference or row.digest != runtime.image_digest:
        raise ImageUnusable(
            f"image_digest_mismatch: registry row {row.id} is {row.reference}@{row.digest}, "
            f"the revision pinned {pinned}"
        )
    cutoff = now - dt.timedelta(days=scan_window_days)
    if row.last_scanned_at is None or row.last_scanned_at < cutoff:
        when = row.last_scanned_at.isoformat() if row.last_scanned_at else "never"
        raise ImageUnusable(
            f"image_stale: {pinned} was last scanned {when}; the policy's scan window is "
            f"{scan_window_days} days (ADR 0021)"
        )


async def resolve_run_image(
    runtime: ProcessRuntime,
    settings: Settings,
    *,
    repo: ImagesRepo | None,
    policy: ImagePolicy | None,
    master_key: bytes | None,
    now: dt.datetime,
) -> ResolvedImage:
    """Spec §8.4: kind 1 -> the platform alias (unchanged); kinds 2-3 -> the
    ``container_images`` row re-checked at launch, then the pinned digest and
    its pull credential. The pull itself is the executor's (``user_image``).

    Raises :class:`RuntimeImageUnavailable` / :class:`ImageUnusable` (the run
    dies), :class:`ImagePolicyUnavailable` or
    ``registry_auth.RegistryAuthUnavailable`` (infrastructure: requeue).
    """
    if runtime.kind not in USER_IMAGE_KINDS:
        return ResolvedImage(image=resolve_runtime_image(runtime, settings))
    if repo is None:
        raise ImageUnusable(
            "image_not_approved: this worker has no image registry to check a user image against"
        )
    if policy is None:
        try:
            policy = load_image_policy()
        except ImagePolicyError as err:
            raise ImagePolicyUnavailable(str(err)) from err
    row = await repo.get_image(runtime.image_id or "")
    check_user_image_launchable(runtime, row, scan_window_days=policy.scan_window_days, now=now)
    assert row is not None  # check_user_image_launchable raised otherwise
    try:
        auth = await resolve_registry_auth(row, repo=repo, settings=settings, master_key=master_key)
    except RegistryCredentialGone as err:
        raise ImageUnusable(f"image_group_mismatch: {err}") from err
    return ResolvedImage(
        image=f"{row.reference}@{row.digest}", user_image=True, registry_auth=auth
    )
```

(c) In `resolve_runtime_image`, replace the comment `# Defence in depth: run_one refuses kinds 2-3 before this is reached.` with `# Defence in depth: kinds 2-3 resolve through resolve_run_image, never an alias.`

(d) Replace `build_run_spec` with:
```python
def build_run_spec(
    settings: Settings,
    *,
    run_id: str,
    process_id: str,
    runtime: ProcessRuntime,
    code: str | None,
    env: dict[str, str],
    credentials: RunCredentials,
    extra_env: Mapping[str, str] | None = None,
    profile: HardwareProfile | None = None,
    priority: str = "triggered",
    image: ResolvedImage | None = None,
) -> RunSpec:
    """The COMPLETE environment of a run, assembled in one place.

    Order matters: platform-controlled values are applied AFTER the revision's
    own env, so a revision cannot shadow its storage credentials, its code,
    its runner or the input pointers (``STAC_HIGHER_INPUT_*``, passed as
    ``extra_env`` -- GOES spec §3.1) by declaring variables with those names.
    ``image`` is what ``resolve_run_image`` returned; without it only a
    platform alias can resolve (kinds 2-3 raise).
    """
    resolved = image or ResolvedImage(image=resolve_runtime_image(runtime, settings))
    run_env = dict(env)
    if resolved.user_image:
        # The forced uid 10001 has no passwd entry and so no home (spec §3.2);
        # /tmp is its one writable directory. The revision may set its own.
        run_env.setdefault("HOME", "/tmp")
    run_env.update(extra_env or {})  # platform-provided input pointers
    run_env.update(credentials.as_env())
    if code is not None:
        run_env[CODE_ENV_VAR] = encode_code(code)
    entrypoint: tuple[str, ...] = ()
    cmd: tuple[str, ...] = ()
    if runtime.kind == "inline_python_on_image":
        # Spec §3.1: our runner, by value, on the user's image.
        run_env[RUNNER_ENV_VAR] = runner_source_b64()
        entrypoint = BOOTSTRAP_ENTRYPOINT
    elif runtime.kind == "container" and runtime.command:
        # Spec §3: `command` overrides Cmd, never Entrypoint or User.
        cmd = runtime.command
    run_env["STAC_HIGHER_RUN_ID"] = run_id
    run_env["STAC_HIGHER_PROCESS_ID"] = process_id

    return RunSpec(
        run_id=run_id,
        process_id=process_id,
        image=resolved.image,
        env=run_env,
        cmd=cmd,
        memory_mb=runtime.memory_mb,
        timeout_seconds=runtime.timeout_seconds,
        # Every accepted network profile level is realised as the deployment's
        # process network today: levels above `isolated` are refused by
        # `check_network_cap` until the egress proxy (GOES spec §11) exists.
        network=settings.process_network,
        cpu=runtime.hardware_cpu,
        gpu_count=runtime.hardware_gpu_count,
        profile=profile,
        priority=priority,
        user_image=resolved.user_image,
        entrypoint=entrypoint,
        registry_auth=resolved.registry_auth,
    )
```

(e) In `execute_run`, change the parameter `code: str,` to `code: str | None,`, add the keyword parameter `image: ResolvedImage | None = None,` after `priority: str = "triggered",`, and pass `image=image,` to its `build_run_spec(...)` call.

- [ ] **Step 3: The runner**

In `services/pipeline/src/pipeline/process/runner.py`:

(a) Replace the imports from `pipeline.process.launch` with:
```python
from pipeline.process.launch import (
    HardwareProfileRejected,
    ImagePolicyUnavailable,
    ImageUnusable,
    NetworkCapExceeded,
    RuntimeImageUnavailable,
    SecretResolutionError,
    check_hardware_bounds_for,
    check_network_cap,
    execute_run,
    resolve_run_image,
)
```
and add:
```python
from pipeline.images.policy import ImagePolicy
from pipeline.images.registry_auth import RegistryAuthUnavailable
from pipeline.images.repo import ImagesRepo
```
and change `from pipeline.process.executor import Executor, ExecutorUnavailable` to `from pipeline.process.executor import Executor, ExecutorUnavailable, ImagePullFailed`.

(b) Add to `run_one`'s keyword parameters, after `profiles: HardwareProfileSet | None = None,`:
```python
    images_repo: ImagesRepo | None = None,
    image_policy: ImagePolicy | None = None,
    master_key: bytes | None = None,
```
and replace its docstring's order paragraph with:
```
    Order (GOES spec §3.2, C-2 spec §8.4): parse -> code rule -> network cap
    -> run image (alias, or the user image re-checked by digest) -> hardware
    bounds -> plan inputs (repo reads) -> stage remote inputs -> mint
    credentials + launch (a user image is pulled by digest at launch). A
    failure anywhere before launch means no container ever existed.
```

(c) Delete the C-1 block (the comment `# C-1 (ADR 0021): the contract admits user-image kinds ...` and its `try: check_user_image_launchable(runtime) ... return RunResult(...)`).

(d) Replace `if run.code is None:` with:
```python
    # Kind 3 is the image's own entrypoint: it has no code by contract
    # (spec §3), and a stray code field on it is never run.
    code = None if runtime.kind == "container" else run.code
    if code is None and runtime.kind != "container":
```
(keep the body of that `if` unchanged).

(e) Replace the X-queue block
```python
    try:
        resolve_runtime_image(runtime, settings)
    except RuntimeImageUnavailable as err:
        await _finish(repo, run, "dead", None, str(err), None, at, on_dead=on_dead)
        return RunResult(run.id, "dead", error=str(err))
```
with:
```python
    try:
        resolved = await resolve_run_image(
            runtime,
            settings,
            repo=images_repo,
            policy=image_policy,
            master_key=master_key,
            now=at,
        )
    except (RuntimeImageUnavailable, ImageUnusable) as err:
        await _finish(repo, run, "dead", None, str(err), None, at, on_dead=on_dead)
        return RunResult(run.id, "dead", error=str(err))
    except (ImagePolicyUnavailable, RegistryAuthUnavailable) as err:
        # The deployment's policy or key is missing: our failure, not the
        # process's -- the hardware-profile precedent below.
        return await _requeue_infrastructure(repo, run, at, err)
```
and update its leading comment to: `# X-queue spec §8 + C-2 spec §8.4: the image this run launches on, resolved before anything is staged or minted.`

(f) Replace the hardware-profile `except HardwareProfileError as err:` body with `return await _requeue_infrastructure(repo, run, at, err)`, and add this helper after `_finish`:
```python
async def _requeue_infrastructure(
    repo: ProcessRepo, run: QueuedRun, at: dt.datetime, err: Exception
) -> RunResult:
    """Back to `queued` without spending an attempt: the deployment is at
    fault (a missing policy, profile set or key), not the process."""
    transition = infrastructure_transition(
        now=at, retry_wait_seconds=DEFAULT_RETRY_WAIT_SECONDS, error=str(err)
    )
    PROCESS_RUNS.labels(outcome=transition.status).inc()
    await repo.finish_run(
        run.id,
        status=transition.status,
        error=transition.error,
        log_ref=None,
        next_attempt_at=transition.next_attempt_at,
    )
    logger.warning(
        "process run could not start: deployment configuration unavailable; requeued "
        "without spending an attempt",
        extra={"run_id": run.id, "process_id": run.process_id, "error": str(err)},
    )
    return RunResult(run.id, transition.status, error=str(err))
```
Then run `grep -n "hardware profiles unavailable" services/pipeline/tests/*.py`: if a test asserts the old log message, update it to the new one.

(g) In the `execute_run(...)` call, replace `code=run.code,` with `code=code,` and add `image=resolved,`.

(h) Directly before `except (ExecutorUnavailable, RunCredentialsError) as err:` add:
```python
    except ImagePullFailed as err:
        # Spec §8.4: the registry refused the digest. A per-run outcome that
        # spends an attempt, then dies on budget -- never an endless requeue.
        transition = outcome_transition(
            exit_code=1,
            timed_out=False,
            attempts=run.attempts,
            max_attempts=runtime.max_attempts,
            now=at,
            retry_wait_seconds=DEFAULT_RETRY_WAIT_SECONDS,
            error=str(err),
        )
        await _finish(
            repo,
            run,
            transition.status,
            None,
            transition.error,
            transition.next_attempt_at,
            at,
            on_dead=on_dead,
        )
        return RunResult(run.id, transition.status, error=transition.error)
```

- [ ] **Step 4: Wire the job**

In `services/pipeline/src/pipeline/jobs/process.py`:

(a) Add `from pipeline.images.repo import PgImagesRepo`.

(b) In `_execute_claimed`, after `ingest_repo = PgIngestRepo(settings.database_url)` add `images_repo = PgImagesRepo(settings.database_url)`, and add to the `run_one(...)` call:
```python
                    images_repo=images_repo,
                    master_key=master_key,
```

(c) Replace `process_reap` with:
```python
    async def process_reap(timestamp: int) -> None:
        await process_reap_tick(
            executor=DockerExecutor(docker_host=settings.docker_host),
            repo=_repo(),
            # C-2: scan containers are judged against image_scans.
            scan_statuses=PgImagesRepo(settings.database_url).scan_statuses,
        )
```

- [ ] **Step 5: The config docstring**

In `services/pipeline/src/pipeline/process/config.py`, in the module docstring replace ``path's (``launch.check_user_image_launchable``; C-2 makes that a digest check).`` with ``path's (``launch.resolve_run_image``, the C-2 digest check).``

- [ ] **Step 6: Run the tests**

Run: `cd services/pipeline && uv run pytest tests/test_process_triggers.py tests/test_process_executor.py tests/test_process_reaper.py -q`
Expected: PASS.

- [ ] **Step 7: Gates and commit**

Run `npm run verify` and `cd services/pipeline && uv run pytest -q && uv run ruff check .`.
```bash
git add services/pipeline/src/pipeline/process/launch.py services/pipeline/src/pipeline/process/runner.py services/pipeline/src/pipeline/jobs/process.py services/pipeline/src/pipeline/process/config.py services/pipeline/tests/test_process_triggers.py services/pipeline/tests/test_process_executor.py
git commit -m "feat(pipeline): digest-pinned launch path for user images: re-check at launch, pull by digest, bootstrap and command (C-2)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 6: The scanner image

`services/image-scanner/` is the third platform-built image (spec §6.1). Its Python is a small stdlib package, `stac_higher_scanner`, plus boto3. The pipeline's pytest imports it through `pythonpath` (the `stac_higher_stactools` precedent), so the scanner's logic is tested without Docker, and the scanner's own output is checked against the pipeline's C-1 parser in the same suite.

**Files:**
- Create: `services/image-scanner/Dockerfile`
- Create: `services/image-scanner/stac_higher_scanner/__init__.py`
- Create: `services/image-scanner/stac_higher_scanner/registry.py`
- Create: `services/image-scanner/stac_higher_scanner/summary.py`
- Create: `services/image-scanner/stac_higher_scanner/store.py`
- Create: `services/image-scanner/stac_higher_scanner/scan.py`
- Modify: `services/pipeline/pyproject.toml` (`pythonpath`)
- Modify: `services/process-runtime/docker-bake.hcl` (a target outside the default group)
- Modify: `.github/workflows/containers.yml` (a matrix entry)
- Create: `services/pipeline/tests/test_image_scanner.py`
- Create: `services/pipeline/tests/test_image_scanner_registry.py`

**Interfaces:**
- Produces (scanner package, consumed only by the image and the tests): `registry.RegistryClient(reference, credentials=None, *, transport=urllib_transport).resolve(tag, platform, max_bytes) -> Resolved`; `registry.Response(status, headers, body)`; `registry.RegistryError`, `registry.ImageTooLarge`; `registry.registry_allowed(host, patterns) -> bool`; `registry._SafeRedirect`; `summary.build_summary(doc, *, published=None) -> dict`; `summary.published_dates(db_file, ids) -> dict[str, str]`; `scan.run_scan(cfg, store, tools, workdir, *, client_factory=RegistryClient) -> dict`; `scan.main(env=None) -> int`; `scan.build_result(...)`, `scan.failure_result(...)`.
- The container's contract with the drain (Task 7): the environment variables in spec §6.2 plus `STAC_HIGHER_ALLOWED_REGISTRIES`, `STAC_HIGHER_IMAGE_IDENTITY` (rescan), `STAC_HIGHER_DB_UPDATE`, `GRYPE_DB_UPDATE_URL` (Decision 19); objects written under `STAC_HIGHER_OUTPUT_PREFIX`: `sbom.syft.json`, `sbom.cdx.json` (admission), `findings.grype.json`, `result.json`; exit 0 with a result, 1 with an error result, 2 when no result could be written.

- [ ] **Step 1: Let the pipeline suite import the scanner**

In `services/pipeline/pyproject.toml`, replace
```toml
pythonpath = ["../process-runtime"]
```
with
```toml
pythonpath = ["../process-runtime", "../image-scanner"]
```
and extend the comment above it with the line `# stac_higher_scanner ships in the image-scanner image (C-2); same arrangement.`

- [ ] **Step 2: Write the failing registry tests**

Create `services/pipeline/tests/test_image_scanner_registry.py`:
```python
"""The scanner's registry v2 client (container-images spec §6.3 step 2).

Every response is untrusted: manifests and the config blob must match the
digest that named them, only HTTPS is spoken, and the registry's
Authorization header never follows a redirect to another host."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest
from stac_higher_scanner.registry import (
    Credentials,
    ImageTooLarge,
    RegistryClient,
    RegistryError,
    Response,
    _SafeRedirect,
    registry_allowed,
    sha256_digest,
)


def _doc(obj) -> tuple[bytes, str]:
    body = json.dumps(obj).encode()
    return body, sha256_digest(body)


CONFIG_BODY, CONFIG_DIGEST = _doc(
    {
        "os": "linux",
        "architecture": "amd64",
        "config": {"User": "root", "Entrypoint": ["/entry.sh"], "Cmd": None},
    }
)
MANIFEST_BODY, MANIFEST_DIGEST = _doc(
    {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"digest": CONFIG_DIGEST, "size": len(CONFIG_BODY)},
        "layers": [
            {"digest": "sha256:" + "1" * 64, "size": 100},
            {"digest": "sha256:" + "2" * 64, "size": 50},
        ],
    }
)
INDEX_BODY, INDEX_DIGEST = _doc(
    {
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [
            {"digest": "sha256:" + "9" * 64, "platform": {"os": "linux", "architecture": "arm64"}},
            {
                "digest": "sha256:" + "8" * 64,
                "platform": {"os": "unknown", "architecture": "unknown"},
            },
            {"digest": MANIFEST_DIGEST, "platform": {"os": "linux", "architecture": "amd64"}},
        ],
    }
)


class Registry:
    """A fake registry behind an anonymous-or-Basic Bearer token flow."""

    def __init__(self, tag_digest, docs, *, realm="https://auth.example/token"):
        self.tag_digest = tag_digest
        self.docs = docs
        self.realm = realm
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, method, url, headers):
        self.calls.append((method, url, dict(headers)))
        if url.startswith(self.realm):
            return Response(200, {}, json.dumps({"token": "T0K"}).encode())
        if headers.get("Authorization") != "Bearer T0K":
            challenge = f'Bearer realm="{self.realm}",service="registry.example"'
            return Response(401, {"www-authenticate": challenge}, b"")
        path = url.split("/v2/example/tool/", 1)[1]
        if method == "HEAD" and path == "manifests/1.0":
            return Response(200, {"docker-content-digest": self.tag_digest}, b"")
        _, _, digest = path.partition("/")
        body = self.docs.get(digest)
        return Response(200, {}, body) if body is not None else Response(404, {}, b"")


DOCS = {INDEX_DIGEST: INDEX_BODY, MANIFEST_DIGEST: MANIFEST_BODY, CONFIG_DIGEST: CONFIG_BODY}


def client(registry, credentials=None) -> RegistryClient:
    return RegistryClient("ghcr.io/example/tool", credentials, transport=registry)


def test_an_index_resolves_to_the_policy_platform_manifest():
    resolved = client(Registry(INDEX_DIGEST, DOCS)).resolve("1.0", "linux/amd64", 10_000)
    assert resolved.digest == INDEX_DIGEST
    assert resolved.platform_digest == MANIFEST_DIGEST
    assert resolved.size_bytes == 150
    assert resolved.platform == {"os": "linux", "architecture": "amd64"}
    assert resolved.config == {"user": "root", "entrypoint": ["/entry.sh"], "cmd": None}


def test_a_single_platform_manifest_is_its_own_platform_digest():
    resolved = client(Registry(MANIFEST_DIGEST, DOCS)).resolve("1.0", "linux/amd64", 10_000)
    assert resolved.digest == resolved.platform_digest == MANIFEST_DIGEST


def test_an_image_above_the_size_cap_is_refused_before_any_layer_is_read():
    registry = Registry(INDEX_DIGEST, DOCS)
    with pytest.raises(ImageTooLarge, match="image_too_large"):
        client(registry).resolve("1.0", "linux/amd64", 149)
    # Nothing past the manifests was requested: no config blob, no layer.
    assert not any("/blobs/" in url for _m, url, _h in registry.calls)


def test_a_document_that_does_not_match_its_digest_is_refused():
    tampered = dict(DOCS)
    tampered[MANIFEST_DIGEST] = MANIFEST_BODY + b" "
    with pytest.raises(RegistryError, match="does not match its digest"):
        client(Registry(INDEX_DIGEST, tampered)).resolve("1.0", "linux/amd64", 10_000)


def test_no_manifest_for_the_policy_platform_is_refused():
    with pytest.raises(RegistryError, match="linux/s390x"):
        client(Registry(INDEX_DIGEST, DOCS)).resolve("1.0", "linux/s390x", 10_000)


def test_the_token_request_asks_for_pull_only_and_sends_basic_credentials():
    registry = Registry(INDEX_DIGEST, DOCS)
    client(registry, Credentials("robot", "s3cret")).resolve("1.0", "linux/amd64", 10_000)
    token_calls = [(u, h) for _m, u, h in registry.calls if u.startswith(registry.realm)]
    assert len(token_calls) == 1  # the token is reused
    url, headers = token_calls[0]
    assert "scope=repository%3Aexample%2Ftool%3Apull" in url
    assert "service=registry.example" in url
    assert headers["Authorization"].startswith("Basic ")


def test_an_anonymous_token_request_carries_no_authorization():
    registry = Registry(INDEX_DIGEST, DOCS)
    client(registry).resolve("1.0", "linux/amd64", 10_000)
    _u, headers = next((u, h) for _m, u, h in registry.calls if u.startswith(registry.realm))
    assert "Authorization" not in headers


def test_a_token_realm_that_is_not_https_is_refused():
    registry = Registry(INDEX_DIGEST, DOCS, realm="http://auth.example/token")
    with pytest.raises(RegistryError, match="not https"):
        client(registry, Credentials("robot", "s3cret")).resolve("1.0", "linux/amd64", 10_000)


def test_docker_hub_references_talk_to_registry_1():
    assert RegistryClient("docker.io/library/python").base == (
        "https://registry-1.docker.io/v2/library/python"
    )


def test_credentials_never_print():
    assert "s3cret" not in repr(Credentials("robot", "s3cret"))


def _redirect(newurl):
    request = urllib.request.Request(
        "https://ghcr.io/v2/example/tool/blobs/sha256:x",
        headers={"Authorization": "Bearer T0K"},
    )
    return _SafeRedirect().redirect_request(request, None, 307, "Temporary Redirect", {}, newurl)


def test_a_redirect_to_another_host_drops_the_registry_token():
    assert _redirect("https://cdn.example/blob").get_header("Authorization") is None


def test_a_redirect_on_the_same_host_keeps_it():
    assert _redirect("https://ghcr.io/v2/other").get_header("Authorization") == "Bearer T0K"


def test_a_redirect_to_http_is_refused():
    with pytest.raises(urllib.error.HTTPError):
        _redirect("http://cdn.example/blob")


def test_registry_patterns_match_the_pipeline_rule():
    from pipeline.images.policy import registry_allowed as pipeline_rule

    patterns = ("docker.io", "ghcr.io", "*.dkr.ecr.*.amazonaws.com")
    for host in (
        "docker.io",
        "GHCR.io",
        "123456789012.dkr.ecr.us-gov-west-1.amazonaws.com",
        "evil.io",
        "a.b.dkr.ecr.x.amazonaws.com",
        "registry.example.com:5000",
    ):
        assert registry_allowed(host, patterns) == pipeline_rule(host, patterns), host
```

Run: `cd services/pipeline && uv run pytest tests/test_image_scanner_registry.py -q`
Expected: FAIL (`No module named 'stac_higher_scanner'`).

- [ ] **Step 3: The registry client**

Create `services/image-scanner/stac_higher_scanner/__init__.py`:
```python
"""The platform image scanner (C-2, container-images spec §6, ADR 0021).

Runs inside ``services/image-scanner``'s image as a platform run: it reads
its job from the environment, resolves and SBOMs an image (Syft), matches
it (Grype, with KEV and EPSS), writes its objects under its own scan
prefix, and writes ``result.json`` last. Standard library plus boto3."""
```

Create `services/image-scanner/stac_higher_scanner/registry.py`:
```python
"""Registry v2 client for the scanner (container-images spec §6.3, step 2).

Stdlib only. Resolves a tag to its manifest (or index) digest, picks the
policy platform's manifest, sums its layer sizes and reads the image config,
all before any layer blob is fetched, so an oversized image is refused for
the cost of a few small requests.

Every response is untrusted: each manifest and the config blob are verified
against the digest that named them, bodies are capped, and only HTTPS is
spoken. A redirect (registries send blob reads to a CDN) never carries the
registry's Authorization header to another host. No message ever names a
credential.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

INDEX_TYPES = frozenset(
    {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)
MANIFEST_TYPES = frozenset(
    {
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    }
)
ACCEPT = ", ".join(sorted(INDEX_TYPES | MANIFEST_TYPES))
DOCKER_HUB_HOSTS = frozenset({"docker.io", "index.docker.io", "registry-1.docker.io"})
DOCKER_HUB_API_HOST = "registry-1.docker.io"
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
TIMEOUT_SECONDS = 30
USER_AGENT = "stac-higher-image-scanner"

_DIGEST_RE = re.compile(r"sha256:[a-f0-9]{64}")
_CHALLENGE_PARAM_RE = re.compile(r'(\w+)="([^"]*)"')
_LABEL_RE = re.compile(r"[a-z0-9-]+")


class RegistryError(Exception):
    """The registry refused, failed, or answered something unusable."""


class ImageTooLarge(RegistryError):
    """The layers sum above the policy's ``max_image_size_mb``."""


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str = field(repr=False)


@dataclass(frozen=True)
class Response:
    status: int
    #: lower-cased header names
    headers: Mapping[str, str]
    body: bytes


Transport = Callable[[str, str, Mapping[str, str]], Response]


@dataclass(frozen=True)
class Resolved:
    digest: str
    platform_digest: str
    size_bytes: int
    config: dict[str, Any]
    platform: dict[str, str]


def split_reference(reference: str) -> tuple[str, str]:
    host, _, repository = reference.partition("/")
    if not host or not repository:
        raise RegistryError(f"{reference!r} is not a normalized image reference")
    return host, repository


def api_host(host: str) -> str:
    """Docker Hub's short names are served by registry-1.docker.io."""
    return DOCKER_HUB_API_HOST if host in DOCKER_HUB_HOSTS else host


def registry_allowed(host: str, patterns) -> bool:
    """``*`` is exactly one DNS label; the host is case-folded; a port must
    match literally. The same rule as ``pipeline.images.policy`` (a test pins
    them equal), copied because the scanner ships without the pipeline."""
    labels = host.lower().split(".")
    for pattern in patterns:
        want = pattern.split(".")
        if len(want) == len(labels) and all(
            (_LABEL_RE.fullmatch(have) is not None) if w == "*" else w == have
            for w, have in zip(want, labels, strict=True)
        ):
            return True
    return False


def parse_platform(value: str) -> tuple[str, str, str | None]:
    parts = value.split("/")
    if len(parts) not in (2, 3) or not all(parts):
        raise RegistryError(f"platform must be os/architecture[/variant], got {value!r}")
    return parts[0], parts[1], parts[2] if len(parts) == 3 else None


def sha256_digest(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def pick_platform(index: dict[str, Any], os_: str, arch: str, variant: str | None) -> str:
    for entry in index.get("manifests") or []:
        if not isinstance(entry, dict):
            continue
        plat = entry.get("platform") if isinstance(entry.get("platform"), dict) else {}
        if (
            plat.get("os") == os_
            and plat.get("architecture") == arch
            and (variant is None or plat.get("variant") == variant)
        ):
            digest = entry.get("digest")
            if isinstance(digest, str) and _DIGEST_RE.fullmatch(digest):
                return digest
    wanted = f"{os_}/{arch}" + (f"/{variant}" if variant else "")
    raise RegistryError(f"the image index has no {wanted} manifest")


def image_config(blob: dict[str, Any]) -> dict[str, Any]:
    """The spec §3.2 config record: USER, ENTRYPOINT, CMD, as information."""
    cfg = blob.get("config") if isinstance(blob.get("config"), dict) else {}

    def str_list(value: Any) -> list[str] | None:
        if not isinstance(value, list):
            return None
        return [v for v in value if isinstance(v, str)]

    user = cfg.get("User")
    return {
        "user": user if isinstance(user, str) else "",
        "entrypoint": str_list(cfg.get("Entrypoint")),
        "cmd": str_list(cfg.get("Cmd")),
    }


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Follow a registry's redirect (blob reads go to a CDN) without handing
    the registry's token to the CDN, and never to plain HTTP."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if target.scheme != "https":
            raise urllib.error.HTTPError(
                newurl, code, "refusing a redirect that is not https", headers, fp
            )
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and target.netloc != urllib.parse.urlsplit(req.full_url).netloc:
            new.remove_header("Authorization")
        return new


_OPENER = urllib.request.build_opener(_SafeRedirect())


def urllib_transport(
    method: str, url: str, headers: Mapping[str, str]
) -> Response:  # pragma: no cover - network
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise RegistryError("refusing a registry URL that is not https")
    request = urllib.request.Request(
        url, method=method, headers={"User-Agent": USER_AGENT, **headers}
    )
    try:
        with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as resp:
            body = b"" if method == "HEAD" else resp.read(MAX_DOCUMENT_BYTES + 1)
            return Response(resp.status, {k.lower(): v for k, v in resp.headers.items()}, body)
    except urllib.error.HTTPError as err:
        body = err.read(64 * 1024) if err.fp is not None else b""
        found = err.headers.items() if err.headers is not None else []
        return Response(err.code, {k.lower(): v for k, v in found}, body)
    except (urllib.error.URLError, OSError) as err:
        raise RegistryError(f"{parts.hostname} unreachable: {type(err).__name__}") from err


class RegistryClient:
    """One repository on one registry, read-only (pull scope)."""

    def __init__(
        self,
        reference: str,
        credentials: Credentials | None = None,
        *,
        transport: Transport = urllib_transport,
    ) -> None:
        self.host, self.repository = split_reference(reference)
        self.base = f"https://{api_host(self.host)}/v2/{self.repository}"
        self.credentials = credentials
        self._transport = transport
        self._authorization: str | None = None

    def _basic(self) -> str | None:
        if self.credentials is None:
            return None
        raw = f"{self.credentials.username}:{self.credentials.password}".encode()
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def _authorize(self, challenge: str) -> str:
        scheme, _, params = challenge.strip().partition(" ")
        if scheme.lower() == "basic":
            basic = self._basic()
            if basic is None:
                raise RegistryError(f"{self.host} requires credentials and none were given")
            return basic
        if scheme.lower() != "bearer":
            raise RegistryError(f"{self.host} sent an unsupported auth challenge")
        fields = {k.lower(): v for k, v in _CHALLENGE_PARAM_RE.findall(params)}
        realm = fields.get("realm", "")
        if urllib.parse.urlsplit(realm).scheme != "https":
            raise RegistryError(f"{self.host} names a token endpoint that is not https")
        query = {"scope": f"repository:{self.repository}:pull"}
        if fields.get("service"):
            query["service"] = fields["service"]
        url = f"{realm}{'&' if '?' in realm else '?'}{urllib.parse.urlencode(query)}"
        headers: dict[str, str] = {}
        basic = self._basic()
        if basic is not None:
            headers["Authorization"] = basic
        resp = self._transport("GET", url, headers)
        if resp.status != 200:
            raise RegistryError(f"the token endpoint of {self.host} returned {resp.status}")
        try:
            doc = json.loads(resp.body)
        except ValueError as err:
            raise RegistryError(f"the token endpoint of {self.host} sent malformed JSON") from err
        token = (doc.get("token") or doc.get("access_token")) if isinstance(doc, dict) else None
        if not isinstance(token, str) or not token:
            raise RegistryError(f"the token endpoint of {self.host} returned no token")
        return f"Bearer {token}"

    def _send(self, method: str, url: str, accept: str | None) -> Response:
        headers: dict[str, str] = {}
        if accept:
            headers["Accept"] = accept
        if self._authorization:
            headers["Authorization"] = self._authorization
        resp = self._transport(method, url, headers)
        if resp.status != 401:
            return resp
        self._authorization = self._authorize(resp.headers.get("www-authenticate", ""))
        headers["Authorization"] = self._authorization
        return self._transport(method, url, headers)

    def _document(self, path: str, digest: str, accept: str | None) -> dict[str, Any]:
        resp = self._send("GET", f"{self.base}/{path}/{digest}", accept)
        if resp.status != 200:
            raise RegistryError(f"GET {path}/{digest} on {self.host} returned {resp.status}")
        if len(resp.body) > MAX_DOCUMENT_BYTES:
            raise RegistryError(f"{path}/{digest} on {self.host} is too large")
        if sha256_digest(resp.body) != digest:
            raise RegistryError(f"{path}/{digest} on {self.host} does not match its digest")
        try:
            doc = json.loads(resp.body)
        except ValueError as err:
            raise RegistryError(f"{path}/{digest} on {self.host} is not JSON") from err
        if not isinstance(doc, dict):
            raise RegistryError(f"{path}/{digest} on {self.host} is not a JSON object")
        return doc

    def head_tag(self, tag: str) -> str:
        """Tag -> digest with a HEAD, which Docker Hub does not count as a pull."""
        url = f"{self.base}/manifests/{urllib.parse.quote(tag, safe='')}"
        resp = self._send("HEAD", url, ACCEPT)
        if resp.status != 200:
            raise RegistryError(f"HEAD manifests/{tag} on {self.host} returned {resp.status}")
        digest = resp.headers.get("docker-content-digest", "")
        if not _DIGEST_RE.fullmatch(digest):
            raise RegistryError(f"{self.host} reported no sha256 digest for tag {tag!r}")
        return digest

    def resolve(self, tag: str, platform: str, max_bytes: int) -> Resolved:
        want_os, want_arch, want_variant = parse_platform(platform)
        digest = self.head_tag(tag)
        top = self._document("manifests", digest, ACCEPT)
        if top.get("mediaType") in INDEX_TYPES or isinstance(top.get("manifests"), list):
            platform_digest = pick_platform(top, want_os, want_arch, want_variant)
            manifest = self._document("manifests", platform_digest, ACCEPT)
        else:
            platform_digest, manifest = digest, top
        layers = manifest.get("layers")
        if not isinstance(layers, list) or not layers:
            raise RegistryError(f"manifest {platform_digest} lists no layers")
        size = 0
        for layer in layers:
            layer_size = layer.get("size") if isinstance(layer, dict) else None
            if isinstance(layer_size, bool) or not isinstance(layer_size, int) or layer_size < 0:
                raise RegistryError(f"manifest {platform_digest} has a layer without a size")
            size += layer_size
        if size > max_bytes:
            raise ImageTooLarge(
                f"image_too_large: {size} bytes of layers exceed the policy's {max_bytes}"
            )
        config_ref = manifest.get("config")
        config_digest = config_ref.get("digest") if isinstance(config_ref, dict) else None
        if not isinstance(config_digest, str) or not _DIGEST_RE.fullmatch(config_digest):
            raise RegistryError(f"manifest {platform_digest} names no config digest")
        blob = self._document("blobs", config_digest, None)
        if blob.get("os") != want_os or blob.get("architecture") != want_arch:
            raise RegistryError(
                f"the image is {blob.get('os')}/{blob.get('architecture')}, "
                f"the policy requires {platform}"
            )
        return Resolved(
            digest=digest,
            platform_digest=platform_digest,
            size_bytes=size,
            config=image_config(blob),
            platform={"os": want_os, "architecture": want_arch},
        )
```

Run: `cd services/pipeline && uv run pytest tests/test_image_scanner_registry.py -q`
Expected: PASS.

- [ ] **Step 4: Write the failing summary, result and image tests**

Create `services/pipeline/tests/test_image_scanner.py`:
```python
"""The scanner's summary, result and entrypoint (container-images spec §6).

The decisive property is at the bottom of each test: whatever the scanner
writes, the pipeline's own C-1 parser (the drain's reader) accepts it."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from stac_higher_scanner import scan as scan_module
from stac_higher_scanner.registry import RegistryError, Resolved
from stac_higher_scanner.scan import (
    EXIT_OK,
    EXIT_PLATFORM_ERROR,
    EXIT_SCAN_FAILED,
    ScanConfig,
    ScanRefused,
    Tools,
    failure_result,
    main,
    run_scan,
    syft_auth_env,
    tool_env,
)
from stac_higher_scanner.summary import (
    MAX_TOP,
    build_summary,
    findings_from_grype,
    published_dates,
)

from pipeline.images.scan_result import parse_scan_result

REPO = Path(__file__).resolve().parents[3]
PREFIX = "scans/7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f/0d9e8f7a-6b5c-4d3e-9f2a-1b0c9d8e7f6a/"


def match(vid, severity="High", *, package="libxml2", version="2.12.7", fixed=None,
          risk=0.1, epss=None, kev=False):
    vuln = {
        "id": vid,
        "severity": severity,
        "fix": {"versions": [fixed] if fixed else [], "state": "fixed" if fixed else "not-fixed"},
        "risk": risk,
    }
    if epss is not None:
        vuln["epss"] = [{"cve": vid, "epss": epss, "percentile": 0.9, "date": "2026-09-20"}]
    if kev:
        vuln["knownExploited"] = [{"cve": vid, "knownRansomwareCampaignUse": "Unknown"}]
    return {"vulnerability": vuln, "artifact": {"name": package, "version": version}}


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


def test_duplicate_matches_count_once_and_severities_are_normalized():
    doc = {
        "matches": [match("CVE-1", "Critical"), match("CVE-1", "Critical"), match("CVE-2", "Weird")]
    }
    findings = findings_from_grype(doc)
    assert [(f.id, f.severity) for f in findings] == [("CVE-1", "critical"), ("CVE-2", "unknown")]


def test_a_fix_counts_only_when_grype_says_fixed():
    doc = {"matches": [match("CVE-1", fixed="2.12.9"), match("CVE-2")]}
    summary = build_summary(doc)
    assert summary["counts"]["high"] == 2
    assert summary["fixed_counts"]["high"] == 1
    assert {f["id"]: f["fixed_in"] for f in summary["top"]} == {"CVE-1": "2.12.9", "CVE-2": None}


def test_nan_and_infinity_never_leave_the_scanner():
    """C-1's parser rejects non-finite numbers; the scanner must never emit one."""
    doc = {
        "matches": [
            match("CVE-1", risk=float("nan"), epss=float("inf")),
            match("CVE-2", risk=float("inf"), epss=2.5),
        ]
    }
    summary = build_summary(doc)
    json.dumps(summary, allow_nan=False)  # raises on NaN/inf
    by_id = {f["id"]: f for f in summary["top"]}
    assert by_id["CVE-1"]["risk"] == 0.0 and by_id["CVE-1"]["epss"] is None
    assert by_id["CVE-2"]["epss"] == 1.0
    assert summary["max_risk"] == 0.0


def test_kev_is_the_complete_sorted_list():
    doc = {"matches": [*(match(f"CVE-{i}", "Low", kev=True) for i in (3, 1, 2)), match("CVE-9")]}
    assert build_summary(doc)["kev"] == ["CVE-1", "CVE-2", "CVE-3"]


def test_top_favours_what_the_policy_blocks_on_over_raw_risk():
    """C-1 plan decision 11: evaluate() can only NAME what is in `top`. Thirty
    risky MEDIUMs must not push out a low-risk fixed HIGH with a high EPSS or
    an unfixed CRITICAL."""
    doc = {
        "matches": [
            *(match(f"CVE-M{i}", "Medium", package=f"p{i}", risk=0.9) for i in range(30)),
            match("CVE-H", "High", package="h", fixed="1.1", risk=0.01, epss=0.5),
            match("CVE-C", "Critical", package="c", risk=0.02),
        ]
    }
    top = build_summary(doc)["top"]
    ids = [f["id"] for f in top]
    assert len(top) == MAX_TOP == 25
    assert "CVE-H" in ids and "CVE-C" in ids
    # Emitted by risk, descending (the fixture's documented order).
    assert [f["risk"] for f in top] == sorted((f["risk"] for f in top), reverse=True)


def test_published_dates_come_from_the_grype_db(tmp_path):
    db = tmp_path / "vulnerability.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE vulnerability_handles (name TEXT, published_date DATETIME)")
        conn.executemany(
            "INSERT INTO vulnerability_handles VALUES (?, ?)",
            [("CVE-1", "2026-07-01 00:00:00+00:00"), ("cve-1", "2026-06-01 00:00:00+00:00")],
        )
    assert published_dates(db, ["CVE-1", "CVE-2"]) == {"cve-1": "2026-06-01"}
    assert published_dates(tmp_path / "missing.db", ["CVE-1"]) == {}
    summary = build_summary(
        {"matches": [match("CVE-1", "Critical")]}, published=lambda ids: {"cve-1": "2026-06-01"}
    )
    assert summary["top"][0]["published_at"] == "2026-06-01"


# ---------------------------------------------------------------------------
# run_scan end to end, with fake tools, store and registry
# ---------------------------------------------------------------------------


class FakeStore:
    def __init__(self, files=None):
        self.puts: dict[str, bytes] = {}
        self.json: dict[str, dict] = {}
        self.files = files or {}

    def put_file(self, key, path, content_type="application/json"):
        self.puts[key] = Path(path).read_bytes()

    def put_json(self, key, doc):
        self.json[key] = json.loads(json.dumps(doc, allow_nan=False))

    def get_file(self, key, path):
        Path(path).write_bytes(self.files[key])


class FakeTools:
    def __init__(self, grype_doc):
        self.grype_doc = grype_doc
        self.syft_calls: list[tuple[str, dict]] = []
        self.updated = False

    def syft(self, source, syft_json, cyclonedx, auth_env):
        self.syft_calls.append((source, auth_env))
        Path(syft_json).write_text("{}")
        Path(cyclonedx).write_text("{}")

    def grype(self, sbom, out):
        assert Path(sbom).exists()
        Path(out).write_text(json.dumps(self.grype_doc))

    def grype_db_update(self):
        self.updated = True
        return True

    def grype_db_status(self):
        return {"built": "2026-09-20T06:00:00Z", "path": "/nonexistent/6"}


RESOLVED = Resolved(
    digest="sha256:" + "a" * 64,
    platform_digest="sha256:" + "b" * 64,
    size_bytes=812,
    config={"user": "", "entrypoint": ["/entry.sh"], "cmd": None},
    platform={"os": "linux", "architecture": "amd64"},
)


class FakeClient:
    def __init__(self, reference, credentials=None):
        self.reference = reference
        self.credentials = credentials

    def resolve(self, tag, platform, max_bytes):
        return RESOLVED


def config(**overrides) -> ScanConfig:
    base = {
        "scan_id": "0d9e8f7a-6b5c-4d3e-9f2a-1b0c9d8e7f6a",
        "kind": "admission",
        "reference": "docker.io/library/python",
        "tag": "3.12-slim",
        "platform": "linux/amd64",
        "max_image_bytes": 4096 * 1024 * 1024,
        "allowed_registries": ("docker.io", "ghcr.io"),
        "bucket": "stac-higher",
        "prefix": PREFIX,
    }
    base.update(overrides)
    return ScanConfig(**base)


def test_an_admission_scan_writes_its_objects_and_a_result_the_pipeline_accepts(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SYFT_VERSION", "1.52.0")
    monkeypatch.setenv("GRYPE_VERSION", "0.119.0")
    store = FakeStore()
    tools = FakeTools({"matches": [match("CVE-1", "High", fixed="2.12.9", epss=0.3, risk=0.4)]})
    doc = run_scan(config(db_update=True), store, tools, tmp_path, client_factory=FakeClient)
    assert set(store.puts) == {
        f"{PREFIX}sbom.syft.json",
        f"{PREFIX}sbom.cdx.json",
        f"{PREFIX}findings.grype.json",
    }
    assert tools.syft_calls[0][0] == f"registry:docker.io/library/python@{RESOLVED.platform_digest}"
    assert tools.updated is True
    parsed = parse_scan_result(json.loads(json.dumps(doc, allow_nan=False)))
    assert parsed.error is None
    assert parsed.digest == RESOLVED.digest and parsed.size_bytes == 812
    assert parsed.sbom_ref == f"{PREFIX}sbom.syft.json"
    assert parsed.findings_ref == f"{PREFIX}findings.grype.json"
    assert parsed.scanner == {
        "syft": "1.52.0",
        "grype": "0.119.0",
        "db_built_at": "2026-09-20T06:00:00Z",
    }
    assert parsed.top[0].id == "CVE-1"


def test_a_rescan_reads_the_stored_sbom_and_echoes_the_rows_identity(tmp_path):
    sbom_key = "scans/7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f/older/sbom.syft.json"
    identity = {
        "digest": RESOLVED.digest,
        "platform_digest": RESOLVED.platform_digest,
        "platform": RESOLVED.platform,
        "size_bytes": 812,
        "config": RESOLVED.config,
    }
    store = FakeStore(files={sbom_key: b"{}"})
    tools = FakeTools({"matches": []})
    doc = run_scan(
        config(kind="rescan", sbom_key=sbom_key, identity=identity),
        store,
        tools,
        tmp_path,
        client_factory=FakeClient,
    )
    assert tools.syft_calls == []  # SBOM-only: nothing is pulled
    parsed = parse_scan_result(doc)
    assert parsed.kind == "rescan" and parsed.sbom_ref == sbom_key
    assert parsed.digest == RESOLVED.digest


def test_a_registry_outside_the_policy_is_refused_before_any_request(tmp_path):
    with pytest.raises(ScanRefused, match="registry_not_allowed"):
        run_scan(
            config(reference="quay.io/org/tool"),
            FakeStore(),
            FakeTools({"matches": []}),
            tmp_path,
            client_factory=lambda *a: pytest.fail("the registry must not be contacted"),
        )


def test_the_failure_result_is_the_c1_shape():
    doc = failure_result("admission", "docker.io/library/python", "3.12-slim", "x" * 5000)
    assert parse_scan_result(doc).error == "x" * 1000


def test_syft_gets_the_credential_for_the_right_authority():
    from stac_higher_scanner.registry import Credentials

    hub = config(credentials=Credentials("robot", "pat"))
    assert syft_auth_env(hub)["SYFT_REGISTRY_AUTH_AUTHORITY"] == "index.docker.io"
    ghcr = config(reference="ghcr.io/org/tool", credentials=Credentials("robot", "pat"))
    assert syft_auth_env(ghcr)["SYFT_REGISTRY_AUTH_AUTHORITY"] == "ghcr.io"
    assert syft_auth_env(config()) == {}


def test_the_tools_never_inherit_the_registry_or_storage_credentials():
    env = tool_env(
        {
            "PATH": "/usr/bin",
            "REGISTRY_PASSWORD": "pat",
            "AWS_SECRET_ACCESS_KEY": "s",
            "GRYPE_DB_CACHE_DIR": "/opt/grype-db",
        }
    )
    assert "REGISTRY_PASSWORD" not in env and "AWS_SECRET_ACCESS_KEY" not in env
    assert env["GRYPE_DB_VALIDATE_AGE"] == "false"
    assert env["GRYPE_DB_AUTO_UPDATE"] == "false"
    assert isinstance(Tools(env=env), Tools)


# ---------------------------------------------------------------------------
# main(): exit codes and the result it always tries to write
# ---------------------------------------------------------------------------


def _env(**overrides):
    base = {
        "STAC_HIGHER_SCAN_ID": "0d9e8f7a-6b5c-4d3e-9f2a-1b0c9d8e7f6a",
        "STAC_HIGHER_SCAN_KIND": "admission",
        "STAC_HIGHER_IMAGE_REF": "docker.io/library/python",
        "STAC_HIGHER_IMAGE_TAG": "3.12-slim",
        "STAC_HIGHER_PLATFORM": "linux/amd64",
        "STAC_HIGHER_MAX_IMAGE_BYTES": "4294967296",
        "STAC_HIGHER_ALLOWED_REGISTRIES": "docker.io,ghcr.io",
        "STAC_HIGHER_OUTPUT_BUCKET": "stac-higher",
        "STAC_HIGHER_OUTPUT_PREFIX": PREFIX,
        "REGISTRY_USERNAME": "robot",
        "REGISTRY_PASSWORD": "pat",
    }
    base.update(overrides)
    return base


def _use_store(monkeypatch, store) -> None:
    monkeypatch.setattr(
        scan_module.ObjectStore, "from_env", classmethod(lambda cls, env: store)
    )


def test_main_without_its_job_cannot_start(monkeypatch):
    _use_store(monkeypatch, FakeStore())
    assert main({"PATH": "/usr/bin"}) == EXIT_PLATFORM_ERROR


def test_main_writes_an_error_result_and_exits_1_when_the_scan_fails(monkeypatch):
    store = FakeStore()
    _use_store(monkeypatch, store)

    def boom(*args, **kwargs):
        raise RegistryError("HEAD manifests/3.12-slim on docker.io returned 404")

    monkeypatch.setattr(scan_module, "run_scan", boom)
    assert main(_env()) == EXIT_SCAN_FAILED
    result = store.json[f"{PREFIX}result.json"]
    parsed = parse_scan_result(result)
    assert parsed.error == "HEAD manifests/3.12-slim on docker.io returned 404"
    assert "pat" not in json.dumps(result)


def test_main_writes_the_result_and_exits_0(monkeypatch):
    store = FakeStore()
    _use_store(monkeypatch, store)
    written = {"version": 1, "error": None, "sentinel": 1}
    monkeypatch.setattr(scan_module, "run_scan", lambda *a, **k: written)
    assert main(_env()) == EXIT_OK
    assert store.json[f"{PREFIX}result.json"]["sentinel"] == 1


# ---------------------------------------------------------------------------
# the image: text pins (no test builds it -- CI's containers.yml does)
# ---------------------------------------------------------------------------

DOCKERFILE = (REPO / "services" / "image-scanner" / "Dockerfile").read_text()


def test_the_binaries_are_pinned_and_checksum_verified():
    for name in ("SYFT", "GRYPE"):
        assert re.search(rf"ARG {name}_VERSION=\d+\.\d+\.\d+", DOCKERFILE)
        for arch in ("AMD64", "ARM64"):
            assert re.search(rf"ARG {name}_SHA256_{arch}=[a-f0-9]{{64}}\b", DOCKERFILE)
    assert DOCKERFILE.count("sha256sum -c -") == 2


def test_the_db_is_baked_and_never_refused_for_its_age():
    assert "grype db update" in DOCKERFILE
    assert "GRYPE_DB_AUTO_UPDATE=false" in DOCKERFILE
    assert "GRYPE_DB_VALIDATE_AGE=false" in DOCKERFILE


def test_the_scanner_runs_as_a_non_root_user_with_no_volume():
    assert "USER scanner" in DOCKERFILE
    assert "VOLUME" not in DOCKERFILE


def test_boto3_is_the_pin_the_runtime_image_already_vouches_for():
    runtime = (REPO / "services" / "process-runtime" / "Dockerfile").read_text()
    pin = re.compile(r'"boto3==([0-9.]+)"')
    assert pin.search(DOCKERFILE).group(1) == pin.search(runtime).group(1)


def test_ci_and_bake_build_the_scanner():
    workflow = (REPO / ".github" / "workflows" / "containers.yml").read_text()
    assert "services/image-scanner/Dockerfile" in workflow
    bake = (REPO / "services" / "process-runtime" / "docker-bake.hcl").read_text()
    assert 'target "image-scanner"' in bake
    assert "stac-higher-image-scanner:local" in bake
```

Run: `cd services/pipeline && uv run pytest tests/test_image_scanner.py -q`
Expected: FAIL at collection (`stac_higher_scanner.scan` / `.summary` do not exist, and the Dockerfile read fails).

- [ ] **Step 5: The summary**

Create `services/image-scanner/stac_higher_scanner/summary.py`:
```python
"""Grype's JSON -> the result's summary (container-images spec §6.4).

- A finding is one (vulnerability, package, version) match; duplicates count
  once. Severity is Grype's, lower-cased, ``unknown`` when unrecognised.
- ``fixed_in`` is set only when Grype says ``fixed`` and names a version.
- ``kev`` is the COMPLETE list of KEV ids (C-1 contract), from Grype's
  ``knownExploited`` records.
- Numbers are finite, always: a NaN or infinite EPSS becomes null (clamped
  into [0, 1] otherwise) and a NaN or infinite risk becomes 0.0 (C-1's
  parser rejects non-finite numbers; the scanner never emits them).
- ``top`` (<= 25) is CHOSEN by what the policy blocks on -- KEV, then fixed
  CRITICAL, unfixed CRITICAL, fixed HIGH by EPSS, unfixed HIGH, then the
  rest by risk -- and EMITTED by risk, descending. C-1's ``evaluate()`` can
  only name a finding that is in ``top`` (C-1 plan decision 11).
- ``published_at`` is not in Grype's JSON; it comes from the Grype DB's
  ``vulnerability_handles.published_date`` (Decision 3), and is null when
  the lookup fails -- which ``evaluate()`` treats as an unknown age, i.e.
  fail closed.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

SEVERITIES = ("critical", "high", "medium", "low", "negligible", "unknown")
MAX_TOP = 25


@dataclass(frozen=True)
class Finding:
    id: str
    severity: str
    package: str
    version: str
    fixed_in: str | None
    kev: bool
    epss: float | None
    risk: float
    published_at: str | None = None


def _finite(value: Any, *, lo: float = 0.0, hi: float | None = None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    number = max(lo, number)
    return min(hi, number) if hi is not None else number


def _kev_ids(vuln: dict[str, Any]) -> list[str]:
    records = vuln.get("knownExploited")
    if not isinstance(records, list) or not records:
        return []
    ids = [r.get("cve") for r in records if isinstance(r, dict) and isinstance(r.get("cve"), str)]
    ids = [i.strip() for i in ids if i and i.strip()]
    return ids or [str(vuln.get("id") or "").strip()]


def findings_from_grype(doc: dict[str, Any]) -> list[Finding]:
    seen: set[tuple[str, str, str]] = set()
    findings: list[Finding] = []
    for m in doc.get("matches") or []:
        if not isinstance(m, dict):
            continue
        vuln = m.get("vulnerability") if isinstance(m.get("vulnerability"), dict) else {}
        artifact = m.get("artifact") if isinstance(m.get("artifact"), dict) else {}
        vid = str(vuln.get("id") or "").strip()
        package = str(artifact.get("name") or "").strip()
        if not vid or not package:
            continue
        version = str(artifact.get("version") or "")
        key = (vid, package, version)
        if key in seen:
            continue
        seen.add(key)
        severity = str(vuln.get("severity") or "unknown").strip().lower()
        if severity not in SEVERITIES:
            severity = "unknown"
        fix = vuln.get("fix") if isinstance(vuln.get("fix"), dict) else {}
        versions = [v for v in fix.get("versions") or [] if isinstance(v, str) and v.strip()]
        fixed_in = versions[0] if fix.get("state") == "fixed" and versions else None
        epss_values = [
            _finite(e.get("epss"), hi=1.0) for e in vuln.get("epss") or [] if isinstance(e, dict)
        ]
        epss_known = [e for e in epss_values if e is not None]
        findings.append(
            Finding(
                id=vid,
                severity=severity,
                package=package,
                version=version,
                fixed_in=fixed_in,
                kev=bool(_kev_ids(vuln)),
                epss=max(epss_known) if epss_known else None,
                risk=_finite(vuln.get("risk")) or 0.0,
            )
        )
    return findings


def _tier(f: Finding) -> int:
    if f.kev:
        return 0
    if f.severity == "critical":
        return 1 if f.fixed_in else 2
    if f.severity == "high":
        return 3 if f.fixed_in else 4
    return 5


def select_top(findings: Iterable[Finding], limit: int = MAX_TOP) -> list[Finding]:
    ranked = sorted(
        findings,
        key=lambda f: (
            _tier(f),
            -(f.epss or 0.0) if _tier(f) == 3 else 0.0,
            -f.risk,
            f.id,
            f.package,
        ),
    )
    return sorted(ranked[:limit], key=lambda f: (-f.risk, f.id, f.package))


def published_dates(db_file: Path, ids: Iterable[str]) -> dict[str, str]:
    """``{lower-cased id: YYYY-MM-DD}`` from the Grype DB (schema v6), the
    earliest published date per name. Any failure is an empty answer."""
    wanted = sorted({i for i in ids if i})
    if not wanted or not Path(db_file).is_file():
        return {}
    placeholders = ",".join("?" for _ in wanted)
    query = (
        "SELECT lower(name), MIN(published_date) FROM vulnerability_handles"
        f" WHERE name COLLATE NOCASE IN ({placeholders}) AND published_date IS NOT NULL"
        " GROUP BY lower(name)"
    )
    try:
        with sqlite3.connect(f"file:{db_file}?mode=ro", uri=True) as conn:
            rows = conn.execute(query, wanted).fetchall()
    except sqlite3.Error:
        return {}
    return {name: str(date)[:10] for name, date in rows if name and date}


def build_summary(
    doc: dict[str, Any],
    *,
    published: Callable[[list[str]], dict[str, str]] | None = None,
) -> dict[str, Any]:
    findings = findings_from_grype(doc)
    counts = dict.fromkeys(SEVERITIES, 0)
    fixed_counts = dict.fromkeys(SEVERITIES, 0)
    kev: set[str] = set()
    for f in findings:
        counts[f.severity] += 1
        if f.fixed_in:
            fixed_counts[f.severity] += 1
    for m in doc.get("matches") or []:
        vuln = m.get("vulnerability") if isinstance(m, dict) else None
        if isinstance(vuln, dict):
            kev.update(i for i in _kev_ids(vuln) if i)
    top = select_top(findings)
    dates = published([f.id for f in top]) if published is not None else {}
    top = [replace(f, published_at=dates.get(f.id.lower())) for f in top]
    return {
        "counts": counts,
        "fixed_counts": fixed_counts,
        "kev": sorted(kev),
        "max_risk": max((f.risk for f in findings), default=0.0),
        "top": [asdict(f) for f in top],
    }
```

- [ ] **Step 6: The store**

Create `services/image-scanner/stac_higher_scanner/store.py`:
```python
"""Object storage for the scanner: the run-scoped STS credential the drain
minted (spec §6.2, the ADR 0014 path) arrives in the standard AWS variables,
and the bucket in STAC_HIGHER_OUTPUT_BUCKET. Writes succeed only under the
scan's own prefix; the credential makes that true, not this code."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class ObjectStore:
    def __init__(self, client: Any, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> ObjectStore:  # pragma: no cover
        import boto3
        from botocore.client import Config

        env = dict(os.environ if env is None else env)
        endpoint = env.get("AWS_ENDPOINT_URL_S3") or env.get("AWS_ENDPOINT_URL") or None
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=env.get("AWS_REGION") or "us-east-1",
            aws_access_key_id=env.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=env.get("AWS_SECRET_ACCESS_KEY"),
            aws_session_token=env.get("AWS_SESSION_TOKEN"),
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if endpoint else "auto"},
            ),
        )
        return cls(client, env["STAC_HIGHER_OUTPUT_BUCKET"])

    def put_file(self, key: str, path: Path, content_type: str = "application/json") -> None:
        self.client.upload_file(
            str(path), self.bucket, key, ExtraArgs={"ContentType": content_type}
        )

    def put_json(self, key: str, doc: dict[str, Any]) -> None:
        body = json.dumps(doc, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=body, ContentType="application/json"
        )

    def get_file(self, key: str, path: Path) -> None:
        self.client.download_file(self.bucket, key, str(path))
```

- [ ] **Step 7: The entrypoint**

Create `services/image-scanner/stac_higher_scanner/scan.py`:
```python
"""The scanner's entrypoint (container-images spec §6.3).

Admission: refuse a registry outside the policy, resolve tag -> digest ->
platform manifest (refusing an oversized image before any layer is read),
``syft`` the platform digest into syft-json + CycloneDX, ``grype`` the SBOM,
write the objects, then ``result.json``. Rescan: ``grype`` the stored SBOM
only (nothing is pulled). Drift (spec §8.2) is C-4's.

Exit codes: 0 a result was written; 1 an error result was written; 2 no
result could be written (the drain records that as a failed scan itself).
The registry credential is read once and removed from the environment the
binaries inherit; no message names it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stac_higher_scanner.registry import (
    DOCKER_HUB_HOSTS,
    Credentials,
    RegistryClient,
    RegistryError,
    registry_allowed,
    split_reference,
)
from stac_higher_scanner.store import ObjectStore
from stac_higher_scanner.summary import build_summary, published_dates

RESULT_VERSION = 1
EXIT_OK = 0
EXIT_SCAN_FAILED = 1
EXIT_PLATFORM_ERROR = 2
ERROR_MAX_CHARS = 1000
#: go-containerregistry names Docker Hub `index.docker.io`; Syft matches its
#: auth entries against that name (Decision 24: C-5 confirms it live).
SYFT_AUTHORITY_DOCKER_HUB = "index.docker.io"
_TOOL_ENV_KEEP = ("PATH", "HOME", "TMPDIR", "GRYPE_DB_CACHE_DIR", "GRYPE_DB_UPDATE_URL")


class ScanRefused(Exception):
    """The scan cannot proceed; the message is the result's ``error``."""


@dataclass(frozen=True)
class ScanConfig:
    scan_id: str
    kind: str
    reference: str
    tag: str
    platform: str
    max_image_bytes: int
    allowed_registries: tuple[str, ...]
    bucket: str
    prefix: str
    sbom_key: str | None = None
    identity: dict[str, Any] | None = None
    db_update: bool = False
    credentials: Credentials | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, env: dict[str, str]) -> ScanConfig:
        def need(name: str) -> str:
            value = (env.get(name) or "").strip()
            if not value:
                raise ScanRefused(f"{name} is not set")
            return value

        kind = need("STAC_HIGHER_SCAN_KIND")
        if kind not in ("admission", "rescan"):
            raise ScanRefused(f"STAC_HIGHER_SCAN_KIND must be admission or rescan, got {kind!r}")
        prefix = need("STAC_HIGHER_OUTPUT_PREFIX")
        if not prefix.endswith("/"):
            raise ScanRefused("STAC_HIGHER_OUTPUT_PREFIX must end with /")
        sbom_key = identity = None
        if kind == "rescan":
            sbom_key = need("STAC_HIGHER_SBOM_KEY")
            identity = json.loads(need("STAC_HIGHER_IMAGE_IDENTITY"))
            if not isinstance(identity, dict):
                raise ScanRefused("STAC_HIGHER_IMAGE_IDENTITY must be a JSON object")
        username = env.get("REGISTRY_USERNAME") or ""
        password = env.get("REGISTRY_PASSWORD") or ""
        return cls(
            scan_id=need("STAC_HIGHER_SCAN_ID"),
            kind=kind,
            reference=need("STAC_HIGHER_IMAGE_REF"),
            tag=need("STAC_HIGHER_IMAGE_TAG"),
            platform=need("STAC_HIGHER_PLATFORM"),
            max_image_bytes=int(need("STAC_HIGHER_MAX_IMAGE_BYTES")),
            allowed_registries=tuple(
                p.strip() for p in need("STAC_HIGHER_ALLOWED_REGISTRIES").split(",") if p.strip()
            ),
            bucket=need("STAC_HIGHER_OUTPUT_BUCKET"),
            prefix=prefix,
            sbom_key=sbom_key,
            identity=identity,
            db_update=env.get("STAC_HIGHER_DB_UPDATE") == "1",
            credentials=Credentials(username, password) if username and password else None,
        )


def tool_env(env: dict[str, str]) -> dict[str, str]:
    """The binaries' environment, built from scratch: no AWS keys, no
    registry secret (Syft gets the latter per call, Grype never does)."""
    out = {k: env[k] for k in _TOOL_ENV_KEEP if env.get(k)}
    out.update(
        {
            "GRYPE_DB_AUTO_UPDATE": "false",
            # The baked DB is days old by design; its age is reported, not
            # refused (Decision 4).
            "GRYPE_DB_VALIDATE_AGE": "false",
            "GRYPE_CHECK_FOR_APP_UPDATE": "false",
            "SYFT_CHECK_FOR_APP_UPDATE": "false",
        }
    )
    return out


def syft_auth_env(cfg: ScanConfig) -> dict[str, str]:
    if cfg.credentials is None:
        return {}
    host, _ = split_reference(cfg.reference)
    authority = SYFT_AUTHORITY_DOCKER_HUB if host in DOCKER_HUB_HOSTS else host
    return {
        "SYFT_REGISTRY_AUTH_AUTHORITY": authority,
        "SYFT_REGISTRY_AUTH_USERNAME": cfg.credentials.username,
        "SYFT_REGISTRY_AUTH_PASSWORD": cfg.credentials.password,
    }


@dataclass
class Tools:
    """The two binaries, behind a seam the tests replace."""

    env: dict[str, str]

    def _run(self, args: list[str], *, extra: dict[str, str] | None = None, stdout=None) -> None:
        done = subprocess.run(
            args,
            env={**self.env, **(extra or {})},
            stdout=stdout if stdout is not None else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if done.returncode != 0:
            tail = done.stderr.decode("utf-8", "replace")[-2000:]
            name = " ".join(args[:2])
            print(f"[scanner] {name} exited {done.returncode}\n{tail}", file=sys.stderr)
            raise ScanRefused(f"{' '.join(args[:2])} exited {done.returncode}")

    def syft(self, source: str, syft_json: Path, cyclonedx: Path, auth_env: dict[str, str]) -> None:
        self._run(
            [
                "syft",
                "scan",
                source,
                "-o",
                f"syft-json={syft_json}",
                "-o",
                f"cyclonedx-json={cyclonedx}",
                "-q",
            ],
            extra=auth_env,
        )

    def grype(self, sbom: Path, out: Path) -> None:
        with out.open("wb") as fh:
            self._run(["grype", f"sbom:{sbom}", "-o", "json", "-q"], stdout=fh)

    def grype_db_update(self) -> bool:
        try:
            self._run(["grype", "db", "update"])
        except ScanRefused:
            return False
        return True

    def grype_db_status(self) -> dict[str, Any]:
        done = subprocess.run(
            ["grype", "db", "status", "-o", "json"], env=self.env, capture_output=True, check=False
        )
        try:
            doc = json.loads(done.stdout or b"{}")
        except ValueError:
            return {}
        return doc if isinstance(doc, dict) else {}


def db_file(status: dict[str, Any], env: dict[str, str]) -> Path:
    path = str(status.get("path") or "")
    if not path:
        return Path(env.get("GRYPE_DB_CACHE_DIR", "/opt/grype-db")) / "6" / "vulnerability.db"
    p = Path(path)
    return p if p.suffix == ".db" else p / "vulnerability.db"


def scanner_info(status: dict[str, Any], env: dict[str, str]) -> dict[str, str]:
    return {
        "syft": env.get("SYFT_VERSION") or "unknown",
        "grype": env.get("GRYPE_VERSION") or "unknown",
        "db_built_at": str(status.get("built") or "unknown"),
    }


def build_result(
    cfg: ScanConfig,
    identity: dict[str, Any],
    *,
    sbom_ref: str,
    findings_ref: str,
    scanner: dict[str, str],
    summary: dict[str, Any],
) -> dict[str, Any]:
    """The spec §6.4 document, key for key."""
    return {
        "version": RESULT_VERSION,
        "kind": cfg.kind,
        "reference": cfg.reference,
        "tag": cfg.tag,
        "digest": identity["digest"],
        "platform_digest": identity["platform_digest"],
        "platform": identity["platform"],
        "size_bytes": identity["size_bytes"],
        "config": identity["config"],
        "scanner": scanner,
        "sbom_ref": sbom_ref,
        "findings_ref": findings_ref,
        "counts": summary["counts"],
        "fixed_counts": summary["fixed_counts"],
        "kev": summary["kev"],
        "max_risk": summary["max_risk"],
        "top": summary["top"],
        "tag_drift": None,
        "error": None,
    }


def failure_result(kind: str, reference: str, tag: str, error: str) -> dict[str, Any]:
    """The C-1 failure shape: identity plus ``error``."""
    message = (error or "").strip() or "scan failed"
    return {
        "version": RESULT_VERSION,
        "kind": kind,
        "reference": reference,
        "tag": tag,
        "error": message[:ERROR_MAX_CHARS],
    }


def run_scan(
    cfg: ScanConfig,
    store: ObjectStore,
    tools: Tools,
    workdir: Path,
    *,
    client_factory: Callable[..., RegistryClient] = RegistryClient,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    env = dict(os.environ if env is None else env)
    sbom = workdir / "sbom.syft.json"
    findings = workdir / "findings.grype.json"
    if cfg.kind == "admission":
        host, _ = split_reference(cfg.reference)
        if not registry_allowed(host, cfg.allowed_registries):
            raise ScanRefused(
                f"registry_not_allowed: {host} is outside the policy's allowed_registries"
            )
        resolved = client_factory(cfg.reference, cfg.credentials).resolve(
            cfg.tag, cfg.platform, cfg.max_image_bytes
        )
        cyclonedx = workdir / "sbom.cdx.json"
        tools.syft(
            f"registry:{cfg.reference}@{resolved.platform_digest}",
            sbom,
            cyclonedx,
            syft_auth_env(cfg),
        )
        sbom_ref = f"{cfg.prefix}sbom.syft.json"
        store.put_file(sbom_ref, sbom)
        store.put_file(f"{cfg.prefix}sbom.cdx.json", cyclonedx)
        identity = {
            "digest": resolved.digest,
            "platform_digest": resolved.platform_digest,
            "platform": resolved.platform,
            "size_bytes": resolved.size_bytes,
            "config": resolved.config,
        }
    else:
        assert cfg.sbom_key is not None and cfg.identity is not None
        store.get_file(cfg.sbom_key, sbom)
        sbom_ref = cfg.sbom_key
        identity = cfg.identity
    if cfg.db_update and not tools.grype_db_update():
        print("[scanner] grype db update failed; scanning with the baked database", file=sys.stderr)
    status = tools.grype_db_status()
    tools.grype(sbom, findings)
    findings_ref = f"{cfg.prefix}findings.grype.json"
    store.put_file(findings_ref, findings)
    grype_doc = json.loads(findings.read_text())
    if not isinstance(grype_doc, dict):
        raise ScanRefused("grype wrote something that is not a JSON object")
    database = db_file(status, env)
    summary = build_summary(grype_doc, published=lambda ids: published_dates(database, ids))
    return build_result(
        cfg,
        identity,
        sbom_ref=sbom_ref,
        findings_ref=findings_ref,
        scanner=scanner_info(status, env),
        summary=summary,
    )


def describe(exc: BaseException) -> str:
    if isinstance(exc, (ScanRefused, RegistryError)):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def main(env: dict[str, str] | None = None) -> int:
    env = dict(os.environ if env is None else env)
    try:
        cfg = ScanConfig.from_env(env)
        store = ObjectStore.from_env(env)
    except Exception as exc:  # no job, no store: nothing can be recorded
        print(f"[scanner] cannot start: {describe(exc)}", file=sys.stderr)
        return EXIT_PLATFORM_ERROR
    # The credential now lives only in cfg; the binaries never inherit it.
    for name in ("REGISTRY_USERNAME", "REGISTRY_PASSWORD"):
        os.environ.pop(name, None)
        env.pop(name, None)
    tools = Tools(env=tool_env(env))
    code = EXIT_OK
    with tempfile.TemporaryDirectory(prefix="scan-") as tmp:
        try:
            doc = run_scan(cfg, store, tools, Path(tmp), env=env)
        except Exception as exc:  # every failure becomes the result's error
            traceback.print_exc()
            doc = failure_result(cfg.kind, cfg.reference, cfg.tag, describe(exc))
            code = EXIT_SCAN_FAILED
    try:
        store.put_json(f"{cfg.prefix}result.json", doc)
    except Exception as exc:
        print(f"[scanner] result.json could not be written: {describe(exc)}", file=sys.stderr)
        return EXIT_PLATFORM_ERROR
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
```

- [ ] **Step 8: The Dockerfile**

Create `services/image-scanner/Dockerfile`:
```dockerfile
# The platform-built image scanner (container-images spec §6.1, ADR 0021).
#
# python:3.12-slim + pinned Syft and Grype release binaries (versions and
# per-architecture sha256 in the ARG block, verified at build) + the Grype
# vulnerability DB baked at build time, so an air-gapped deployment has a
# usable DB the day the image was built (spec §14 decision 5). It runs as a
# platform run through the pipeline's Executor seam with the process
# posture: its own container, limits, no platform credentials, and a
# run-scoped storage credential for its scan prefix (spec §6.2).
#
# Build (repo root):
#   docker buildx bake -f services/process-runtime/docker-bake.hcl image-scanner
# or:
#   docker build -t stac-higher-image-scanner:local services/image-scanner
FROM python:3.12-slim-bookworm

ARG SYFT_VERSION=1.52.0
ARG SYFT_SHA256_AMD64=caeedb81fb0491615f1ebd1761e4145d41ee86dd2cc7bf80669f9f5ad9d6133d
ARG SYFT_SHA256_ARM64=c46d5e4c28e12aa4c5becfaa343ef1c7f89045b6b895f2c21d471c62db09c706
ARG GRYPE_VERSION=0.119.0
ARG GRYPE_SHA256_AMD64=3fa2dc4b924621ab65404cf08d0b8438d896d80ab949c9d5a4ca283c36004c9b
ARG GRYPE_SHA256_ARM64=29f0ec7c549ddb0e2b6a0ca714851f7399438afc399b80c12808e065edc9a8f8
ARG TARGETARCH

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SYFT_VERSION=${SYFT_VERSION} \
    GRYPE_VERSION=${GRYPE_VERSION} \
    GRYPE_DB_CACHE_DIR=/opt/grype-db \
    GRYPE_DB_AUTO_UPDATE=false \
    GRYPE_DB_VALIDATE_AGE=false \
    GRYPE_CHECK_FOR_APP_UPDATE=false \
    SYFT_CHECK_FOR_APP_UPDATE=false \
    PYTHONPATH=/opt/stac-higher

# Release assets, checksum-verified; curl leaves in the same layer.
RUN set -eu; \
    arch="${TARGETARCH:-amd64}"; \
    case "$arch" in \
      amd64) syft_sum="$SYFT_SHA256_AMD64"; grype_sum="$GRYPE_SHA256_AMD64" ;; \
      arm64) syft_sum="$SYFT_SHA256_ARM64"; grype_sum="$GRYPE_SHA256_ARM64" ;; \
      *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ca-certificates; \
    cd /tmp; \
    curl -fsSLo syft.tar.gz "https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}/syft_${SYFT_VERSION}_linux_${arch}.tar.gz"; \
    echo "${syft_sum}  syft.tar.gz" | sha256sum -c -; \
    curl -fsSLo grype.tar.gz "https://github.com/anchore/grype/releases/download/v${GRYPE_VERSION}/grype_${GRYPE_VERSION}_linux_${arch}.tar.gz"; \
    echo "${grype_sum}  grype.tar.gz" | sha256sum -c -; \
    tar -xzf syft.tar.gz -C /usr/local/bin syft; \
    tar -xzf grype.tar.gz -C /usr/local/bin grype; \
    rm -f syft.tar.gz grype.tar.gz; \
    apt-get purge -y curl; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/*

# boto3 is the runtime image's pin (a test holds them equal). The DB is
# baked here; `IMAGE_SCANNER_DB_UPDATE` refreshes it at scan start where the
# deployment allows egress (spec §6.1).
RUN pip install --no-cache-dir "boto3==1.43.49" \
 && useradd --create-home --uid 10001 scanner \
 && mkdir -p /opt/grype-db \
 && grype db update \
 && chown -R scanner:scanner /opt/grype-db

COPY stac_higher_scanner /opt/stac-higher/stac_higher_scanner

USER scanner
WORKDIR /home/scanner
ENTRYPOINT ["python", "-m", "stac_higher_scanner.scan"]
```

- [ ] **Step 9: Build wiring**

(a) In `services/process-runtime/docker-bake.hcl`, append:
```hcl

// The image scanner (C-2, container-images spec §6.1): Syft + Grype with the
// vulnerability DB baked in. Deliberately NOT in the default group -- it is
// not a process runtime and CI builds it in containers.yml's matrix. Build it
// locally with:
//   docker buildx bake -f services/process-runtime/docker-bake.hcl image-scanner
target "image-scanner" {
  context    = "services/image-scanner"
  dockerfile = "Dockerfile"
  tags       = ["stac-higher-image-scanner:local"]
}
```

(b) In `.github/workflows/containers.yml`, in the `build` job's `matrix.image` list, after the `titiler` entry (its `platforms: linux/amd64` line), add:
```yaml
          - name: image-scanner
            # The platform image scanner (C-2): Syft + Grype, DB baked at
            # build. Tagged by date too so the baked DB's age is visible.
            dockerfile: services/image-scanner/Dockerfile
            context: services/image-scanner
```
and in the `Image metadata` step's `tags:` block add the line:
```yaml
            type=raw,value={{date 'YYYYMMDD'}},enable=${{ matrix.image.name == 'image-scanner' }}
```

- [ ] **Step 10: Run the tests**

Run: `cd services/pipeline && uv run pytest tests/test_image_scanner.py tests/test_image_scanner_registry.py -q`
Expected: PASS.

- [ ] **Step 11: Gates and commit**

Run `npm run verify` and `cd services/pipeline && uv run pytest -q && uv run ruff check .`. (`ruff check .` covers only `services/pipeline`; also run `uv run ruff check ../image-scanner` from `services/pipeline` and fix what it reports, wrapping long lines without changing content.)
```bash
git add services/image-scanner services/pipeline/pyproject.toml services/process-runtime/docker-bake.hcl .github/workflows/containers.yml services/pipeline/tests/test_image_scanner.py services/pipeline/tests/test_image_scanner_registry.py
git commit -m "feat(image-scanner): Syft + Grype scanner image with a baked DB, policy-ranked top findings, verified registry reads (C-2)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 7: The scan drain

**Files:**
- Create: `services/pipeline/src/pipeline/images/scan_launch.py`
- Create: `services/pipeline/src/pipeline/images/drain.py`
- Create: `services/pipeline/src/pipeline/jobs/image_scans.py`
- Modify: `services/pipeline/src/pipeline/main.py`
- Create: `services/pipeline/tests/test_image_scan_launch.py`
- Create: `services/pipeline/tests/test_image_scan_drain.py`
- Modify: `services/pipeline/tests/test_main_jobs.py`

**Interfaces:**
- Consumes: `ImagesRepo`, `ClaimedScan`, `ImageRow`, `FakeImagesRepo` (Task 4); `resolve_registry_auth` (Task 4); `failure_result`, `scan_result_to_json` (Task 4); `mint_prefix_credentials`, `image_scan_prefix`, `image_scan_log_key`, `store_log`, `Settings.image_scanner_image/process_scanner_network/image_scanner_db_update/grype_db_update_url/image_scan_concurrency` (Task 3); `RunSpec.kind` (Task 1); `evaluate`, `parse_scan_result`, `load_image_policy` (C-1); the scanner's environment and object contract (Task 6).
- Produces (`pipeline.images.scan_launch`): `SCANNER_RUN_KIND = "image_scan"`, `RESULT_MAX_BYTES = 1048576`, `ScanRun(status: ExitStatus, handle_id: str, log_ref: str | None)`, `scan_env(settings, policy, *, scan_id, image, kind, registry_auth) -> dict[str, str]`, `sbom_read_prefix(image: ImageRow) -> str`, `build_scan_spec(settings, policy, *, scan_id, image, kind, credentials, registry_auth) -> RunSpec`, `execute_scan(executor, settings, policy, storage_client, *, scan_id, image, kind, registry_auth, sts_client=None) -> ScanRun`, `read_scan_result(storage_client, bucket, key) -> dict | None`.
- Produces (`pipeline.images.drain`): `STALL_GRACE_SECONDS = 600`, `DrainOutcome(scan_id, image_id, scan_status, image_status, error=None)`, `next_image_status(current, *, passed, exception_live) -> str`, `effective_kind(scan: ClaimedScan) -> str`, `check_identity(result, image, *, kind, prefix, policy) -> str | None`, `async drain_one(repo, *, policy, max_running, run_scan, read_result, clock) -> DrainOutcome | None`.
- Produces (`pipeline.jobs.image_scans`): `JOB_NAME = "pipeline.image_scan_drain"`, `CRON = "* * * * *"`, `register(queue, settings)`.

- [ ] **Step 1: Write the failing launch tests**

Create `services/pipeline/tests/test_image_scan_launch.py`:
```python
"""One scan as a platform run (container-images spec §6.2): the scanner gets
exactly the process posture -- its own container, the policy's limits, the
scanner network, a credential for its own scan prefix, and nothing of the
platform's."""

from __future__ import annotations

import json

import pytest

from pipeline.config import Settings
from pipeline.images.policy import load_image_policy
from pipeline.images.repo import ImageRow
from pipeline.images.scan_launch import (
    RESULT_MAX_BYTES,
    SCANNER_RUN_KIND,
    build_scan_spec,
    execute_scan,
    read_scan_result,
    sbom_read_prefix,
)
from pipeline.images.scan_result import ScanResultError
from pipeline.process.credentials import RunCredentials
from pipeline.process.executor import ExitStatus, RegistryAuth
from pipeline.process.memory_executor import MemoryExecutor

IMG = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f"
SCAN = "0d9e8f7a-6b5c-4d3e-9f2a-1b0c9d8e7f6a"
OLD = "11111111-2222-4333-8444-555555555555"
DIGEST = "sha256:" + "a" * 64
POLICY = load_image_policy()

PENDING = ImageRow(id=IMG, reference="docker.io/library/python", tag_at_add="3.12-slim",
                   status="scanning")
APPROVED = ImageRow(
    id=IMG,
    reference="docker.io/library/python",
    tag_at_add="3.12-slim",
    status="approved",
    digest=DIGEST,
    platform_digest=DIGEST,
    platform={"os": "linux", "architecture": "amd64"},
    size_bytes=10,
    config={"user": "", "entrypoint": None, "cmd": None},
    sbom_ref=f"scans/{IMG}/{OLD}/sbom.syft.json",
)
CREDS = RunCredentials("AK", "SK", "TOK", "stac-higher", f"scans/{IMG}/{SCAN}/", None, "r")
SETTINGS = Settings.from_env(
    {"PROCESS_SCANNER_NETWORK": "stac-higher_scanner-egress", "GRYPE_DB_UPDATE_URL": "https://m/x"}
)


def test_an_admission_scan_spec_is_the_process_posture_and_nothing_more():
    spec = build_scan_spec(
        SETTINGS,
        POLICY,
        scan_id=SCAN,
        image=PENDING,
        kind="admission",
        credentials=CREDS,
        registry_auth=RegistryAuth("robot", "pat", "docker.io"),
    )
    assert spec.kind == SCANNER_RUN_KIND == "image_scan"
    assert (spec.run_id, spec.process_id) == (SCAN, IMG)
    assert spec.image == "stac-higher-image-scanner:local"
    assert spec.network == "stac-higher_scanner-egress"
    assert (spec.memory_mb, spec.timeout_seconds) == (4096, 900)
    assert spec.user_image is False and spec.registry_auth is None
    env = spec.env
    assert env["STAC_HIGHER_SCAN_KIND"] == "admission"
    assert env["STAC_HIGHER_IMAGE_REF"] == "docker.io/library/python"
    assert env["STAC_HIGHER_IMAGE_TAG"] == "3.12-slim"
    assert env["STAC_HIGHER_PLATFORM"] == "linux/amd64"
    assert env["STAC_HIGHER_MAX_IMAGE_BYTES"] == str(4096 * 1024 * 1024)
    assert env["STAC_HIGHER_ALLOWED_REGISTRIES"].split(",")[0] == "docker.io"
    assert env["STAC_HIGHER_DB_UPDATE"] == "1"
    assert env["GRYPE_DB_UPDATE_URL"] == "https://m/x"
    assert (env["REGISTRY_USERNAME"], env["REGISTRY_PASSWORD"]) == ("robot", "pat")
    assert env["STAC_HIGHER_OUTPUT_PREFIX"] == f"scans/{IMG}/{SCAN}/"
    for forbidden in (
        "DATABASE_URL",
        "CREDENTIALS_MASTER_KEY",
        "STAGING_S3_ACCESS_KEY_ID",
        "STAGING_S3_SECRET_ACCESS_KEY",
        "STAC_HIGHER_SBOM_KEY",
    ):
        assert forbidden not in env


def test_a_rescan_spec_carries_the_stored_sbom_and_identity_and_no_registry_secret():
    spec = build_scan_spec(
        SETTINGS,
        POLICY,
        scan_id=SCAN,
        image=APPROVED,
        kind="rescan",
        credentials=CREDS,
        registry_auth=RegistryAuth("robot", "pat", "docker.io"),
    )
    assert spec.env["STAC_HIGHER_SBOM_KEY"] == APPROVED.sbom_ref
    identity = json.loads(spec.env["STAC_HIGHER_IMAGE_IDENTITY"])
    assert identity["digest"] == DIGEST and identity["size_bytes"] == 10
    assert "REGISTRY_PASSWORD" not in spec.env


def test_the_sbom_read_prefix_must_be_the_images_own():
    assert sbom_read_prefix(APPROVED) == f"scans/{IMG}/{OLD}/"
    from dataclasses import replace

    with pytest.raises(ValueError, match="outside"):
        sbom_read_prefix(replace(APPROVED, sbom_ref="assets/other/sbom.syft.json"))


class FakeSts:
    def __init__(self):
        self.kwargs = None

    def assume_role(self, **kwargs):
        self.kwargs = kwargs
        return {"Credentials": {"AccessKeyId": "A", "SecretAccessKey": "S", "SessionToken": "T"}}


class FakeStore:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.written: list[str] = []

    def put_object(self, **kwargs):
        self.written.append(kwargs["Key"])
        self.objects[kwargs["Key"]] = kwargs["Body"]

    def head_object(self, **kwargs):
        if kwargs["Key"] not in self.objects:
            raise KeyError(kwargs["Key"])
        return {"ETag": '"e"', "ContentLength": len(self.objects[kwargs["Key"]])}

    def get_object(self, **kwargs):
        import io

        return {"Body": io.BytesIO(self.objects[kwargs["Key"]])}


def test_execute_scan_mints_for_the_scan_prefix_stores_the_log_and_always_reaps():
    sts = FakeSts()
    store = FakeStore()
    executor = MemoryExecutor(results=[ExitStatus(0)], log_output=b"scanned")
    run = execute_scan(
        executor, SETTINGS, POLICY, store,
        scan_id=SCAN, image=APPROVED, kind="rescan", registry_auth=None, sts_client=sts,
    )
    assert run.status.ok and run.handle_id == f"mem-{SCAN}"
    assert run.log_ref == f"scans/{IMG}/{SCAN}/log"
    policy = json.loads(sts.kwargs["Policy"])
    assert policy["Statement"][0]["Resource"] == [f"arn:aws:s3:::stac-higher/scans/{IMG}/{SCAN}/*"]
    assert policy["Statement"][2]["Resource"] == [f"arn:aws:s3:::stac-higher/scans/{IMG}/{OLD}/*"]
    assert sts.kwargs["RoleSessionName"] == f"stac-scan-{SCAN}"
    assert executor.reaped == [f"mem-{SCAN}"]


def test_read_scan_result_missing_oversized_and_present():
    key = f"scans/{IMG}/{SCAN}/result.json"
    assert read_scan_result(FakeStore(), "b", key) is None
    with pytest.raises(ScanResultError, match="cap"):
        read_scan_result(FakeStore({key: b"x" * (RESULT_MAX_BYTES + 1)}), "b", key)
    with pytest.raises(ScanResultError, match="not JSON"):
        read_scan_result(FakeStore({key: b"{nope"}), "b", key)
    assert read_scan_result(FakeStore({key: b'{"version": 1}'}), "b", key) == {"version": 1}
```

Run: `cd services/pipeline && uv run pytest tests/test_image_scan_launch.py -q`
Expected: FAIL (`No module named 'pipeline.images.scan_launch'`).

- [ ] **Step 2: The scan launcher**

Create `services/pipeline/src/pipeline/images/scan_launch.py`:
```python
"""One image scan as a platform run (C-2, container-images spec §6.2).

The scanner parses hostile image content, so it gets exactly the process
posture (ADR 0021): its own container through the same Executor a process
uses, the policy's ``scan_limits``, the scanner network, an STS credential
bounded to ``scans/{image_id}/{scan_id}/`` (plus read on the stored SBOM's
prefix for a rescan), and nothing of the platform's -- no DB URL, no master
key, no platform keys. A registry credential reaches only an ADMISSION
scan's environment; a rescan pulls nothing.

This module is synchronous (the Engine API client is): the job runs it in
``asyncio.to_thread`` so a 15-minute scan never blocks the event loop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pipeline.config import Settings
from pipeline.images.policy import ImagePolicy
from pipeline.images.repo import ImageRow
from pipeline.images.scan_result import ScanResultError
from pipeline.process.credentials import RunCredentials, mint_prefix_credentials
from pipeline.process.executor import Executor, ExitStatus, RegistryAuth, RunSpec
from pipeline.process.logs import store_log
from pipeline.storage.keys import SCANS_PREFIX, image_scan_log_key, image_scan_prefix
from pipeline.storage.platform import get_object, head_object

SCANNER_RUN_KIND = "image_scan"
#: result.json is a summary (top <= 25); anything this large is not one.
RESULT_MAX_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ScanRun:
    status: ExitStatus
    handle_id: str
    #: None when the log could not be stored (the verdict never depends on it).
    log_ref: str | None


def _identity(image: ImageRow) -> dict[str, Any]:
    return {
        "digest": image.digest,
        "platform_digest": image.platform_digest,
        "platform": image.platform,
        "size_bytes": image.size_bytes,
        "config": image.config,
    }


def scan_env(
    settings: Settings,
    policy: ImagePolicy,
    *,
    scan_id: str,
    image: ImageRow,
    kind: str,
    registry_auth: RegistryAuth | None,
) -> dict[str, str]:
    """The scanner's job (spec §6.2 plus Decision 19's four variables)."""
    env = {
        "STAC_HIGHER_SCAN_ID": scan_id,
        "STAC_HIGHER_SCAN_KIND": kind,
        "STAC_HIGHER_IMAGE_REF": image.reference,
        "STAC_HIGHER_IMAGE_TAG": image.tag_at_add,
        "STAC_HIGHER_PLATFORM": policy.platform,
        "STAC_HIGHER_MAX_IMAGE_BYTES": str(policy.max_image_bytes),
        "STAC_HIGHER_ALLOWED_REGISTRIES": ",".join(policy.allowed_registries),
        "STAC_HIGHER_DB_UPDATE": "1" if settings.image_scanner_db_update else "0",
    }
    if settings.grype_db_update_url:
        env["GRYPE_DB_UPDATE_URL"] = settings.grype_db_update_url
    if kind == "rescan":
        env["STAC_HIGHER_SBOM_KEY"] = image.sbom_ref or ""
        env["STAC_HIGHER_IMAGE_IDENTITY"] = json.dumps(_identity(image))
    elif registry_auth is not None:
        env["REGISTRY_USERNAME"] = registry_auth.username
        env["REGISTRY_PASSWORD"] = registry_auth.password
    return env


def sbom_read_prefix(image: ImageRow) -> str:
    """The stored SBOM's scan prefix, which a rescan may read. It must sit
    under this image's own ``scans/{image_id}/`` or nothing is granted."""
    ref = image.sbom_ref or ""
    prefix = ref.rsplit("/", 1)[0] + "/"
    if not prefix.startswith(f"{SCANS_PREFIX}/{image.id}/") or ".." in ref:
        raise ValueError(f"the stored SBOM {ref!r} is outside the image's scan prefix")
    return prefix


def build_scan_spec(
    settings: Settings,
    policy: ImagePolicy,
    *,
    scan_id: str,
    image: ImageRow,
    kind: str,
    credentials: RunCredentials,
    registry_auth: RegistryAuth | None,
) -> RunSpec:
    env = scan_env(
        settings, policy, scan_id=scan_id, image=image, kind=kind, registry_auth=registry_auth
    )
    env.update(credentials.as_env())  # platform-controlled, applied last
    return RunSpec(
        run_id=scan_id,
        process_id=image.id,
        image=settings.image_scanner_image,
        env=env,
        memory_mb=policy.scan_memory_mb,
        timeout_seconds=policy.scan_timeout_seconds,
        network=settings.process_scanner_network,
        kind=SCANNER_RUN_KIND,
    )


def execute_scan(
    executor: Executor,
    settings: Settings,
    policy: ImagePolicy,
    storage_client,
    *,
    scan_id: str,
    image: ImageRow,
    kind: str,
    registry_auth: RegistryAuth | None,
    sts_client=None,
) -> ScanRun:
    """Mint, launch, wait (the policy timeout), capture the log, ALWAYS reap."""
    read_prefixes = (sbom_read_prefix(image),) if kind == "rescan" else ()
    credentials = mint_prefix_credentials(
        settings,
        session_name=f"stac-scan-{scan_id}",
        prefix=image_scan_prefix(image.id, scan_id),
        timeout_seconds=policy.scan_timeout_seconds,
        sts_client=sts_client,
        read_prefixes=read_prefixes,
    )
    spec = build_scan_spec(
        settings,
        policy,
        scan_id=scan_id,
        image=image,
        kind=kind,
        credentials=credentials,
        registry_auth=registry_auth,
    )
    handle = executor.launch(spec)
    try:
        status = executor.wait(handle, policy.scan_timeout_seconds)
        payload = executor.logs(handle, settings.process_log_max_bytes)
    finally:
        executor.reap(handle)
    log_ref = store_log(
        storage_client,
        settings.staging_bucket,
        image_scan_log_key(image.id, scan_id),
        payload,
        settings.process_log_max_bytes,
        context={"scan_id": scan_id, "image_id": image.id},
    )
    return ScanRun(status=status, handle_id=handle.id, log_ref=log_ref)


def read_scan_result(storage_client, bucket: str, key: str) -> dict[str, Any] | None:
    """The scanner's result.json, or None when it wrote none. UNTRUSTED: the
    caller parses it with ``parse_scan_result``."""
    try:
        _etag, size = head_object(storage_client, bucket, key)
    except Exception:  # absent (or unreadable): the scanner left no result
        return None
    if size > RESULT_MAX_BYTES:
        raise ScanResultError(f"result.json is {size} bytes; the cap is {RESULT_MAX_BYTES}")
    body = get_object(storage_client, bucket, key)
    try:
        doc = json.loads(body)
    except ValueError as err:
        raise ScanResultError("result.json is not JSON") from err
    return doc if isinstance(doc, dict) else {"version": None}
```

Run: `cd services/pipeline && uv run pytest tests/test_image_scan_launch.py -q`
Expected: PASS.

- [ ] **Step 3: Write the failing drain tests**

Create `services/pipeline/tests/test_image_scan_drain.py`:
```python
"""The scan drain (container-images spec §8.1, §4.3, §9.1 dedup).

The scanner is untrusted: its result is believed only when it parses, names
this scan's image and prefix, and matches what the drain asked for. Every
other outcome is a failed scan with an identity-bearing result, and the image
leaves `scanning`."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import replace
from pathlib import Path

import pytest

from _images_fake import FakeImagesRepo
from pipeline.images.drain import drain_one, effective_kind, next_image_status
from pipeline.images.policy import load_image_policy
from pipeline.images.repo import ClaimedScan, ImageRow
from pipeline.images.scan_launch import ScanRun
from pipeline.images.scan_result import parse_scan_result
from pipeline.process.executor import ExitStatus

NOW = dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.UTC)
IMG = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f"
OTHER = "99999999-8888-4777-8666-555555555555"
SCAN = "0d9e8f7a-6b5c-4d3e-9f2a-1b0c9d8e7f6a"
OLD = "11111111-2222-4333-8444-555555555555"
DIGEST = "sha256:" + "a" * 64
REF = "docker.io/library/python"
PREFIX = f"scans/{IMG}/{SCAN}/"
POLICY = load_image_policy()
FIXTURE = json.loads(
    (Path(__file__).resolve().parents[3] / "tests/contract-fixtures/image-scan-result.json")
    .read_text()
)["document"]


def result_doc(**overrides) -> dict:
    doc = {
        **FIXTURE,
        "kind": "admission",
        "reference": REF,
        "tag": "3.12-slim",
        "digest": DIGEST,
        "platform_digest": DIGEST,
        "sbom_ref": f"{PREFIX}sbom.syft.json",
        "findings_ref": f"{PREFIX}findings.grype.json",
        "counts": {"critical": 0, "high": 0, "medium": 1, "low": 0, "negligible": 0, "unknown": 0},
        "fixed_counts": dict.fromkeys(
            ("critical", "high", "medium", "low", "negligible", "unknown"), 0
        ),
        "kev": [],
        "max_risk": 0.1,
        "top": [],
    }
    doc.update(overrides)
    return doc


def repo_with(status="pending", scan_kind="admission", **image) -> FakeImagesRepo:
    repo = FakeImagesRepo(clock=NOW)
    repo.add_image(id=IMG, reference=REF, tag_at_add="3.12-slim", status=status, **image)
    repo.add_scan(SCAN, IMG, kind=scan_kind)
    return repo


class Scanner:
    """A scripted scanner run plus the result.json it left behind."""

    def __init__(
        self, doc=None, *, status: ExitStatus | None = None, error: Exception | None = None
    ):
        self.doc = doc
        self.status = status or ExitStatus(0)
        self.error = error
        self.kinds: list[str] = []
        self.read_keys: list[str] = []

    async def run_scan(self, scan, kind):
        self.kinds.append(kind)
        if self.error is not None:
            raise self.error
        return ScanRun(status=self.status, handle_id="c1", log_ref=f"{PREFIX}log")

    async def read_result(self, key):
        self.read_keys.append(key)
        return self.doc


async def drain(repo, scanner):
    return await drain_one(
        repo,
        policy=POLICY,
        max_running=1,
        run_scan=scanner.run_scan,
        read_result=scanner.read_result,
        clock=lambda: NOW,
    )


@pytest.mark.parametrize(
    ("current", "passed", "exception_live", "expected"),
    [
        ("scanning", True, False, "approved"),
        ("scanning", False, False, "rejected"),
        ("scan_failed", True, False, "approved"),
        ("rejected", True, False, "approved"),
        ("rejected", False, False, "rejected"),
        ("approved", False, False, "flagged"),
        ("approved", False, True, "approved"),
        ("flagged", True, False, "approved"),
        ("flagged", False, False, "flagged"),
        ("revoked", True, False, "revoked"),
    ],
)
def test_next_image_status(current, passed, exception_live, expected):
    assert next_image_status(current, passed=passed, exception_live=exception_live) == expected


@pytest.mark.asyncio
async def test_nothing_pending_launches_nothing():
    repo = FakeImagesRepo(clock=NOW)
    scanner = Scanner()
    assert await drain(repo, scanner) is None
    assert scanner.kinds == []


@pytest.mark.asyncio
async def test_a_passing_admission_approves_and_fills_the_row():
    repo = repo_with()
    outcome = await drain(repo, Scanner(result_doc()))
    assert (outcome.scan_status, outcome.image_status) == ("done", "approved")
    image = repo.images[IMG]
    assert (image.status, image.digest, image.size_bytes) == ("approved", DIGEST, 812345678)
    assert image.sbom_ref == f"{PREFIX}sbom.syft.json"
    assert image.last_scanned_at == NOW
    assert repo.last_scan_ids[IMG] == SCAN
    scan = repo.scans[SCAN]
    assert scan["status"] == "done" and scan["executor_handle"] == "c1"
    assert scan["log_ref"] == f"{PREFIX}log"
    assert scan["findings_ref"] == f"{PREFIX}findings.grype.json"
    assert scan["result"]["verdict"]["pass"] is True and scan["result"]["diff"] is None
    assert parse_scan_result(scan["result"]).digest == DIGEST


@pytest.mark.asyncio
async def test_a_failing_admission_is_rejected_with_reasons():
    repo = repo_with()
    outcome = await drain(repo, Scanner(result_doc(kev=["CVE-2026-0001"])))
    assert outcome.image_status == "rejected"
    assert repo.verdicts[IMG]["reasons"] == ["kev:CVE-2026-0001"]


@pytest.mark.asyncio
async def test_no_result_json_still_fails_the_scan_with_a_readable_result():
    """Review Focus 3: a crash, an OOM kill or a timeout leaves nothing behind."""
    repo = repo_with()
    outcome = await drain(repo, Scanner(None, status=ExitStatus(137, timed_out=True)))
    assert outcome.scan_status == "failed"
    scan = repo.scans[SCAN]
    parsed = parse_scan_result(scan["result"])
    assert "timed out after 900 s" in parsed.error
    assert (parsed.reference, parsed.tag, parsed.kind) == (REF, "3.12-slim", "admission")
    assert scan["log_ref"] == f"{PREFIX}log"
    assert repo.images[IMG].status == "scan_failed"


@pytest.mark.asyncio
async def test_an_error_result_fails_the_scan_with_the_scanners_message():
    repo = repo_with()
    doc = {"version": 1, "kind": "admission", "reference": REF, "tag": "3.12-slim",
           "error": "image_too_large: 9 bytes of layers exceed the policy's 8"}
    await drain(repo, Scanner(doc, status=ExitStatus(1)))
    assert parse_scan_result(repo.scans[SCAN]["result"]).error.startswith("image_too_large")
    assert repo.images[IMG].status == "scan_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lie",
    [
        {"reference": "docker.io/library/alpine"},
        {"tag": "latest"},
        {"sbom_ref": f"scans/{IMG}/{OLD}/sbom.syft.json"},
        {"findings_ref": "assets/c/i/findings.json"},
        {"kind": "rescan"},
        {"platform": {"os": "linux", "architecture": "arm64"}},
        {"max_risk": float("nan")},
    ],
    ids=["reference", "tag", "sbom", "findings", "kind", "platform", "nan"],
)
async def test_a_scanner_that_lies_is_not_believed(lie):
    """Review Focus 2."""
    repo = repo_with()
    outcome = await drain(repo, Scanner(result_doc(**lie)))
    assert outcome.scan_status == "failed"
    assert repo.images[IMG].status == "scan_failed"
    assert repo.images[IMG].digest is None


@pytest.mark.asyncio
async def test_a_result_with_a_failed_exit_is_not_believed():
    repo = repo_with()
    await drain(repo, Scanner(result_doc(), status=ExitStatus(1)))
    assert repo.scans[SCAN]["status"] == "failed"


@pytest.mark.asyncio
async def test_a_scan_that_cannot_start_fails_without_a_result_read():
    repo = repo_with()
    scanner = Scanner(error=RuntimeError("docker unreachable"))
    outcome = await drain(repo, scanner)
    assert outcome.scan_status == "failed"
    assert "could not start" in outcome.error and scanner.read_keys == []


@pytest.mark.asyncio
async def test_a_revoked_image_is_never_scanned():
    repo = repo_with(status="revoked")
    scanner = Scanner(result_doc())
    await drain(repo, scanner)
    assert scanner.kinds == []
    assert repo.scans[SCAN]["status"] == "failed"
    assert repo.images[IMG].status == "revoked"


@pytest.mark.asyncio
async def test_a_second_add_of_the_same_digest_folds_into_the_existing_row():
    """Spec §9.1: dedup by digest happens in the drain."""
    repo = repo_with()
    repo.add_image(
        id=OTHER,
        reference=REF,
        tag_at_add="3.12",
        status="approved",
        digest=DIGEST,
        sbom_ref=f"scans/{OTHER}/{OLD}/sbom.syft.json",
    )
    outcome = await drain(repo, Scanner(result_doc()))
    assert outcome.image_id == OTHER and outcome.image_status == "approved"
    assert IMG in repo.deleted_images and IMG not in repo.images
    assert repo.scans[SCAN]["image_id"] == OTHER
    # The existing row keeps its own SBOM (Decision 8).
    assert repo.images[OTHER].sbom_ref == f"scans/{OTHER}/{OLD}/sbom.syft.json"
    assert repo.last_scan_ids[OTHER] == SCAN


@pytest.mark.asyncio
async def test_a_failing_rescan_flags_an_approved_image():
    repo = repo_with(
        status="approved",
        scan_kind="rescan",
        digest=DIGEST,
        sbom_ref=f"scans/{IMG}/{OLD}/sbom.syft.json",
    )
    doc = result_doc(
        kind="rescan", sbom_ref=f"scans/{IMG}/{OLD}/sbom.syft.json", kev=["CVE-2026-0001"]
    )
    scanner = Scanner(doc)
    outcome = await drain(repo, scanner)
    assert scanner.kinds == ["rescan"]
    assert outcome.image_status == "flagged"
    # A rescan never rewrites the admission fields.
    assert repo.images[IMG].sbom_ref == f"scans/{IMG}/{OLD}/sbom.syft.json"


@pytest.mark.asyncio
async def test_a_live_exception_keeps_a_failing_rescan_approved():
    repo = repo_with(
        status="approved",
        scan_kind="rescan",
        digest=DIGEST,
        sbom_ref=f"scans/{IMG}/{OLD}/sbom.syft.json",
        exception_expires_at=NOW + dt.timedelta(days=5),
    )
    doc = result_doc(
        kind="rescan", sbom_ref=f"scans/{IMG}/{OLD}/sbom.syft.json", kev=["CVE-2026-0001"]
    )
    assert (await drain(repo, Scanner(doc))).image_status == "approved"


@pytest.mark.asyncio
async def test_a_rescan_of_an_image_without_an_sbom_runs_as_an_admission():
    repo = repo_with(status="scan_failed", scan_kind="rescan")
    scanner = Scanner(result_doc())
    outcome = await drain(repo, scanner)
    assert scanner.kinds == ["admission"]
    assert outcome.image_status == "approved"


def test_effective_kind_needs_a_stored_sbom_and_a_digest():
    base = ImageRow(id=IMG, reference=REF, tag_at_add="3.12-slim", status="approved")
    assert effective_kind(ClaimedScan("s", "rescan", "u", base)) == "admission"
    full = replace(base, digest=DIGEST, sbom_ref=f"scans/{IMG}/{OLD}/sbom.syft.json")
    assert effective_kind(ClaimedScan("s", "rescan", "u", full)) == "rescan"
    assert effective_kind(ClaimedScan("s", "admission", "u", full)) == "admission"
```

Run: `cd services/pipeline && uv run pytest tests/test_image_scan_drain.py -q`
Expected: FAIL (`No module named 'pipeline.images.drain'`).

- [ ] **Step 4: The drain logic**

Create `services/pipeline/src/pipeline/images/drain.py`:
```python
"""The scan drain's logic (C-2, container-images spec §8.1, §4.3, §9.1).

One claimed scan: launch the scanner (through an injected ``run_scan``), read
``result.json`` back (``read_result``), believe it only if it parses AND
names this scan's image, tag, kind, prefix and platform, evaluate it against
the policy, and transition the image. Everything else is a FAILED scan whose
result still carries the image's identity (the C-1 failure shape), and an
image in ``pending``/``scanning`` goes ``scan_failed``.

Transitions (spec §4.3, :func:`next_image_status`): a passing scan approves
(a passing scan auto-approves, spec decision 3, a ``rejected`` or
``scan_failed`` image included); a failing admission rejects; a failing
rescan flags an ``approved`` image unless a live exception covers it;
``revoked`` is terminal. The rescan TICK, the diff, drift, alerts and
exception expiry are C-4's; the drain records ``diff: null``.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pipeline.images.policy import ImagePolicy, evaluate
from pipeline.images.repo import ClaimedScan, ImageRow, ImagesRepo
from pipeline.images.scan_launch import ScanRun
from pipeline.images.scan_result import (
    ScanResult,
    ScanResultError,
    failure_result,
    parse_scan_result,
    scan_result_to_json,
)
from pipeline.storage.keys import image_scan_prefix

logger = logging.getLogger(__name__)

#: A scan still `running` this long past the policy timeout is presumed lost.
STALL_GRACE_SECONDS = 600

RunScan = Callable[[ClaimedScan, str], Awaitable[ScanRun]]
ReadResult = Callable[[str], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True)
class DrainOutcome:
    scan_id: str
    #: The authoritative image id (differs from the claimed one after a dedup).
    image_id: str
    scan_status: str
    image_status: str | None
    error: str | None = None


def next_image_status(current: str, *, passed: bool, exception_live: bool) -> str:
    if current == "revoked":
        return "revoked"
    if passed or exception_live:
        return "approved"
    if current in ("approved", "flagged"):
        return "flagged"
    return "rejected"


def exception_is_live(image: ImageRow, now: dt.datetime) -> bool:
    return image.exception_expires_at is not None and image.exception_expires_at > now


def effective_kind(scan: ClaimedScan) -> str:
    """A rescan needs a stored SBOM and digest; without them it runs as an
    admission (Decision 7), e.g. a re-request after ``scan_failed``."""
    image = scan.image
    if scan.kind == "rescan" and image.sbom_ref and image.digest:
        return "rescan"
    return "admission"


def check_identity(
    result: ScanResult, image: ImageRow, *, kind: str, prefix: str, policy: ImagePolicy
) -> str | None:
    """Why the result must not be believed, or None."""
    if result.kind != kind:
        return f"the scanner ran a {result.kind} scan; the drain asked for {kind}"
    if result.reference != image.reference:
        return f"the scanner reported {result.reference}; the row is {image.reference}"
    if result.tag != image.tag_at_add:
        return f"the scanner reported tag {result.tag!r}; the row has {image.tag_at_add!r}"
    if result.findings_ref != f"{prefix}findings.grype.json":
        return "findings_ref is outside this scan's prefix"
    if kind == "admission":
        if result.sbom_ref != f"{prefix}sbom.syft.json":
            return "sbom_ref is outside this scan's prefix"
        os_, arch = policy.platform.split("/")[:2]
        if result.platform != {"os": os_, "architecture": arch}:
            return f"the scanner scanned {result.platform}; the policy requires {policy.platform}"
        return None
    if result.sbom_ref != image.sbom_ref:
        return "the rescan read an SBOM other than the stored one"
    if result.digest != image.digest:
        return "the rescan reported a digest other than the row's"
    return None


async def _fail(
    repo: ImagesRepo,
    scan: ClaimedScan,
    kind: str,
    error: str,
    run: ScanRun | None,
) -> DrainOutcome:
    image = scan.image
    await repo.mark_image_scan_failed(image.id)
    await repo.finish_scan(
        scan.id,
        status="failed",
        result=failure_result(kind, image.reference, image.tag_at_add, error),
        findings_ref=None,
        log_ref=run.log_ref if run else None,
        executor_handle=run.handle_id if run else None,
    )
    logger.warning(
        "image scan failed",
        extra={"scan_id": scan.id, "image_id": image.id, "kind": kind, "error": error[:500]},
    )
    return DrainOutcome(scan.id, image.id, "failed", None, error)


async def drain_one(
    repo: ImagesRepo,
    *,
    policy: ImagePolicy,
    max_running: int,
    run_scan: RunScan,
    read_result: ReadResult,
    clock: Callable[[], dt.datetime],
) -> DrainOutcome | None:
    """Claim and process at most one scan. None when nothing was claimable."""
    scan = await repo.claim_pending_scan(max_running=max_running)
    if scan is None:
        return None
    image = scan.image
    kind = effective_kind(scan)
    if image.status == "revoked":
        return await _fail(repo, scan, kind, "the image is revoked; nothing is scanned", None)

    try:
        run = await run_scan(scan, kind)
    except Exception as err:  # never started: fail the request, nothing retries silently
        return await _fail(
            repo, scan, kind, f"the scan could not start: {type(err).__name__}: {err}", None
        )

    prefix = image_scan_prefix(image.id, scan.id)
    try:
        doc = await read_result(f"{prefix}result.json")
    except ScanResultError as err:
        return await _fail(repo, scan, kind, f"scanner result rejected: {err}", run)
    if doc is None:
        if run.status.timed_out:
            reason = f"the scan timed out after {policy.scan_timeout_seconds} s"
        else:
            reason = f"the scanner exited {run.status.exit_code} without writing a result"
        if run.status.error:
            reason = f"{reason} ({run.status.error})"
        return await _fail(repo, scan, kind, reason, run)
    try:
        result = parse_scan_result(doc)
    except ScanResultError as err:
        return await _fail(repo, scan, kind, f"scanner result rejected: {err}", run)
    if result.error is not None:
        return await _fail(repo, scan, kind, result.error, run)
    if not run.status.ok:
        return await _fail(
            repo,
            scan,
            kind,
            f"the scanner exited {run.status.exit_code} after writing a result",
            run,
        )
    mismatch = check_identity(result, image, kind=kind, prefix=prefix, policy=policy)
    if mismatch is not None:
        return await _fail(repo, scan, kind, f"scanner result rejected: {mismatch}", run)

    now = clock()
    verdict = evaluate(result, policy, now=now)
    verdict_json = verdict.as_json()
    image_id = image.id
    if kind == "admission":
        assert result.digest is not None
        existing = await repo.find_image_by_digest(
            result.reference, result.digest, exclude_id=image.id
        )
        if existing is not None:
            status = next_image_status(
                existing.status,
                passed=verdict.passed,
                exception_live=exception_is_live(existing, now),
            )
            await repo.merge_admission(
                provisional_id=image.id,
                existing_id=existing.id,
                scan_id=scan.id,
                verdict=verdict_json,
                status=status,
                at=now,
            )
            image_id = existing.id
        else:
            status = next_image_status(
                image.status,
                passed=verdict.passed,
                exception_live=exception_is_live(image, now),
            )
            await repo.record_admission(
                image.id,
                scan_id=scan.id,
                result=result,
                verdict=verdict_json,
                status=status,
                at=now,
            )
    else:
        status = next_image_status(
            image.status, passed=verdict.passed, exception_live=exception_is_live(image, now)
        )
        await repo.record_rescan(
            image.id, scan_id=scan.id, verdict=verdict_json, status=status, at=now
        )
    await repo.finish_scan(
        scan.id,
        status="done",
        result={**scan_result_to_json(result), "verdict": verdict_json, "diff": None},
        findings_ref=result.findings_ref,
        log_ref=run.log_ref,
        executor_handle=run.handle_id,
    )
    return DrainOutcome(scan.id, image_id, "done", status)
```

- [ ] **Step 5: The job and its registration**

Create `services/pipeline/src/pipeline/jobs/image_scans.py`:
```python
"""Image scan drain (C-2, container-images spec §8.1, ADR 0021, ADR 0004).

Every minute: fail scans stalled past the policy timeout, then claim ONE
pending ``image_scans`` row (a deployment-wide cap of
``IMAGE_SCAN_CONCURRENCY`` running scans, default 1) and run it through
:func:`pipeline.images.drain.drain_one`. The scanner runs in
``asyncio.to_thread``: it blocks a worker slot for up to the policy timeout
until K-4 (ISSUES I-124), never the event loop.

A missing or invalid image policy skips the tick (fail closed, spec §7.2):
pending scans stay pending and nothing is scanned against a policy nobody
can read.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

from pipeline.config import Settings
from pipeline.images.drain import STALL_GRACE_SECONDS, drain_one
from pipeline.images.policy import ImagePolicyError, load_image_policy
from pipeline.images.registry_auth import resolve_registry_auth
from pipeline.images.repo import ClaimedScan, PgImagesRepo
from pipeline.images.scan_launch import ScanRun, execute_scan, read_scan_result
from pipeline.jobs._common import load_key_or_skip
from pipeline.process.docker_executor import DockerExecutor
from pipeline.queue.interface import QueueBackend
from pipeline.storage.platform import build_platform_client

logger = logging.getLogger(__name__)

JOB_NAME = "pipeline.image_scan_drain"
CRON = "* * * * *"


def register(queue: QueueBackend, settings: Settings) -> None:
    async def image_scan_drain(timestamp: int) -> None:  # pragma: no cover - needs a DB
        try:
            policy = load_image_policy()
        except ImagePolicyError as err:
            logger.error(
                "image scan drain skipped: the image policy is unavailable",
                extra={"job": JOB_NAME, "error": str(err)},
            )
            return
        repo = PgImagesRepo(settings.database_url)
        started_before = dt.datetime.now(dt.UTC) - dt.timedelta(
            seconds=policy.scan_timeout_seconds + STALL_GRACE_SECONDS
        )
        stalled = await repo.fail_stalled_scans(started_before=started_before)
        if stalled:
            logger.warning("stalled image scans failed", extra={"count": stalled})
        executor = DockerExecutor(docker_host=settings.docker_host)
        storage_client = build_platform_client(settings)

        async def run_scan(scan: ClaimedScan, kind: str) -> ScanRun:
            auth = None
            if kind == "admission":
                # The key is needed only for a private image's credential; a
                # deployment without one still scans public images.
                key = (
                    load_key_or_skip(settings, JOB_NAME)
                    if scan.image.registry_connection_id
                    else None
                )
                auth = await resolve_registry_auth(
                    scan.image, repo=repo, settings=settings, master_key=key
                )
            return await asyncio.to_thread(
                execute_scan,
                executor,
                settings,
                policy,
                storage_client,
                scan_id=scan.id,
                image=scan.image,
                kind=kind,
                registry_auth=auth,
            )

        async def read_result(key: str):
            return await asyncio.to_thread(
                read_scan_result, storage_client, settings.staging_bucket, key
            )

        outcome = await drain_one(
            repo,
            policy=policy,
            max_running=settings.image_scan_concurrency,
            run_scan=run_scan,
            read_result=read_result,
            clock=lambda: dt.datetime.now(dt.UTC),
        )
        if outcome is not None:
            logger.info(
                "image scan finished",
                extra={
                    "scan_id": outcome.scan_id,
                    "image_id": outcome.image_id,
                    "scan_status": outcome.scan_status,
                    "image_status": outcome.image_status,
                    "scheduled_timestamp": timestamp,
                },
            )

    queue.register_periodic(image_scan_drain, name=JOB_NAME, cron=CRON)
```

In `services/pipeline/src/pipeline/main.py`:
(a) add `image_scans,` to the `from pipeline.jobs import (...)` list (alphabetical: after `history,`);
(b) after `process.register(queue, settings)` and its comment add:
```python
    # C-2 (ADR 0021): drain image_scans -- the scanner as a platform run, one
    # at a time deployment-wide, the verdict evaluated here (spec §8.1).
    image_scans.register(queue, settings)
```

In `services/pipeline/tests/test_main_jobs.py`:
(a) add `from pipeline.jobs.image_scans import JOB_NAME as IMAGE_SCAN_JOB`;
(b) add `assert IMAGE_SCAN_JOB in registered` after the `assert PGSTAC_DRAIN_JOB in registered` line.

- [ ] **Step 6: Run the tests**

Run: `cd services/pipeline && uv run pytest tests/test_image_scan_launch.py tests/test_image_scan_drain.py tests/test_main_jobs.py -q`
Expected: PASS.

- [ ] **Step 7: Gates and commit**

Run `npm run verify` and `cd services/pipeline && uv run pytest -q && uv run ruff check .`.
```bash
git add services/pipeline/src/pipeline/images/scan_launch.py services/pipeline/src/pipeline/images/drain.py services/pipeline/src/pipeline/jobs/image_scans.py services/pipeline/src/pipeline/main.py services/pipeline/tests/test_image_scan_launch.py services/pipeline/tests/test_image_scan_drain.py services/pipeline/tests/test_main_jobs.py
git commit -m "feat(pipeline): image scan drain: scanner as a platform run, untrusted result checked, verdict and transition, dedup by digest (C-2)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: `/health` names the policy; compose gets the scanner network and `IMAGES=1`

**Files:**
- Modify: `services/pipeline/src/pipeline/health.py`
- Modify: `services/pipeline/tests/test_health.py` (append)
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Create: `services/pipeline/tests/test_compose_scanner.py`

**Interfaces:**
- Produces: `GET /health` gains `"image_policy": {"file": str, "ok": bool, "version": int | None, "error": str | None}`; it never changes the 200/503 decision. `create_health_app(queue, heartbeat_state=STATE, *, image_policy_file: Path | None = None)`.

- [ ] **Step 1: Write the failing tests**

Append to `services/pipeline/tests/test_health.py`:
```python
def test_health_names_the_image_policy_file():
    body = make_client(InMemoryQueue(), HeartbeatState()).get("/health").json()
    policy = body["image_policy"]
    assert policy["ok"] is True and policy["version"] == 1 and policy["error"] is None
    assert policy["file"].endswith("default.json")


def test_a_broken_image_policy_is_reported_but_not_a_503(tmp_path):
    from fastapi.testclient import TestClient

    missing = tmp_path / "policy.json"
    app = create_health_app(
        InMemoryQueue(), heartbeat_state=HeartbeatState(), image_policy_file=missing
    )
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    policy = response.json()["image_policy"]
    assert policy == {"file": str(missing), "ok": False, "version": None, "error": policy["error"]}
    assert "policy.json" in policy["error"]
```

Create `services/pipeline/tests/test_compose_scanner.py`:
```python
"""Compose wiring for C-2 (container-images spec §8.5, §11), pinned as text:
a boundary that silently widens is the failure these catch."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
COMPOSE = (REPO / "docker-compose.yml").read_text()


def _service(name: str) -> str:
    match = re.search(rf"^  {name}:\n(.*?)(?=^  [a-z][a-z0-9-]*:\n|^[a-z])", COMPOSE, re.M | re.S)
    assert match, name
    return match.group(1)


def test_the_socket_proxy_allows_images_and_nothing_else_new():
    proxy = _service("docker-socket-proxy")
    assert "- IMAGES=1" in proxy and "IMAGES=0" not in proxy
    for still_off in ("EXEC=0", "VOLUMES=0", "NETWORKS=0", "BUILD=0", "SYSTEM=0"):
        assert still_off in proxy
    assert "scanner-egress" not in proxy


def test_the_scanner_network_is_egress_capable_and_minio_is_on_it():
    networks = COMPOSE.split("\nnetworks:\n", 1)[1]
    block = re.search(r"^  scanner-egress:\n((?:    .*\n|\s*#.*\n)*)", networks, re.M)
    assert block, "scanner-egress network is not declared"
    assert "internal: true" not in block.group(1)
    assert "- scanner-egress" in _service("minio")


def test_the_pipeline_names_the_scanner_network_and_the_hub_credential():
    pipeline = _service("pipeline")
    network = "PROCESS_SCANNER_NETWORK=${PROCESS_SCANNER_NETWORK:-stac-higher_scanner-egress}"
    assert network in pipeline
    assert "IMAGE_SCANNER_IMAGE=${IMAGE_SCANNER_IMAGE:-stac-higher-image-scanner:local}" in pipeline
    assert "REGISTRY_DOCKERHUB_USER=${REGISTRY_DOCKERHUB_USER:-}" in pipeline
    assert "REGISTRY_DOCKERHUB_TOKEN=${REGISTRY_DOCKERHUB_TOKEN:-}" in pipeline
    # The pipeline itself never joins the scanner's network.
    assert "scanner-egress\n" not in pipeline.split("environment:", 1)[0]
```

Run: `cd services/pipeline && uv run pytest tests/test_health.py tests/test_compose_scanner.py -q`
Expected: FAIL (`KeyError: 'image_policy'`, compose assertions).

- [ ] **Step 2: The health key**

In `services/pipeline/src/pipeline/health.py`:

(a) Add imports:
```python
from pathlib import Path
from typing import Any

from pipeline.images.policy import ImagePolicyError, image_policy_path, load_image_policy
```

(b) Add before `create_health_app`:
```python
def image_policy_status(path: Path | None = None) -> dict[str, Any]:
    """C-2 (spec §7.2): the policy fails closed, so an operator must be able to
    see WHY no image is scanned or launched. Informational: a broken policy
    does not make the pipeline unhealthy (inline processes are unaffected)."""
    where = path or image_policy_path()
    try:
        policy = load_image_policy(where)
    except ImagePolicyError as err:
        return {"file": str(where), "ok": False, "version": None, "error": str(err)}
    return {"file": str(where), "ok": True, "version": policy.version, "error": None}
```

(c) Change the signature to
`def create_health_app(queue: QueueBackend, heartbeat_state: HeartbeatState = STATE, *, image_policy_file: Path | None = None) -> FastAPI:`
(wrapped to the line limit) and add to the JSON `content` after `"db_pool": pool_stats(),`:
```python
                # C-2: the image policy's file and whether it parses.
                "image_policy": image_policy_status(image_policy_file),
```

- [ ] **Step 3: Compose**

In `docker-compose.yml`:

(a) In the `docker-socket-proxy` comment block, replace the sentence `container create/start/wait/logs/remove are permitted, everything else` with `container create/start/wait/logs/remove and (C-2) image inspect/pull are permitted, everything else`, and append to the RESIDUAL RISK paragraph:
```yaml
  # C-2 (ADR 0021, spec §8.5): IMAGES=1 lets the pipeline pull (and delete)
  # images on the daemon. The executor pulls only digests that exist as an
  # approved/flagged container_images row, and it is still the proxy's only
  # client. NETWORKS, VOLUMES and EXEC stay off.
```

(b) In its `environment`, replace `      - IMAGES=0` with `      - IMAGES=1`.

(c) In the `minio` service's `networks:` list, add `      - scanner-egress` after `      - process-runs`, with the comment line above it `      # C-2: the scanner uploads its SBOMs, findings and result here.`

(d) In the `pipeline` service's `environment`, after the `PROCESS_IMAGE_POLICY_FILE=...` line add:
```yaml
      # C-2 (container-images spec §6, §11): the scanner image, the network it
      # runs on (egress-capable: it pulls from registries; MinIO is attached),
      # whether it refreshes the Grype DB at scan start (false for air-gap,
      # with GRYPE_DB_UPDATE_URL pointing at a mirror when it is true), and
      # how many scans run at once (each holds a worker slot, I-124).
      - IMAGE_SCANNER_IMAGE=${IMAGE_SCANNER_IMAGE:-stac-higher-image-scanner:local}
      - PROCESS_SCANNER_NETWORK=${PROCESS_SCANNER_NETWORK:-stac-higher_scanner-egress}
      - IMAGE_SCANNER_DB_UPDATE=${IMAGE_SCANNER_DB_UPDATE:-true}
      - GRYPE_DB_UPDATE_URL=${GRYPE_DB_UPDATE_URL:-}
      - IMAGE_SCAN_CONCURRENCY=${IMAGE_SCAN_CONCURRENCY:-1}
      # Optional deployment Docker Hub credential for docker.io images that
      # carry no group credential (spec §5). Unset = anonymous, which shares
      # Docker's 100-pulls-per-6-hours-per-IP budget (ISSUES I-125).
      - REGISTRY_DOCKERHUB_USER=${REGISTRY_DOCKERHUB_USER:-}
      - REGISTRY_DOCKERHUB_TOKEN=${REGISTRY_DOCKERHUB_TOKEN:-}
```

(e) In the top-level `networks:` block, after `process-runs:` / `internal: true`, add:
```yaml
  # C-2 (container-images spec §11): the image scanner's network. NOT
  # internal -- the scanner must reach public and private registries. Docker
  # cannot filter it by host, so `allowed_registries` is enforced twice in
  # software (the app on add, the pipeline and the scanner before any pull)
  # and the scanner is the only container on it besides MinIO (ISSUES
  # I-123). Neither the database nor the socket proxy is attached.
  scanner-egress:
    driver: bridge
```

- [ ] **Step 4: `.env.example`**

After the `# PROCESS_IMAGE_POLICY_FILE=` line add:
```
# The image scanner (C-2): its image (built by `docker buildx bake -f
# services/process-runtime/docker-bake.hcl image-scanner`), its network
# (compose: the egress-capable `stac-higher_scanner-egress`; the CODE default
# `none` makes every scan fail), whether it refreshes the Grype DB at scan
# start (false for air-gap), the DB mirror, and scans at once.
# IMAGE_SCANNER_IMAGE=stac-higher-image-scanner:local
# PROCESS_SCANNER_NETWORK=stac-higher_scanner-egress
# IMAGE_SCANNER_DB_UPDATE=true
# GRYPE_DB_UPDATE_URL=
# IMAGE_SCAN_CONCURRENCY=1
# Optional Docker Hub credential for docker.io images without a group
# credential; unset = anonymous pulls (ISSUES I-125).
# REGISTRY_DOCKERHUB_USER=
# REGISTRY_DOCKERHUB_TOKEN=
```

- [ ] **Step 5: Run the tests**

Run: `cd services/pipeline && uv run pytest tests/test_health.py tests/test_compose_scanner.py -q`
Expected: PASS. If `_service("pipeline")`'s regex stops at a nested key, print `_service("pipeline")[:400]` in a scratch run to see where it ends and adjust ONLY the test's regex (the services are two-space indented; their keys are four).

- [ ] **Step 6: Gates and commit**

Run `npm run verify` and `cd services/pipeline && uv run pytest -q && uv run ruff check .`.
```bash
git add services/pipeline/src/pipeline/health.py services/pipeline/tests/test_health.py services/pipeline/tests/test_compose_scanner.py docker-compose.yml .env.example
git commit -m "feat(compose): socket proxy IMAGES=1, scanner-egress network, scanner env; /health names the image policy (C-2)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 9: Docs

**Files:**
- Modify: `docs/processes.md`
- Modify: `docs/backend.md`
- Modify: `services/pipeline/README.md`
- Modify: `docs/ISSUES.md`
- Modify: `docs/FEATURES.md`

No code. Every statement below is true once Tasks 1-8 are in.

- [ ] **Step 1: `docs/processes.md`**

(a) In "What every user image gets, whatever its own config says", replace the two sentences
`The executor enforces these once a user image can launch at all (C-2);
today every kind 2/3 revision is refused before launch, so no user image
runs yet.`
with:
```markdown
`HOME` is `/tmp` unless the revision's env sets it (uid 10001 has no home
directory). The executor enforces all of it (C-2): the image runs only as
`{reference}@{digest}`, pulled by digest when the daemon lacks it, and a
kind-2 image's entrypoint is replaced by the platform's one-line bootstrap
(`python3 -c ...`), which decodes the platform runner and your code from the
environment. A kind-3 image keeps its own `ENTRYPOINT`; `command` replaces
its `CMD`.

At **launch** the pipeline re-checks the registry row, independently of the
deploy gate: a run dies with `image_not_approved` (the row is missing or not
`approved`/`flagged`), `image_digest_mismatch`, `image_stale` (not scanned
within the policy window; an exception does not cover this), or
`image_group_mismatch` (the registry credential it is pulled with was
deleted or no longer matches the registry). A `flagged` image still
launches: a rescan that newly fails blocks new deploys, not running
pipelines. A pull the registry refuses spends one of the run's attempts. A
missing image policy or master key requeues the run without spending one.
```
(b) In "When a deploy is refused", replace the sentences from `The scanner, the
image registry page and rescans are being built now.` through `other route dies naming ADR 0021.` with:
```markdown
The scanner runs as a platform container per scan (Syft for the SBOM, Grype
for the match, its vulnerability DB baked into the scanner image and
refreshed at scan start where the deployment allows). A passing scan
approves the image; a failing one rejects it with the policy's reasons; a
scan that cannot finish leaves the image `scan_failed`. The image registry
page and the add-image API (C-3) and the daily rescans (C-4) are being
built; until C-3 lands an image is added only by inserting its
`container_images` + `image_scans` rows.
```

- [ ] **Step 2: `docs/backend.md`**

(a) In the `docker-socket-proxy` service row, replace the text
```
(`CONTAINERS=1 POST=1`, everything else 403 — ADR 0013)
```
with
```
(`CONTAINERS=1 IMAGES=1 POST=1`, everything else 403 — ADR 0013; `IMAGES` since C-2 for pull-by-digest, ADR 0021: the pipeline can now pull and delete images on the daemon, and it pulls only digests that exist as an approved/flagged `container_images` row)
```

(b) Add a row to the services table directly after the `docker-socket-proxy` row:
```markdown
| **image scanner** | none (a run) | Not a service: `services/image-scanner/` is a platform image (Syft 1.52.0 + Grype 0.119.0, vulnerability DB baked at build) that the pipeline's `pipeline.image_scan_drain` launches once per `image_scans` row through the same executor as a process run (C-2, ADR 0021). It runs on the `scanner-egress` network (egress-capable, MinIO attached, nothing else), gets an STS credential bounded to `scans/{image_id}/{scan_id}/`, and writes the SBOM pair, the Grype findings and `result.json` there. Build: `docker buildx bake -f services/process-runtime/docker-bake.hcl image-scanner`. `allowed_registries` is enforced in software because the network cannot filter by host (I-123). |
```

- [ ] **Step 3: `services/pipeline/README.md`**

(a) In the "Environment contract" table, in the `PROCESS_IMAGE_POLICY_FILE` row replace the sentence
```
C-1 only parses and evaluates it; the scan drain and the launch path (C-2) are its first runtime readers.
```
with
```
The scan drain reads it every tick and the launch path for every kind 2/3 run (C-2); a missing or invalid file skips the drain tick and requeues those runs, and `GET /health` reports it under `image_policy`.
```
and add these rows after that row:
```markdown
| `IMAGE_SCANNER_IMAGE` | `stac-higher-image-scanner:local` | The platform scanner image (C-2, container-images spec §6.1; `services/image-scanner/`). |
| `PROCESS_SCANNER_NETWORK` | `none` | The scanner run's Docker network (spec §11). `none` means every scan fails for want of a registry; compose sets `stac-higher_scanner-egress`. |
| `IMAGE_SCANNER_DB_UPDATE` | `true` | Refresh the baked Grype DB at scan start (spec §6.1). `false` for an air-gapped deployment, which rebuilds the scanner image instead. |
| `GRYPE_DB_UPDATE_URL` | _(unset — Anchore's listing)_ | Where that refresh comes from; an air-gap mirror when set. |
| `IMAGE_SCAN_CONCURRENCY` | `1` | Scans running at once, deployment-wide. Each holds one worker slot for up to the policy's `scan_limits.timeout_seconds` until K-4 (ISSUES I-124). |
| `REGISTRY_DOCKERHUB_USER` / `REGISTRY_DOCKERHUB_TOKEN` | _(unset)_ | Optional deployment Docker Hub credential for `docker.io` images that carry no group `registry` credential, for scans and launch pulls alike (spec §5). Unset = anonymous, which shares Docker's 100-pulls-per-6-hours-per-IP budget (ISSUES I-125). The token never appears in logs or `repr`. |
```

(b) In "Health endpoint", add `"image_policy": { "file": "/app/share/image-policy/default.json", "ok": true, "version": 1, "error": null }` to the JSON example (after `db_pool`), and after the paragraph about `db_pool` add:
```markdown
`image_policy` (C-2) names the image policy file and whether it parses. It
does **not** affect the 200/503 decision: inline processes do not need it,
and a broken policy already fails closed where it matters (no scan runs, no
user-image run launches).
```

(c) Under "### Jobs" (the Connections section's job list) or, if that list is connection-only, directly before "## Develop", add a short section:
```markdown
## Image scans (C-2)

`pipeline.image_scan_drain` (`* * * * *`, `jobs/image_scans.py`) fails scans
stalled past the policy timeout + 600 s, then claims one pending
`image_scans` row (deployment-wide cap `IMAGE_SCAN_CONCURRENCY`), launches
the scanner through the executor with an STS credential for
`scans/{image_id}/{scan_id}/`, reads `result.json` back as untrusted data
(`pipeline/images/drain.py`: it must name this scan's image, tag, kind,
prefix and platform), evaluates it against the policy and transitions the
image: pass -> `approved`, fail -> `rejected` (admission) or `flagged`
(rescan of an approved image, unless an exception is live), failure ->
`scan_failed`. A second add of an already-known digest folds into the
existing row. The orphan reaper judges scanner containers
(`stac-higher.run-kind=image_scan`) against `image_scans`, never
`process_runs`.
```

- [ ] **Step 4: `docs/ISSUES.md`**

(a) I-123: replace its `- Tracked in:` line with:
```
- Tracked in: ADR 0021 Consequences; `docker-compose.yml` `scanner-egress` (built in C-2).
```

(b) I-124: insert before its `- Tracked in:` line (as the paragraph's last sentence):
```
C-2 caps scans at `IMAGE_SCAN_CONCURRENCY` (default 1) deployment-wide, so at
most that many slots are held, and runs each scan in a thread so the event
loop is never blocked.
```

(c) I-125: replace the words
```
Scans and launches of `docker.io` references
```
with
```
Scans (the scanner's own registry reads, Syft's layer pulls included) and
launch pulls (the daemon's) of `docker.io` references
```
and replace its `- Tracked in:` line with:
```
- Tracked in: `services/pipeline/README.md` env table (C-2).
```

(d) Append a new entry after I-136 (if C-3 has merged an I-137 by the time you rebase, take the next free number):
```markdown
### I-137 · Pulled user images are never removed from the Docker daemon 🟡
C-2 pulls a user image by digest the first time a run needs it
(`DockerExecutor._ensure_image`) and nothing removes it afterwards: a
revoked or superseded image's layers stay on the host until an operator
prunes them. The socket proxy's `IMAGES=1` would allow a delete, but deleting
from the daemon needs a reference count across in-flight runs and every
revision still pinning the digest, which is C-4's retention territory (spec
§8.3) and K-4's reconcile loop in cloud (the kubelet's own image GC applies
on Kubernetes). Deployment-checklist item for compose hosts: `docker image
prune` on a schedule, never while a run is starting.
- Found in: the C-2 plan (2026-09-27).
```

- [ ] **Step 5: `docs/FEATURES.md`**

Replace the C-2 row `| C-2 · Scanner image, drain, digest-pinned launch | ⬜ | #51 |` with:
```markdown
| C-2 · Scanner image, drain, digest-pinned launch | ✅ | `services/image-scanner/` (Syft 1.52.0 + Grype 0.119.0, checksum-verified, DB baked, `boto3` at the runtime image's pin; bake target `image-scanner`, CI matrix, date tag). `pipeline.image_scan_drain` (`jobs/image_scans.py`, `images/drain.py`, `images/scan_launch.py`) runs one scan at a time as a platform run on `scanner-egress` with STS for `scans/{image_id}/{scan_id}/`, believes `result.json` only when it names this scan, evaluates, transitions (approved / rejected / flagged / scan_failed), folds duplicate digests, fails stalled scans. The scanner's `top` is chosen by the policy's rules. Launch path: `resolve_run_image` re-checks the row at launch (`image_not_approved` / `image_digest_mismatch` / `image_stale` / `image_group_mismatch`; `flagged` launches), `DockerExecutor` pulls by digest (`X-Registry-Auth` from the group credential or `REGISTRY_DOCKERHUB_*`) and forces uid `10001:10001` + a `/tmp` tmpfs, kind 2 gets the runner bootstrap, kind 3 `command` → `Cmd`. Socket proxy `IMAGES=1`. `/health` `image_policy`. The reaper judges scan containers against `image_scans`. No app change, no DDL |
```

- [ ] **Step 6: Gate and commit**

Run `npm run verify`, then `cd services/pipeline && uv run pytest -q && uv run ruff check .`.
```bash
git add docs/processes.md docs/backend.md services/pipeline/README.md docs/ISSUES.md docs/FEATURES.md
git commit -m "docs: user images launch by digest, the scanner and its drain, env and health; ISSUES I-123..I-125 updated, I-137 (C-2)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Rebase, full gates, PR, smoke (lead only)

- [ ] `git fetch origin main && git rebase origin/main`. Expected overlap with C-3 (#52), which runs in parallel: `docs/FEATURES.md` (the C-3 row beside C-2's), `docs/processes.md` ("Bring your own image": C-3 documents the deploy-form chooser; keep both, and drop C-2's "until C-3 lands an image is added only by inserting rows" sentence if C-3 has merged), `docs/backend.md` (C-3 adds route rows; C-2 edits service rows), `docs/ISSUES.md` (renumber C-2's I-137 if taken), and possibly `services/pipeline/README.md`. C-2 touches nothing under `app/` and no fixture, so no code conflict is expected; if C-3 changed `tests/contract-fixtures/image-scan-result.json` or the failure shape, re-run `tests/test_image_scan_drain.py` and `tests/test_image_scanner.py` (they parse against it). If K-3 (#11) merged first, nothing here collides (no migration). For `package-lock.json`: `git checkout --theirs package-lock.json && npm install && git add package-lock.json`.
- [ ] Gates: `npm run verify`, then `cd services/pipeline && uv run pytest -q && uv run ruff check . && uv run ruff check ../image-scanner`. All green.
- [ ] `git push -u origin feat/c2-scanner-drain`, then `gh pr create --base main --title "C-2: scanner image, scan drain and digest-pinned launch"`. The body starts `Closes #51`, lists the gates run, copies "Decisions made in this plan" below as deviations and choices, names the files C-3 also touches, and ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Lead-only steps listed in the body:
  - CI's `containers.yml` must build the new `image-scanner` matrix entry (the Grype DB download happens at build) and the `pipeline` image. This is the only verification the Dockerfile gets before merge.
  - e2e is not required: no UI flow changed.
- [ ] After CI is green, squash-merge; `git worktree remove .claude/worktrees/c2-scanner-drain`.
- [ ] **Smoke (Docker policy `smoke`: rebuild/restart and the GOES canary; no measurement).** From the main checkout at the merged `main`:
  1. `docker buildx bake -f services/process-runtime/docker-bake.hcl image-scanner` and `docker compose build pipeline && docker compose up -d docker-socket-proxy minio pipeline` (the proxy and MinIO pick up `IMAGES=1` and `scanner-egress`).
  2. `curl -s localhost:8083/health | jq .image_policy` shows `"ok": true`.
  3. `DATABASE_URL=postgresql://username:password@localhost:5433/postgis uv run pytest tests/test_integration_images_repo.py` from `services/pipeline` (the Pg SQL against migration 030; it cleans up after itself).
  4. One admission scan, inserted by hand (C-3's API may not exist yet):
     ```sql
     WITH i AS (INSERT INTO stac_higher.container_images (reference, tag_at_add, added_by)
                VALUES ('docker.io/library/python', '3.12-slim', 'lead-smoke') RETURNING id)
     INSERT INTO stac_higher.image_scans (image_id, kind, requested_by)
     SELECT id, 'admission', 'lead-smoke' FROM i RETURNING id, image_id;
     ```
     Within ~2 minutes (plus the scan) `SELECT status, digest, verdict->'reasons' FROM stac_higher.container_images WHERE added_by = 'lead-smoke'` shows `approved` or `rejected` with reasons, the scan row is `done` with `log_ref`/`findings_ref`, and `mc ls local/stac-higher/scans/<image_id>/<scan_id>/` lists `sbom.syft.json`, `sbom.cdx.json`, `findings.grype.json`, `result.json`, `log`. Record the outcome in the PR; do not record durations or memory (C-5 measures).
  5. The GOES canary still publishes (inline kind-1 runs are unaffected by the launch-path change).
  6. Leave the smoke image rows in place or `UPDATE ... SET status = 'revoked'`; there is no DELETE route by design (spec §4.4).
- [ ] **Live checks beyond smoke:** open ONE lead-only issue, "C-2 live checks beyond smoke", in the C queue, linked from #54 (C-5), listing: (a) a kind-2 revision (the GOES GeoColor code on the platform runtime image added by its GHCR reference) launched on an approved digest, with `docker inspect` showing `User 10001:10001`, the `/tmp` tmpfs and the bootstrap entrypoint; (b) a kind-3 revision with `command`; (c) a private GHCR image through a group `registry` connection, confirming Syft's Docker Hub / GHCR auth authority (Decision 24) and the daemon pull with `X-Registry-Auth`; (d) a rescan inserted by hand on an approved image, with the policy tightened, going `flagged` while a triggered run still launches; (e) a scan killed mid-run (`docker kill stac-scan-<id>`) ending `scan_failed` and its container reaped. C-5 (#54) may absorb it.
- [ ] Follow-ups to record on the issues: C-4 (#53) owns the rescan tick, the diff (the drain stores `diff: null`), drift HEADs (`tag_drift` is always null from C-2's scanner), alerts (`pipeline/images/alerts.py` was not created: the kind is still declared-only), exception expiry, retention of `scans/` objects (including the orphaned `scans/{provisional_id}/…` objects a dedup leaves) and I-137.

---

## Decisions made in this plan

Each resolves a C-2 question the spec leaves open, choosing the option most consistent with spec §14. They go into the PR body verbatim.

1. **Failed scans keep the C-1 shape; no contract change.** Spec §4.2 says `{error}`; C-1's fixture and both readers want `{version, kind, reference, tag, error}`. The scanner always knows its identity from its environment and writes it, and when the scanner leaves nothing (crash, OOM, timeout) the drain synthesizes the same shape from the row (`failure_result`), as does the stall sweep's SQL. No fixture, reader or app change, which keeps C-2 out of C-3's files.
2. **`top` is chosen by the policy's rules, emitted by risk.** Selection tiers: KEV, fixed CRITICAL, unfixed CRITICAL, fixed HIGH by EPSS, unfixed HIGH, then the rest by risk; the chosen <= 25 are emitted by risk descending (the fixture's documented order). C-1's residual (decision 11) is closed in practice: a blocking finding falls out of `top` only when 25 higher-tier findings already block.
3. **`published_at` comes from the Grype DB**, `vulnerability_handles.published_date` (schema v6, read-only sqlite3), because Grype's JSON (checked against v0.119.0's presenter models) carries no published date. A failed lookup is null, which `evaluate()` treats as an unknown age: fail closed.
4. **`GRYPE_DB_VALIDATE_AGE=false`.** Grype refuses a DB older than 5 days by default, and the baked DB is older than that by design; its age is reported as `scanner.db_built_at` instead (spec §6.1). `GRYPE_DB_AUTO_UPDATE=false`: the only refresh is the explicit one at scan start.
5. **An oversized image is `scan_failed` with `error: "image_too_large: ..."`,** not `rejected`: nothing was pulled, so there is no SBOM or findings to hang a verdict on. `evaluate()`'s `image_too_large` reason still fires if a later, smaller policy meets a stored scan.
6. **The drain applies the §4.3 transitions itself (`next_image_status`)**, rescans included, because spec §8.1 puts "transition the image" in the drain and C-3's manual rescan button will insert rescan rows before C-4 lands. Rules: `revoked` is terminal; pass (or a live exception) -> `approved`, including from `rejected`/`scan_failed` (spec decision 3, a passing scan auto-approves); a failing scan flags an `approved`/`flagged` image and rejects anything else. C-4 adds the tick, the diff, drift, alerts and exception expiry around it.
7. **A rescan of an image with no stored SBOM or digest runs as an admission** (full pull). This is how a re-request after `scan_failed` works whichever kind C-3 inserts.
8. **Dedup by digest (spec §9.1) keeps the existing row's SBOM.** The scan row is re-pointed and keeps `kind`, the existing row gets the verdict, status (by `next_image_status`), `last_scan_id` and `last_scanned_at`, and the provisional row is deleted only while it is still `pending`/`scanning`/`scan_failed`. The new scan's objects stay under `scans/{provisional_id}/…`; their pruning is C-4's retention leg.
9. **One scan per tick, `IMAGE_SCAN_CONCURRENCY` (default 1) deployment-wide**, enforced by a transaction-scoped advisory lock around the count-and-claim, so ticks overlapping a 15-minute scan never start a second one and M3-D's 12 slots are never all scans (I-124).
10. **A scan that cannot start fails; nothing requeues it.** Executor outage, STS failure, a gone or undecryptable credential: the scan row goes `failed` with the reason and the operator re-requests (C-4's tick re-requests rescans anyway). A scan still `running` past the policy timeout + 600 s is failed by the next tick's stall sweep.
11. **Scanner containers carry `stac-higher.run-kind=image_scan`**, and the orphan reaper judges them against `image_scans` (live only while `running`), never `process_runs`, and skips them when it cannot read that ledger. Containers from before C-2 have no kind label and read as process runs.
12. **The pull lives in `DockerExecutor.launch`** (inspect by the pinned name; on 404, `POST /images/create?fromImage=…&tag=<digest>` with `X-Registry-Auth`), driven by `RunSpec.user_image`, which also forces the uid and tmpfs and refuses any image not pinned by digest. A refused pull (`ImagePullFailed`) spends a run attempt; an unreachable daemon requeues as before. On Kubernetes the same `RunSpec` fields map to `imagePullSecret` and `runAsUser` (spec §12).
13. **The tmpfs is `rw,nosuid,nodev,size=<memory_mb>m`, without `noexec`** (tools that JIT or unpack into /tmp would break), and `HOME=/tmp` is a default the revision's env may override. `ReadonlyRootfs` is not set: spec §3.2 does not list it.
14. **Launch-time reasons reuse the deploy gate's strings** as the prefix of `process_runs.error`. Launch does not re-check group ownership (the gate did, and revisions are immutable), except that a deleted, mismatched or empty registry credential kills the run as `image_group_mismatch` rather than falling back to an anonymous pull.
15. **A missing image policy or master key at launch requeues** a kind 2/3 run without spending an attempt (the hardware-profile precedent); an inline run never reads the policy.
16. **`resolve_runtime_image` still refuses kinds 2-3.** It stays the alias resolver; `resolve_run_image` is the only path to a user image, so a caller that bypasses it cannot launch one.
17. **`check_user_image_launchable` is pure** (`runtime, row, scan_window_days, now`), mirroring the app gate's order and its inclusive staleness boundary; `resolve_run_image` loads the row, the policy and the credential around it.
18. **The kind-2 runner ships as `runtime_entrypoint.py.txt`**, a byte-identical data file pinned by sha256 to `services/process-runtime/entrypoint.py`. A `.py` copy would be linted and its `noqa` codes for rules this project does not enable would trip RUF100.
19. **Four scanner variables beyond spec §6.2:** `STAC_HIGHER_ALLOWED_REGISTRIES` (the scanner's own defence-in-depth check, §6.3 step 1, needs the list), `STAC_HIGHER_IMAGE_IDENTITY` (a rescan must echo digest/platform/size/config for the §6.4 shape without pulling), `STAC_HIGHER_DB_UPDATE` and `GRYPE_DB_UPDATE_URL` (§6.1's refresh switch and mirror).
20. **The scanner installs `boto3==1.43.49`**, the pin the platform runtime image already vouches for, and is otherwise stdlib; the pipeline gains no dependency. A test holds the two pins equal.
21. **Deferred to C-4, as §15 assigns them there:** `pipeline/images/alerts.py` (the issue's list names it, but writing `process_image_flagged` while it is still a declared-only kind would break C-1's alert-kinds fixture), drift HEADs (`tag_drift` is null), the diff (`diff: null` is stored), the rescan tick, exception expiry and retention.
22. **`/health`'s `image_policy` key is informational** and never changes 200/503: inline processes do not need the policy, and the policy already fails closed where it matters.
23. **The scanner image is built by `containers.yml`'s matrix** (with a `YYYYMMDD` tag so the baked DB's age is visible) and by a bake target outside the default group, so the runtime job does not build it twice.
24. **Syft's auth authority for Docker Hub is `index.docker.io`** (go-containerregistry's name for it), the registry host otherwise. Not verifiable without a private Hub image; listed for the lead's beyond-smoke issue.
25. **Scan pulls are the scanner's, launch pulls the daemon's.** The registry secret reaches the scanner's environment only for an admission scan and the daemon only through `X-Registry-Auth`; the scanner strips it from the environment its binaries inherit.
26. **Pulled user images are never removed from the daemon** in C-2; recorded as I-137.
27. **Scans run in `asyncio.to_thread`**: the Engine API client is synchronous, and a 15-minute blocking `wait` on the event loop would stall every other job in the worker.
28. **No DDL.** Every column C-2 writes exists in migration 030 (`image_scans.executor_handle/log_ref/result/findings_ref`, the image's admission columns).

## Self-review

- **Spec coverage.**
  - §6.1 scanner image: Task 6 (pinned, checksum-verified binaries, baked DB, non-root, date tag, bake + CI).
  - §6.2 a scan is a run: Task 7 (`build_scan_spec`/`execute_scan`: same executor, `scan_limits`, `scanner_network`, the env table, STS for the scan prefix, no platform secrets) with Task 3's `mint_prefix_credentials`.
  - §6.3 `scan.py`: Task 6 (allowed registries, HEAD tag, platform pick, size refusal before blobs, config, Syft x2, Grype, result last, error result + exit 1; rescan SBOM-only). Drift HEAD deferred (Decision 21).
  - §6.4 result: Task 6 builds it, Task 4 stores it canonically, both parsed by C-1's parser in tests.
  - §8.1 drain: Task 7 (claim batch 1, running, scanning on admission only, launch, wait, result, evaluate, transitions, admission vs rescan fields, log to `scans/…/log`, blocking wait in a thread).
  - §8.4 launch path: Tasks 1 and 5 (`resolve_run_image`, dead-run reasons, pull by digest with `X-Registry-Auth`, `RunSpec.image = reference@digest`, bootstrap, `Cmd = command`, forced uid). §3.1 bootstrap: Task 2. §3.2 hardening: Task 1.
  - §8.5 socket proxy `IMAGES=1`: Task 8, residual risk in `docs/backend.md` (Task 9) and ADR 0021 (already written).
  - §11 networks: Task 8 (`scanner-egress`, MinIO on it, `PROCESS_SCANNER_NETWORK`). §5 `REGISTRY_DOCKERHUB_*`: Tasks 3, 4, 8. §7.2 `/health` names the file: Task 8.
  - §9.1 dedup in the drain: Task 7. Known limits I-123/I-124/I-125 updated, I-137 added: Task 9.
- **Dry run.** Before committing this plan, every task's code was applied to a scratch copy of `services/pipeline` + `services/image-scanner` (outside the repo) and the whole pipeline suite ran green (1585 passed, 11 skipped) with `ruff check` clean on both packages. The docs edits (Task 9) and the Docker build were not exercised.
- **Placeholders.** None. Every file is given in full or as an exact replacement; the binaries' checksums were read from the v1.52.0 / v0.119.0 release `checksums.txt`. Where a line may exceed ruff's 100 columns after pasting, the task says to wrap without changing content.
- **Type consistency.**
  - `RegistryAuth(username, password, server)` is defined in Task 1 and built in Task 4 (`resolve_registry_auth`), carried by `ResolvedImage` (Task 5) into `RunSpec.registry_auth`, and read by `DockerExecutor._ensure_image`.
  - `ImageRow`/`ClaimedScan`/`RegistryCredentialRow` (Task 4) are what `resolve_run_image` (Task 5), `scan_env`/`execute_scan` and `drain_one` (Task 7) consume; `FakeImagesRepo` implements every `ImagesRepo` method with the same keyword names (`max_running`, `started_before`, `scan_id`, `verdict`, `status`, `at`, `exclude_id`, `provisional_id`, `existing_id`).
  - `ScanRun(status, handle_id, log_ref)` is produced by `execute_scan` and consumed by `drain_one`; the drain's `run_scan(scan, kind)` and `read_result(key)` callables match the job's closures.
  - `RunSpec.kind` values `"process"`/`"image_scan"` match `RUN_KINDS`, the Docker label, `LaunchedRun.kind` and the reaper's check; `SCANNER_RUN_KIND == "image_scan"`.
  - `failure_result(kind, reference, tag, error)` has the same signature in `pipeline.images.scan_result` and `stac_higher_scanner.scan` (two copies by design: the scanner ships without the pipeline).
  - `mint_prefix_credentials(settings, *, session_name, prefix, timeout_seconds, sts_client, read_prefixes)` is defined in Task 3 and called that way in Task 7.
