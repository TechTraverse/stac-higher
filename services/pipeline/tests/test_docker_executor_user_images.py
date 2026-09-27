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


def test_a_pull_failure_message_never_contains_the_password():
    """The failure message is built from the daemon's own response, never
    from the credential we sent — a leaked password in a log or an error
    surfaced to an operator would defeat the point of a scoped pull secret."""
    engine = Engine(
        has_image=False,
        pull_error=EngineHTTPError(
            "docker POST failed: 401", status=401, detail="unauthorized"
        ),
    )
    with pytest.raises(ImagePullFailed) as err:
        executor(engine).launch(spec(registry_auth=RegistryAuth("robot", "s3cret", "ghcr.io")))
    assert "s3cret" not in str(err.value)


def test_an_unreachable_daemon_is_an_outage_not_a_pull_failure():
    engine = Engine(inspect_error=ExecutorUnavailable("docker GET unreachable"))
    with pytest.raises(ExecutorUnavailable) as err:
        executor(engine).launch(spec())
    assert not isinstance(err.value, ImagePullFailed)


def test_a_reference_half_that_is_not_a_valid_image_reference_is_refused():
    engine = Engine()
    with pytest.raises(ImagePullFailed, match="not pinned by digest"):
        executor(engine).launch(spec(image=f"NOTVALID@{DIGEST}"))
    assert engine.calls == []


@pytest.mark.parametrize("status", [400, 422])
def test_an_inspect_400_or_422_is_a_pull_failure_that_spends_an_attempt(status):
    engine = Engine(
        inspect_error=EngineHTTPError(
            f"docker GET failed: {status}", status=status, detail="bad reference"
        )
    )
    with pytest.raises(ImagePullFailed):
        executor(engine).launch(spec())


@pytest.mark.parametrize("status", [403, 500, 503])
def test_an_inspect_403_or_5xx_stays_an_outage(status):
    engine = Engine(
        inspect_error=EngineHTTPError(
            f"docker GET failed: {status}", status=status, detail="daemon trouble"
        )
    )
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
