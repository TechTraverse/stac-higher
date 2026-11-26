"""K-5 (K5-R1): the stdlib Kubernetes API client, against the fake API server."""

from __future__ import annotations

import asyncio
import re
import socket
import ssl
from pathlib import Path

import pytest

from _fake_kube import FakeKube
from pipeline.config import Settings
from pipeline.process.executor import ExecutorUnavailable
from pipeline.process.kube_api import (
    KubeApi,
    KubeApiError,
    KubeConfig,
    KubeGone,
    KubeNotFound,
)

NS = "stac-higher-runs"
JOBS = f"/apis/batch/v1/namespaces/{NS}/jobs"
PODS = f"/api/v1/namespaces/{NS}/pods"
SECRETS = f"/api/v1/namespaces/{NS}/secrets"


def _job(name: str, labels: dict | None = None) -> dict:
    return {
        "metadata": {"name": name, "labels": {"stac-higher.run-id": name, **(labels or {})}},
        "spec": {"template": {"spec": {"containers": [{"name": "run", "image": "x"}]}}},
    }


@pytest.fixture
def fake():
    with FakeKube() as server:
        yield server


@pytest.fixture
def token_file(tmp_path, fake) -> Path:
    path = tmp_path / "token"
    path.write_text(fake.token + "\n")
    return path


@pytest.fixture
def api(fake, token_file) -> KubeApi:
    return KubeApi(KubeConfig(fake.url, token_file, None))


def test_requests_carry_the_bearer_token_from_the_file(api, fake):
    assert api.get(JOBS)["items"] == []
    assert fake.calls[-1].verb == "list"


def test_a_rotated_token_is_used_on_the_next_request(api, fake, token_file):
    api.get(JOBS)
    fake.token = "rotated-token"
    with pytest.raises(KubeApiError) as err:
        api.get(JOBS)
    assert err.value.status == 401
    token_file.write_text("rotated-token")
    assert api.get(JOBS)["items"] == []


def test_a_missing_token_file_is_unavailable(fake, tmp_path):
    missing = tmp_path / "nope"
    api = KubeApi(KubeConfig(fake.url, missing, None))
    with pytest.raises(ExecutorUnavailable, match=re.escape(str(missing))):
        api.get(JOBS)
    assert fake.calls == []


@pytest.mark.parametrize(
    ("code", "cls", "reason"),
    [
        (404, KubeNotFound, "NotFound"),
        (410, KubeGone, "Expired"),
        (403, KubeApiError, "Forbidden"),
        (409, KubeApiError, "AlreadyExists"),
        (422, KubeApiError, "Invalid"),
        (500, KubeApiError, "InternalError"),
    ],
)
def test_error_statuses_map_to_typed_errors(api, fake, code, cls, reason):
    fake.fail_next("GET", "jobs", code, message="the server says no")
    with pytest.raises(cls) as err:
        api.get(f"{JOBS}/x")
    assert type(err.value) is cls
    assert isinstance(err.value, ExecutorUnavailable)
    assert err.value.status == code and err.value.reason == reason
    assert str(err.value) == (
        f"kubernetes GET {JOBS}/x failed: {code} {reason}: the server says no"
    )


def test_a_forbidden_request_is_a_403_api_error(api):
    with pytest.raises(KubeApiError) as err:
        api.get(f"{SECRETS}/x")  # the Role has no `get secrets`
    assert err.value.status == 403 and err.value.reason == "Forbidden"


def test_a_refused_connection_is_unavailable(token_file):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    api = KubeApi(KubeConfig(f"http://127.0.0.1:{port}", token_file, None))
    with pytest.raises(ExecutorUnavailable, match="unreachable") as err:
        api.get(JOBS)
    assert not isinstance(err.value, KubeApiError)


def test_a_redacted_create_never_echoes_the_server_message(api, fake):
    fake.fail_next("POST", "secrets", 422, message="data[AWS_SECRET_ACCESS_KEY]: s3cr3t")
    with pytest.raises(KubeApiError) as err:
        api.create(SECRETS, {"metadata": {"name": "s"}}, redact=True)
    text = str(err.value)
    assert "422" in text and "Invalid" in text and "s3cr3t" not in text
    assert err.value.__cause__ is None and err.value.__suppress_context__


def test_create_returns_the_stored_object(api, fake):
    created = api.create(JOBS, _job("a"))
    assert created["metadata"]["uid"] == fake.objects("batch", "jobs")["a"]["metadata"]["uid"]


def test_list_follows_continue_and_returns_the_resource_version(api, fake):
    for name in ("a", "b", "c"):
        api.create(JOBS, _job(name))
    api.create(JOBS, {"metadata": {"name": "stranger"}, "spec": {}})
    items, rv = api.list(JOBS, label_selector="stac-higher.run-id", limit=1)
    assert [i["metadata"]["name"] for i in items] == ["a", "b", "c"]
    lists = [c for c in fake.calls if c.verb == "list"]
    assert len(lists) == 3
    assert lists[1].query["continue"]
    assert rv and int(rv) > 0


def test_patch_sends_a_merge_patch(api, fake):
    api.create(JOBS, _job("j"))
    pod = fake.add_pod("j", phase="Running")
    patched = api.patch(f"{PODS}/{pod}", {"spec": {"activeDeadlineSeconds": 1}})
    assert patched["spec"]["activeDeadlineSeconds"] == 1


