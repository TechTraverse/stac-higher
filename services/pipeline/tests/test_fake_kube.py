"""K-5: the fake Kubernetes API server, pinned before the executor relies on it.

Plain ``urllib`` / ``asyncio`` against the fake, so a bug in the fake cannot
hide behind a bug in ``kube_api``. The fake enforces the Role in
``infra/kubernetes/pipeline-role.yaml``: every executor test that passes
against it proves the Role is sufficient (K5-R10).
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

import pytest

from _fake_kube import KUEUE_API_VERSION, FakeKube
from pipeline.process.http_stream import is_chunked, iter_body_lines, read_http_head

NS = "stac-higher-runs"
JOBS = f"/apis/batch/v1/namespaces/{NS}/jobs"
PODS = f"/api/v1/namespaces/{NS}/pods"
SECRETS = f"/api/v1/namespaces/{NS}/secrets"
WORKLOADS = f"/apis/kueue.x-k8s.io/{KUEUE_API_VERSION}/namespaces/{NS}/workloads"


def _req(fake, method, path, body=None, *, token=None, content_type=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{fake.url}{path}", data=data, method=method)
    if token is not False:
        request.add_header("Authorization", f"Bearer {token or fake.token}")
    if data is not None:
        request.add_header("Content-Type", content_type or "application/json")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = response.read()
            status = response.status
            ctype = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as err:
        payload, status, ctype = err.read(), err.code, err.headers.get("Content-Type", "")
    if "json" in ctype and payload:
        return status, json.loads(payload)
    return status, payload


def _job(name, *, queue=True, labels=None):
    meta_labels = {"stac-higher.run-id": name, **(labels or {})}
    if queue:
        meta_labels["kueue.x-k8s.io/queue-name"] = "process-runs"
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "labels": meta_labels},
        "spec": {
            "template": {
                "metadata": {"labels": {"stac-higher.run-id": name}},
                "spec": {"restartPolicy": "Never", "containers": [{"name": "run", "image": "x"}]},
            }
        },
    }


@pytest.fixture
def fake():
    with FakeKube() as server:
        yield server


def test_rbac_is_enforced_from_the_role_file(fake):
    status, body = _req(fake, "GET", f"{SECRETS}/x")
    assert status == 403
    assert body["kind"] == "Status" and body["reason"] == "Forbidden"
    status, _ = _req(fake, "POST", SECRETS, {"metadata": {"name": "s"}, "data": {}})
    assert status == 201
    assert ("", "secrets", "create") in fake.used
    assert ("", "secrets", "get") not in fake.used


def test_another_namespace_is_forbidden(fake):
    status, body = _req(fake, "GET", "/apis/batch/v1/namespaces/default/jobs")
    assert status == 403 and body["reason"] == "Forbidden"
    status, _ = _req(fake, "GET", "/api/v1/nodes")
    assert status == 403


def test_a_missing_or_wrong_token_is_401(fake):
    assert _req(fake, "GET", JOBS, token=False)[0] == 401
    assert _req(fake, "GET", JOBS, token="nope")[0] == 401
    assert _req(fake, "GET", JOBS)[0] == 200


def test_create_get_list_delete_round_trip_with_label_selectors(fake):
    for name, extra in (("a", {"a": "b"}), ("b", {}), ("c", {"a": "c"})):
        status, created = _req(fake, "POST", JOBS, _job(name, labels=extra))
        assert status == 201 and created["metadata"]["uid"]
    assert _req(fake, "POST", JOBS, _job("a"))[0] == 409
    status, got = _req(fake, "GET", f"{JOBS}/a")
    assert status == 200 and got["metadata"]["name"] == "a"
    assert got["metadata"]["creationTimestamp"].endswith("Z")

    _, listed = _req(fake, "GET", f"{JOBS}?labelSelector=a%3Db")
    assert [j["metadata"]["name"] for j in listed["items"]] == ["a"]
    _, listed = _req(fake, "GET", f"{JOBS}?labelSelector=a")
    assert [j["metadata"]["name"] for j in listed["items"]] == ["a", "c"]
    _, listed = _req(fake, "GET", f"{JOBS}?labelSelector=stac-higher.run-id&limit=1")
    assert len(listed["items"]) == 1
    assert listed["metadata"]["continue"]
    assert int(listed["metadata"]["resourceVersion"]) > 0
    token = listed["metadata"]["continue"]
    _, page2 = _req(
        fake, "GET", f"{JOBS}?labelSelector=stac-higher.run-id&limit=1&continue={token}"
    )
    assert [j["metadata"]["name"] for j in page2["items"]] == ["b"]

    assert _req(fake, "DELETE", f"{JOBS}/a")[0] == 200
    assert _req(fake, "GET", f"{JOBS}/a")[0] == 404
    status, body = _req(fake, "DELETE", f"{JOBS}/a")
    assert status == 404 and body["reason"] == "NotFound"


def test_deleting_a_job_cascades_to_what_it_owns(fake):
    _, job = _req(fake, "POST", JOBS, _job("j"))
    uid = job["metadata"]["uid"]
    owner = [{"apiVersion": "batch/v1", "kind": "Job", "name": "j", "uid": uid}]
    _req(fake, "POST", SECRETS, {"metadata": {"name": "j", "ownerReferences": owner}})
    _req(fake, "POST", SECRETS, {"metadata": {"name": "other"}})
    fake.add_pod("j", phase="Running")
    assert fake.objects("kueue.x-k8s.io", "workloads")
    _req(fake, "DELETE", f"{JOBS}/j?propagationPolicy=Foreground")
    assert fake.calls[-1].query == {"propagationPolicy": "Foreground"}
    assert set(fake.objects("", "secrets")) == {"other"}
    assert fake.objects("", "pods") == {}
    assert fake.objects("kueue.x-k8s.io", "workloads") == {}


def test_kueue_emulation_suspends_a_queue_labelled_job_and_creates_its_workload(fake):
    _, job = _req(fake, "POST", JOBS, _job("q"))
    assert job["spec"]["suspend"] is True
    workloads = list(fake.objects("kueue.x-k8s.io", "workloads").values())
    assert len(workloads) == 1
    wl = workloads[0]
    assert wl["metadata"]["labels"]["kueue.x-k8s.io/job-uid"] == job["metadata"]["uid"]
    assert wl["metadata"]["ownerReferences"][0]["name"] == "q"
    assert wl["metadata"]["ownerReferences"][0]["kind"] == "Job"
    cond = wl["status"]["conditions"][0]
    assert (cond["type"], cond["status"], cond["reason"]) == (
        "QuotaReserved", "False", "WaitingForQuota"
    )
    # Not queue-labelled, or Kueue absent: neither suspended nor queued.
    _, plain = _req(fake, "POST", JOBS, _job("p", queue=False))
    assert not plain["spec"].get("suspend")
    fake.kueue = False
    _, other = _req(fake, "POST", JOBS, _job("r"))
    assert not other["spec"].get("suspend")
    assert len(fake.objects("kueue.x-k8s.io", "workloads")) == 1


def test_without_kueue_the_workload_api_is_404(fake):
    fake.kueue = False
    status, body = _req(fake, "GET", WORKLOADS)
    assert status == 404 and body["reason"] == "NotFound"


def test_pod_patch_accepts_only_a_lower_deadline(fake):
    _req(fake, "POST", JOBS, _job("j"))
    pod = fake.add_pod("j", phase="Running")
    merge = "application/merge-patch+json"
    status, patched = _req(
        fake, "PATCH", f"{PODS}/{pod}", {"spec": {"activeDeadlineSeconds": 1}}, content_type=merge
    )
    assert status == 200 and patched["spec"]["activeDeadlineSeconds"] == 1
    assert _req(
        fake, "PATCH", f"{PODS}/{pod}", {"spec": {"activeDeadlineSeconds": 5}}, content_type=merge
    )[0] == 422
    assert _req(
        fake, "PATCH", f"{PODS}/{pod}", {"spec": {"containers": []}}, content_type=merge
    )[0] == 422
    assert _req(
        fake, "PATCH", f"{PODS}/{pod}", {"spec": {"activeDeadlineSeconds": 1}}
    )[0] == 415
    assert _req(
        fake, "PATCH", f"{PODS}/nope", {"spec": {"activeDeadlineSeconds": 1}}, content_type=merge
    )[0] == 404


def test_pod_log_honours_limit_bytes(fake):
    _req(fake, "POST", JOBS, _job("j"))
    pod = fake.add_pod("j", phase="Failed", state={"terminated": {"exitCode": 1}}, log=b"x" * 100)
    status, body = _req(fake, "GET", f"{PODS}/{pod}/log?container=run&limitBytes=10")
    assert status == 200 and body == b"x" * 10
    assert ("", "pods/log", "get") in fake.used


def test_log_of_a_waiting_container_is_400(fake):
    _req(fake, "POST", JOBS, _job("j"))
    pod = fake.add_pod("j", phase="Pending", state={"waiting": {"reason": "ContainerCreating"}})
    status, body = _req(fake, "GET", f"{PODS}/{pod}/log?container=run")
    assert status == 400 and body["reason"] == "BadRequest"


async def _watch(fake, path, *, until):
    """Collect watch lines until ``until(events)`` or the stream ends."""
    reader, writer = await asyncio.open_connection("127.0.0.1", fake.port)
    writer.write(
        f"GET {path} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {fake.token}\r\n"
        "Connection: close\r\n\r\n".encode()
    )
    await writer.drain()
    status, headers = await read_http_head(reader)
    events = []
    ended = False
    try:
        if status == 200:
            agen = iter_body_lines(reader, chunked=is_chunked(headers))
            while not until(events):
                try:
                    line = await asyncio.wait_for(agen.__anext__(), 5)
                except StopAsyncIteration:
                    ended = True
                    break
                events.append(json.loads(line))
    finally:
        writer.close()
    return status, events, ended


async def test_watch_streams_events_after_the_given_resource_version(fake):
    _, first = await asyncio.to_thread(_req, fake, "POST", JOBS, _job("old", queue=False))
    rv = first["metadata"]["resourceVersion"]

    async def act():
        await asyncio.sleep(0.2)
        await asyncio.to_thread(_req, fake, "POST", JOBS, _job("new", queue=False))
        await asyncio.to_thread(fake.set_job_status, "new", active=1)
        await asyncio.to_thread(_req, fake, "DELETE", f"{JOBS}/new")

    task = asyncio.create_task(act())
    status, events, _ = await _watch(
        fake, f"{JOBS}?watch=1&resourceVersion={rv}&labelSelector=stac-higher.run-id",
        until=lambda ev: len(ev) >= 3,
    )
    await task
    assert status == 200
    assert [(e["type"], e["object"]["metadata"]["name"]) for e in events] == [
        ("ADDED", "new"), ("MODIFIED", "new"), ("DELETED", "new"),
    ]
    assert ("batch", "jobs", "watch") in fake.used


async def test_watch_from_a_compacted_version_gets_an_error_410_event(fake):
    _, job = await asyncio.to_thread(_req, fake, "POST", JOBS, _job("a", queue=False))
    old = job["metadata"]["resourceVersion"]
    await asyncio.to_thread(_req, fake, "POST", JOBS, _job("b", queue=False))
    fake.compact()
    status, events, ended = await _watch(
        fake, f"{JOBS}?watch=1&resourceVersion={old}", until=lambda ev: False
    )
    assert status == 200 and ended
    assert events[0]["type"] == "ERROR"
    assert events[0]["object"]["code"] == 410 and events[0]["object"]["kind"] == "Status"


async def test_bookmarks_and_close_are_delivered(fake):
    _, job = await asyncio.to_thread(_req, fake, "POST", JOBS, _job("a", queue=False))
    rv = job["metadata"]["resourceVersion"]

    async def act():
        await asyncio.sleep(0.2)
        fake.bookmark()
        await asyncio.sleep(0.2)
        fake.close_watches()

    task = asyncio.create_task(act())
    _status, events, ended = await _watch(
        fake, f"{JOBS}?watch=1&resourceVersion={rv}&allowWatchBookmarks=true",
        until=lambda ev: False,
    )
    await task
    assert ended
    assert [e["type"] for e in events] == ["BOOKMARK"]
    assert events[0]["object"]["metadata"]["resourceVersion"] == rv


async def test_a_watch_ends_at_its_timeout_seconds(fake):
    status, events, ended = await _watch(
        fake, f"{JOBS}?watch=1&resourceVersion=1&timeoutSeconds=1", until=lambda ev: False
    )
    assert status == 200 and ended and events == []


def test_fail_next_injects_one_error(fake):
    fake.fail_next("GET", "jobs", 500, reason="InternalError", message="boom")
    status, body = _req(fake, "GET", JOBS)
    assert status == 500 and body["reason"] == "InternalError" and body["message"] == "boom"
    assert _req(fake, "GET", JOBS)[0] == 200


def test_the_default_deny_policy_is_served_unless_switched_off(fake):
    path = f"/apis/networking.k8s.io/v1/namespaces/{NS}/networkpolicies"
    status, policy = _req(fake, "GET", f"{path}/stac-higher-runs-default-deny")
    assert status == 200 and policy["spec"]["podSelector"] == {}
    assert _req(fake, "GET", path)[0] == 403  # get only, no list
    with FakeKube(default_deny=False) as bare:
        assert _req(bare, "GET", f"{path}/stac-higher-runs-default-deny")[0] == 404
