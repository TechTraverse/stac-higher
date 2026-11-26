"""K-6: the Kubernetes executor on a real API server, kubelet and Kueue (kind).

Skips unless ``KIND_INTEGRATION=1`` (the kind CI leg, `.github/workflows/
kind.yml`; locally: services/pipeline/README.md). Every executor call runs
as the pipeline's ServiceAccount; the admin kubeconfig is the harness's
(``tests/_kind.py``, K6-R3).

This module: identity and transport (plan Task 5), the run lifecycle (Task 6)
and the K-5 hand-off items about stopping a run, its log and the Job's
deletion (infra/kubernetes/README.md, "What a real cluster must prove",
items 3, 4, 7, 8, 9). Kueue behaviour, admission and network isolation are in
``test_integration_kind_kueue.py``."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import os
import shutil

import pytest
import yaml

import _kind
from _kind import MARKER, NAMESPACE, code_spec, kget, kubectl, observe, printing
from pipeline.process import kubernetes_executor as kx
from pipeline.process.executor import (
    RESYNC,
    ExecutorUnavailable,
    ExitStatus,
    RunEvent,
    RunHandle,
    oom_error,
)
from pipeline.process.kube_api import KubeApiError
from pipeline.process.kubernetes_manifests import (
    CONTAINER,
    DEFAULT_DENY_POLICY,
    KUEUE_API_VERSION,
)

pytestmark = _kind.skip_unless_kind
_kind.require()  # at import: a half-configured CI run fails, never skips

ROLE_FILE = _kind.REPO / "infra" / "kubernetes" / "pipeline-role.yaml"
GREETING = "hello-from-the-secret"
#: Phases in the order a healthy run passes through them.
ORDER = ["pending_capacity", "starting", "running", "exited"]


@pytest.fixture(autouse=True)
def _clean():
    kx.reset_isolation_cache()
    _kind.clean_namespace()
    yield
    _kind.clean_namespace()


def _review(path: str, kind: str, spec: dict | None = None) -> dict:
    body = {"apiVersion": path.split("/apis/")[1].rsplit("/", 1)[0], "kind": kind}
    if spec is not None:
        body["spec"] = spec
    return _kind.api().create(path, body)


def _triples(rules: list[dict]) -> set[tuple[str, str, str]]:
    return {(group, resource, verb)
            for rule in rules
            for group in rule.get("apiGroups") or []
            for resource in rule.get("resources") or []
            for verb in rule.get("verbs") or []}


# -- Task 5: identity and transport -------------------------------------------


def test_the_tests_run_as_the_pipeline_service_account():
    review = _review("/apis/authentication.k8s.io/v1/selfsubjectreviews", "SelfSubjectReview")
    assert review["status"]["userInfo"]["username"] == _kind.PIPELINE_USER


def test_the_pipeline_identity_has_exactly_the_roles_rules():
    """K6-R3: the real authorizer grants the pipeline exactly pipeline-role.yaml
    in the runs namespace, and nothing namespaced elsewhere."""
    everyone = {"authorization.k8s.io", "authentication.k8s.io"}  # self-reviews

    def granted(namespace: str) -> set[tuple[str, str, str]]:
        review = _review("/apis/authorization.k8s.io/v1/selfsubjectrulesreviews",
                         "SelfSubjectRulesReview", {"namespace": namespace})
        assert review["status"]["incomplete"] is False, review["status"]
        return {t for t in _triples(review["status"]["resourceRules"]) if t[0] not in everyone}

    role = yaml.safe_load(ROLE_FILE.read_text())
    expected = _triples(role["rules"])
    assert not any("*" in part for t in expected for part in t)
    assert granted(NAMESPACE) == expected
    assert granted("default") == set()


def test_the_cluster_runs_the_pinned_versions():
    kind_pins = _kind.env_file(_kind.REPO / "infra" / "kind" / "versions.env")
    kueue_pins = _kind.env_file(_kind.REPO / "infra" / "kueue" / "kueue.env")
    server = json.loads(kubectl("version", "-o", "json").stdout)["serverVersion"]["gitVersion"]
    assert kind_pins["KIND_NODE_IMAGE"].startswith(f"kindest/node:{server}@")
    image = kubectl("-n", "kueue-system", "get", "deploy", "kueue-controller-manager", "-o",
                    "jsonpath={.spec.template.spec.containers[0].image}").stdout
    assert image.endswith(f":{kueue_pins['KUEUE_VERSION']}"), image
    live = kget("configmap", "kueue-manager-config", namespace="kueue-system")
    shipped = (_kind.REPO / "infra" / "kueue" / "config" / "kind.yaml").read_text()
    assert live["data"]["controller_manager_config.yaml"] == shipped


def test_kueue_serves_the_executors_api_version():
    group = _kind.api().get("/apis/kueue.x-k8s.io")  # discovery, as the pipeline
    assert group["preferredVersion"]["version"] == KUEUE_API_VERSION
    assert [v["version"] for v in group["versions"]] == [KUEUE_API_VERSION]


def test_tls_rejects_an_untrusted_ca():
    bundle = "/etc/ssl/certs/ca-certificates.crt"
    if not os.path.exists(bundle):
        pytest.fail(f"{bundle} is missing: this test needs a CA bundle without kind's CA")
    with pytest.raises(ExecutorUnavailable):
        _kind.executor(PROCESS_K8S_CA_FILE=bundle).probe()
    _kind.executor().probe()  # K5-R1's TLS with the cluster CA


def test_a_rotated_token_is_used_and_a_bad_one_is_unavailable(tmp_path):
    token = tmp_path / "token"
    shutil.copyfile(os.environ["PROCESS_K8S_TOKEN_FILE"], token)
    ex = _kind.executor(PROCESS_K8S_TOKEN_FILE=str(token))
    ex.probe()
    fresh = kubectl("-n", _kind.PIPELINE_NAMESPACE, "create", "token", _kind.PIPELINE_SA,
                    "--duration=10m").stdout.strip()
    token.write_text(fresh)
    ex.probe()  # the SAME executor reads the file per request (C3)
    token.write_text("not-a-token")
    kx.reset_isolation_cache()
    with pytest.raises(ExecutorUnavailable) as err:
        ex.probe()
    assert isinstance(err.value, KubeApiError) and err.value.status == 401, err.value


def test_a_broken_api_url_raises_rather_than_listing_nothing():
    with pytest.raises(ExecutorUnavailable):
        _kind.executor(PROCESS_K8S_API_URL="https://127.0.0.1:1").list_launched()


def test_without_the_default_deny_the_executor_starts_nothing():
    """K-5 addendum A18 on a real server: the Role's `networkpolicies get`
    reads the policy, and without it no run starts."""
    ex = _kind.executor()
    ex.probe()
    try:
        kubectl("-n", NAMESPACE, "delete", "networkpolicy", DEFAULT_DENY_POLICY)
        kx.reset_isolation_cache()
        with pytest.raises(ExecutorUnavailable) as err:
            ex.probe()
        assert DEFAULT_DENY_POLICY in str(err.value)
        with pytest.raises(ExecutorUnavailable):
            ex.submit(code_spec("pass"))
        assert (kget("jobs") or {}).get("items") == []
    finally:
        kubectl("apply", "-f", str(_kind.DEFAULT_DENY_FILE))
        kx.reset_isolation_cache()
    ex.probe()


# -- Task 6: the lifecycle ------------------------------------------------------


def _in_order(seen: list[tuple[str, str | None]]) -> bool:
    ranks = [ORDER.index(phase) for phase, _ in seen if phase in ORDER]
    return ranks == sorted(ranks) and all(phase in ORDER for phase, _ in seen)


def test_a_run_succeeds_end_to_end_and_is_reaped():
    ex = _kind.executor()
    code = (f"import os, time\nprint({MARKER!r}, os.environ['K6_GREETING'], flush=True)\n"
            "time.sleep(2)\n")
    spec = code_spec(code, env={"K6_GREETING": GREETING})
    handle = ex.submit(spec)
    with _kind.reaping(ex, handle):
        job = _kind.job_of(handle)
        labels = job["metadata"]["labels"]
        assert labels["kueue.x-k8s.io/queue-name"] == "process-runs"
        assert labels["kueue.x-k8s.io/priority-class"] == "triggered"
        dumped = json.dumps(job)
        assert GREETING not in dumped  # C17: the env is in the Secret only
        assert spec.env["STAC_HIGHER_PROCESS_CODE_B64"] not in dumped
        owner, immutable = kubectl(
            "get", "secret", handle.id, "-n", NAMESPACE, "-o",
            "jsonpath={.metadata.ownerReferences[0].uid},{.immutable}").stdout.split(",")
        assert owner == job["metadata"]["uid"] and immutable == "true"
        _kind.wait_for(lambda: _kind.workload_of(handle), lambda w: w is not None,
                       what="Kueue's Workload for the Job", timeout=30)

        seen: list[tuple[str, str | None]] = []
        state = _kind.wait_state(ex, handle, {"exited", "lost"}, seen=seen)
        observe("success", seen=seen)
        assert state.phase == "exited" and state.exit == ExitStatus(0), (state, seen)
        assert _in_order(seen), f"phases out of order: {seen}"

        # The env reached the pod through the Secret, and the platform image
        # ran under PSA restricted as uid 10001 on a read-only root (C16).
        assert f"{MARKER} {GREETING}".encode() in ex.logs(handle, 65536)

        [launched] = [r for r in ex.list_launched() if r.handle.id == handle.id]
        assert launched.run_id == spec.run_id and launched.kind == "process"
        assert abs(dt.datetime.now(dt.UTC) - launched.created_at) < dt.timedelta(minutes=5)

        ex.reap(handle)
        _kind.wait_for(
            lambda: (_kind.job_of(handle), kget("secret", handle.id), _kind.pod_of(handle)),
            lambda objs: objs == (None, None, None), what="the reaped run gone", timeout=60)
        ex.reap(handle)  # idempotent
        assert ex.status(handle).phase == "lost"


def test_a_failing_run_exits_with_its_code():
    ex = _kind.executor()
    handle = ex.submit(code_spec("raise SystemExit(3)"))
    with _kind.reaping(ex, handle):
        state = _kind.wait_state(ex, handle, {"exited", "lost"})
        assert state.phase == "exited", state
        assert state.exit.exit_code == 3 and state.exit.timed_out is False, state


def test_an_oom_kill_names_the_memory_limit():
    ex = _kind.executor()
    code = "b = b'x' * (512 * 1024 * 1024)\nprint(len(b))\n"
    handle = ex.submit(code_spec(code, memory_mb=128))
    with _kind.reaping(ex, handle):
        state = _kind.wait_state(ex, handle, {"exited", "lost"})
        assert state.phase == "exited", state
        assert state.exit.error == oom_error(128), state
        assert state.exit.exit_code == 137, state


# -- stopping a run: timeout and cancel (K-5 hand-off items 3 and 4) ----------


def _stop_record(ex, handle: RunHandle, scenario: str, **extra) -> dict:
    """What a stopped run looks like: never raises (K6-R22)."""
    record: dict = {"handle": handle.id, **extra}
    seen: list[tuple[str, str | None]] = []
    try:
        state = _kind.wait_state(ex, handle, {"exited", "lost"}, seen=seen, timeout=120)
        record["state"] = state
    except AssertionError as err:
        record["state"] = None
        record["wait_error"] = str(err)
    record["seen"] = seen
    pod = _kind.pod_of(handle)
    record["pod_present"] = pod is not None
    record["pod_reason"] = ((pod or {}).get("status") or {}).get("reason")
    record["pod_phase"] = ((pod or {}).get("status") or {}).get("phase")
    record["pod_conditions"] = _kind.condition_summary(pod)
    record["job_conditions"] = _kind.condition_summary(_kind.job_of(handle))
    record["log"] = ex.logs(handle, 65536)
    record["diagnosis"] = (_kind.diagnose_log_loss(handle)
                           if MARKER.encode() not in record["log"] else None)
    observe(scenario, **{k: v for k, v in record.items() if k != "log"},
            log_has_marker=MARKER.encode() in record["log"])
    return record


@pytest.fixture(scope="module")
def timeout_run() -> dict:
    """A run past its 20 s timeout, killed by the pod's activeDeadlineSeconds.
    The deadline counts from the pod's startTime (before the container starts,
    I-180), so 20 s leaves the container time to print its marker first."""
    _kind.clean_namespace()
    ex = _kind.executor()
    handle = ex.submit(code_spec(printing(120), timeout_seconds=20))
    try:
        return _stop_record(ex, handle, "timeout")
    finally:
        ex.reap(handle)
        _kind.clean_namespace()


@pytest.fixture(scope="module")
def cancelled_run() -> dict:
    """A running run cancelled by the executor (deadline lowered to 1 s)."""
    _kind.clean_namespace()
    ex = _kind.executor()
    handle = ex.submit(code_spec(printing(600), timeout_seconds=600))
    extra: dict = {}
    try:
        try:
            _kind.wait_state(ex, handle, {"running"}, timeout=120)
            _kind.wait_log(ex, handle, MARKER.encode())
            extra["marker_before_stop"] = True
        except AssertionError as err:
            extra["marker_before_stop"] = False
            extra["before_error"] = str(err)
        try:
            ex.cancel(handle)
            extra["cancel_error"] = None
        except Exception as err:  # recorded; a test asserts it
            extra["cancel_error"] = repr(err)
        pod = _kind.pod_of(handle) or {}
        extra["pod_deadline_after_cancel"] = (pod.get("spec") or {}).get("activeDeadlineSeconds")
        extra["job_kept"] = _kind.job_of(handle) is not None
        record = _stop_record(ex, handle, "cancel", **extra)
        try:
            ex.cancel(handle)
            record["second_cancel_error"] = None
        except Exception as err:
            record["second_cancel_error"] = repr(err)
        return record
    finally:
        ex.reap(handle)
        _kind.clean_namespace()


def _not_a_disruption(record: dict, what: str) -> None:
    """K-5 hand-off item 3: a deadline kill must not read as a disruption."""
    disruption = record["pod_conditions"].get("DisruptionTarget", "")
    state = record["state"]
    if disruption.startswith("True"):
        pytest.fail(
            f"{what}: OUTCOME 'deadline kill is a disruption' — the pod carries "
            f"DisruptionTarget={disruption}, so the Job's podFailurePolicy fails it as "
            f"PodFailurePolicy and status() reads lost: every {what} becomes a free, endless "
            "requeue. A fix/ PR must stop mapping this case to lost (K5-R4/R5). "
            f"Job={record['job_conditions']}, seen={record['seen']}")
    assert state is not None, f"{what}: never stopped: {record.get('wait_error')}"
    assert state.phase == "exited", (
        f"{what}: OUTCOME 'stopped run reads {state.phase}' ({state.detail}); expected exited. "
        f"pod={record['pod_phase']}/{record['pod_reason']} {record['pod_conditions']}, "
        f"Job={record['job_conditions']}")


def test_a_run_past_its_timeout_is_killed_as_timed_out(timeout_run):
    _not_a_disruption(timeout_run, "timeout")
    state = timeout_run["state"]
    assert state.exit.timed_out is True, (state, timeout_run["pod_reason"])
    assert timeout_run["pod_reason"] == "DeadlineExceeded", timeout_run


def test_a_timed_out_runs_log_survives(timeout_run):
    """K-5 hand-off item 4 (K6-R12, I-178)."""
    assert MARKER.encode() in timeout_run["log"], timeout_run["diagnosis"]


def test_cancel_lowers_the_deadline_and_keeps_the_job(cancelled_run):
    assert cancelled_run["marker_before_stop"], cancelled_run.get("before_error")
    assert cancelled_run["cancel_error"] is None, (
        f"cancel() failed — if this is a 422, the pod-update admission policy refused the "
        f"executor's own patch: {cancelled_run['cancel_error']}")
    assert cancelled_run["pod_deadline_after_cancel"] == kx.CANCEL_POD_DEADLINE_SECONDS
    assert cancelled_run["job_kept"] is True  # C8: kept for logs, reaped later
    assert cancelled_run["second_cancel_error"] is None  # idempotent


def test_a_cancelled_pod_is_not_a_disruption(cancelled_run):
    _not_a_disruption(cancelled_run, "cancel")


def test_a_cancelled_runs_log_survives(cancelled_run):
    """K-5 hand-off item 4 (K6-R12, I-178)."""
    assert MARKER.encode() in cancelled_run["log"], cancelled_run["diagnosis"]


def test_cancel_of_a_queued_run_deletes_it():
    ex = _kind.executor()
    a = ex.submit(code_spec(printing(60)))
    b = ex.submit(code_spec("pass"))
    with _kind.reaping(ex, a, b):
        _kind.wait_state(ex, a, {"running"})
        _kind.wait_state(ex, b, {"pending_capacity"}, timeout=60)
        ex.cancel(b)
        _kind.wait_for(lambda: (_kind.job_of(b), _kind.workload_of(b)),
                       lambda objs: objs == (None, None),
                       what="the queued run's Job and Workload gone", timeout=60)
        assert ex.status(b).phase == "lost"
        assert ex.status(a).phase == "running"  # cancel touched only B


def test_a_job_deleted_behind_the_executors_back_is_lost():
    """K-5 hand-off item 8: a foreground delete leaves deletionTimestamp for a
    while (lost, JOB_DELETING_DETAIL), then the Job is gone (JOB_GONE_DETAIL)."""
    ex = _kind.executor()
    handle = ex.submit(code_spec(printing(120)))
    with _kind.reaping(ex, handle):
        _kind.wait_state(ex, handle, {"running"})
        kubectl("delete", "job", handle.id, "-n", NAMESPACE, "--cascade=foreground",
                "--wait=false")
        seen: list = []
        job_conditions: list = []

        def look():
            job = _kind.job_of(handle)
            summary = (_kind.condition_summary(job), bool(job and job["metadata"].get(
                "deletionTimestamp"))) if job else None
            if summary is not None and (not job_conditions or job_conditions[-1] != summary):
                job_conditions.append(summary)
            state = ex.status(handle)
            if (state.phase, state.detail) not in seen:
                seen.append((state.phase, state.detail))
            return state

        _kind.wait_for(look, lambda s: s.detail == kx.JOB_GONE_DETAIL,
                       what="the deleted Job gone", timeout=90)
        observe("foreground-delete", seen=seen, job_conditions=job_conditions,
                deleting_seen=(("lost", kx.JOB_DELETING_DETAIL) in seen))
        failed = [c for c, _deleting in job_conditions if c.get("Failed", "").startswith("True")]
        assert set(seen) <= {("lost", kx.JOB_DELETING_DETAIL), ("lost", kx.JOB_GONE_DETAIL)}, (
            "OUTCOME: after the delete request returned, status() read something other than "
            f"lost: {seen}. "
            + (f"The Job controller marked the Job Failed during foreground deletion "
               f"({failed[0]['Failed']}), and status() reads Failed before deletionTimestamp: "
               "a deleted Job spends an attempt. A fix/ PR should read deletionTimestamp "
               "first (row 8 before rows 3-7)." if failed else "")
            + f" Job conditions seen: {job_conditions}")
        assert handle.id not in {r.handle.id for r in ex.list_launched()}


def test_a_fresh_submission_reads_waiting_until_kueue_has_its_workload():
    """K-5 hand-off item 8: Kueue creates the Workload asynchronously; until it
    does the run reads as waiting, never as lost or exited."""
    ex = _kind.executor()
    handle = ex.submit(code_spec("pass"))
    with _kind.reaping(ex, handle):
        first = ex.status(handle)
        had_workload = _kind.workload_of(handle) is not None
        observe("fresh-submission", first=first, workload_at_first_read=had_workload)
        assert first.phase in ("pending_capacity", "starting", "running"), first
        state = _kind.wait_state(ex, handle, {"exited", "lost"})
        assert state.phase == "exited" and state.exit.ok, state


def test_pods_log_serves_text_to_the_executors_accept_header():
    """K-5 hand-off item 7: `pods/log` answers the executor's `Accept:
    application/json` with the plain log, not 406."""
    ex = _kind.executor()
    handle = ex.submit(code_spec(printing(1)))
    with _kind.reaping(ex, handle):
        _kind.wait_state(ex, handle, {"exited"})
        pod = _kind.pod_of(handle)["metadata"]["name"]
        try:
            raw = _kind.api().get_raw(
                f"/api/v1/namespaces/{NAMESPACE}/pods/{pod}/log",
                query={"container": CONTAINER, "limitBytes": "65536", "timestamps": "false"},
                max_bytes=65536)
        except KubeApiError as err:
            pytest.fail(f"pods/log refused the executor's request ({err.status} {err.reason}): "
                        "KubeApi.get_raw must send an Accept the log endpoint serves")
        assert MARKER.encode() in raw, raw[:200]


# -- watch (K-5 hand-off item 9; a real 410 is not provoked: K6-R13) ---------


async def _consume(ex, events: list):
    async for event in ex.watch():
        events.append(event)


async def _until(predicate, task: asyncio.Task, *, what: str, timeout: float = 60):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if task.done():
            raise AssertionError(f"{what}: the watch ended: {task.exception()!r}")
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"{what}: not within {timeout}s")
        await asyncio.sleep(0.5)


async def _watch_then_submit(ex, *, pause: float = 0.0):
    events: list = []
    task = asyncio.create_task(_consume(ex, events))
    handle = None
    try:
        await _until(lambda: events, task, what="the first event", timeout=15)
        assert events[0] == RESYNC
        if pause:
            await asyncio.sleep(pause)
            assert not task.done(), f"the watch ended on a server close: {task.exception()!r}"
        spec = code_spec("pass")
        handle = await asyncio.to_thread(ex.submit, spec)
        mine = RunEvent(spec.run_id, handle.id, "process")
        await _until(lambda: events.count(mine) >= 2, task,
                     what="two events for the submitted run")
        assert events.count(RESYNC) == 1, events
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        if handle is not None:
            await asyncio.to_thread(ex.reap, handle)


async def test_watch_resyncs_then_delivers_events():
    await _watch_then_submit(_kind.executor())


async def test_watch_resumes_after_the_server_closes_it(monkeypatch):
    """The server ends each stream after 3 s; watch() resumes from its last
    resourceVersion without raising (K5-R21) and still delivers."""
    monkeypatch.setattr(kx, "WATCH_TIMEOUT_SECONDS", 3)
    await _watch_then_submit(_kind.executor(), pause=8)


# -- Pod Security --------------------------------------------------------------


def test_pod_security_restricted_is_enforced_and_admits_the_run():
    namespace = kget("namespace", NAMESPACE, namespace=None)
    assert namespace["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted"
    privileged = {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": "psa-probe", "namespace": NAMESPACE},
        "spec": {"containers": [{"name": "c", "image": os.environ["KIND_RUNTIME_IMAGE"],
                                 "securityContext": {"privileged": True}}]},
    }
    done = kubectl("create", "--dry-run=server", "-f", "-", input=json.dumps(privileged),
                   check=False)
    # ...so the runs above, all admitted, prove K-5's pod complies.
    assert done.returncode != 0 and "violates PodSecurity" in done.stderr, done.stderr