def test_delete_with_a_propagation_policy(api, fake):
    api.create(JOBS, _job("j"))
    api.delete(f"{JOBS}/j", propagation="Foreground")
    assert fake.calls[-1].query == {"propagationPolicy": "Foreground"}
    with pytest.raises(KubeNotFound):
        api.delete(f"{JOBS}/j")


def test_get_raw_returns_bytes(api, fake):
    api.create(JOBS, _job("j"))
    pod = fake.add_pod("j", phase="Failed", state={"terminated": {"exitCode": 1}}, log=b"abc")
    assert api.get_raw(f"{PODS}/{pod}/log", query={"container": "run"}) == b"abc"


def test_environment_proxies_are_ignored(api, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    assert api.get(JOBS)["items"] == []


async def _collect(agen, n):
    out = []
    async for event in agen:
        out.append(event)
        if len(out) >= n:
            break
    return out


async def test_watch_yields_decoded_events(api, fake):
    _, rv = await asyncio.to_thread(api.list, JOBS, label_selector="stac-higher.run-id")
    agen = api.watch(JOBS, query={"resourceVersion": rv, "labelSelector": "stac-higher.run-id"})
    task = asyncio.create_task(_collect(agen, 2))
    await asyncio.sleep(0.2)
    await asyncio.to_thread(api.create, JOBS, _job("w"))
    await asyncio.to_thread(fake.set_job_status, "w", active=1)
    events = await asyncio.wait_for(task, 5)
    assert [(e["type"], e["object"]["metadata"]["name"]) for e in events] == [
        ("ADDED", "w"), ("MODIFIED", "w"),
    ]
    watches = [c for c in fake.calls if c.verb == "watch"]
    assert len(watches) == 1 and watches[0].query["resourceVersion"] == rv


async def test_a_normal_close_ends_the_iterator(api, fake):
    agen = api.watch(JOBS, query={"resourceVersion": "1"})
    task = asyncio.create_task(_collect(agen, 10))
    await asyncio.sleep(0.2)
    fake.close_watches()
    assert await asyncio.wait_for(task, 5) == []


async def test_watch_raises_gone_on_an_error_410(api, fake):
    await asyncio.to_thread(api.create, JOBS, _job("a"))
    await asyncio.to_thread(api.create, JOBS, _job("b"))
    fake.compact()
    with pytest.raises(KubeGone) as err:
        await _collect(api.watch(JOBS, query={"resourceVersion": "1"}), 10)
    assert err.value.status == 410


async def test_an_http_410_on_connect_is_gone(api, fake):
    fake.fail_next("GET", "jobs", 410, message="too old")
    with pytest.raises(KubeGone):
        await _collect(api.watch(JOBS, query={"resourceVersion": "1"}), 1)


async def test_watch_raises_unavailable_on_a_non_200(api, fake):
    fake.fail_next("GET", "jobs", 500, message="etcd down")
    with pytest.raises(KubeApiError) as err:
        await _collect(api.watch(JOBS, query={"resourceVersion": "1"}), 1)
    assert err.value.status == 500 and "etcd down" in str(err.value)


async def test_watch_raises_unavailable_after_idle_timeout_of_silence(api):
    with pytest.raises(ExecutorUnavailable, match="idle"):
        await _collect(api.watch(JOBS, query={"resourceVersion": "1"}, idle_timeout=0.2), 1)


async def test_a_watch_that_cannot_connect_is_unavailable(token_file):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    api = KubeApi(KubeConfig(f"http://127.0.0.1:{port}", token_file, None))
    with pytest.raises(ExecutorUnavailable, match="unreachable"):
        await _collect(api.watch(JOBS, query={}), 1)


def test_https_uses_the_ca_file(tmp_path):
    calls: list[dict] = []

    def factory(**kwargs):
        calls.append(kwargs)
        return ssl.create_default_context()

    ca = tmp_path / "ca.crt"
    api = KubeApi(KubeConfig("https://x:443", tmp_path / "token", ca,
                             ssl_context_factory=factory))
    assert calls == []  # building the client does no I/O
    ctx = api.ssl_context()
    assert calls == [{"cafile": str(ca)}]
    assert ctx.check_hostname is True and ctx.verify_mode == ssl.CERT_REQUIRED
    assert api.ssl_context() is ctx  # built once


def test_an_unreadable_ca_file_is_unavailable(tmp_path):
    api = KubeApi(KubeConfig("https://x:443", tmp_path / "token", tmp_path / "missing.crt"))
    with pytest.raises(ExecutorUnavailable, match=r"missing\.crt"):
        api.ssl_context()


def test_from_settings_needs_an_api_url(tmp_path):
    with pytest.raises(ExecutorUnavailable, match="PROCESS_K8S_API_URL"):
        KubeApi.from_settings(Settings())
    api = KubeApi.from_settings(
        Settings.from_env(env={
            "PROCESS_K8S_API_URL": "https://10.0.0.1:443",
            "PROCESS_K8S_TOKEN_FILE": str(tmp_path / "t"),
            "PROCESS_K8S_CA_FILE": str(tmp_path / "ca"),
        })
    )
    assert api.config.api_url == "https://10.0.0.1:443"
    assert api.config.token_file == tmp_path / "t"
    assert api.config.ca_file == tmp_path / "ca"
    loopback = KubeApi.from_settings(
        Settings.from_env(env={"PROCESS_K8S_API_URL": "http://127.0.0.1:8001"})
    )
    assert loopback.config.ca_file is None
