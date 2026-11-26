"""K-5 (plan Task 5): KubernetesExecutor submit, cancel, logs, reap,
list_launched and probe, against the RBAC-enforcing fake API server."""

from __future__ import annotations

import datetime as dt
import json
import logging

import pytest

from _fake_kube import FakeKube
from _kube_executor_fixtures import DIGEST, NAME, NOW, RUN, SCAN, spec
from pipeline.process.executor import (
    ExecutorUnavailable,
    HardwareUnavailable,
    ImagePullFailed,
    RegistryAuth,
    RunHandle,
    RunRejected,
)
from pipeline.process.kube_api import KubeApi, KubeConfig
from pipeline.process.kubernetes_executor import (
    ISOLATION_CHECK_TTL_SECONDS,
    KubernetesExecutor,
    reset_isolation_cache,
)

JOBS = ("batch", "jobs")
SECRETS = ("", "secrets")
WORKLOADS = ("kueue.x-k8s.io", "workloads")
HANDLE = RunHandle(NAME, "kubernetes")


def _calls(fake):
    return [(c.method, c.resource) for c in fake.calls]


@pytest.fixture(autouse=True)
def _fresh_isolation_cache():
    reset_isolation_cache()
    yield
    reset_isolation_cache()


# -- submit -------------------------------------------------------------------


def test_submit_creates_the_job_then_its_owned_secret(executor, fake):
    handle = executor.submit(spec())
    assert handle == RunHandle(NAME, "kubernetes")
    assert _calls(fake) == [
        ("GET", "networkpolicies"), ("POST", "jobs"), ("POST", "secrets"),
    ]
    job = fake.objects(*JOBS)[NAME]
    secret = fake.objects(*SECRETS)[NAME]
    assert secret["metadata"]["ownerReferences"][0]["uid"] == job["metadata"]["uid"]
    assert job["spec"]["suspend"] is True
    assert len(fake.objects(*WORKLOADS)) == 1


def test_submit_with_a_registry_credential_creates_the_pull_secret(executor, fake):
    auth = RegistryAuth(username="u", password="p-SENTINEL", server="ghcr.io")
    executor.submit(spec(image=f"ghcr.io/acme/private@{DIGEST}", user_image=True,
                         registry_auth=auth))
    assert [c for c in _calls(fake) if c[0] == "POST"] == [
        ("POST", "jobs"), ("POST", "secrets"), ("POST", "secrets"),
    ]
    pull = fake.objects(*SECRETS)[f"{NAME}-pull"]
    assert pull["type"] == "kubernetes.io/dockerconfigjson"


def test_a_user_image_not_pinned_by_digest_is_refused_before_any_call(executor, fake):
    with pytest.raises(ImagePullFailed, match="not pinned by digest"):
        executor.submit(spec(image="docker.io/library/python:3.12", user_image=True))
    assert fake.calls == []


def test_a_gpu_run_without_an_extended_resource_creates_nothing(executor, fake):
    with pytest.raises(HardwareUnavailable, match="'standard'"):
        executor.submit(spec(gpu_count=1))
    assert fake.calls == []


def test_an_oversized_environment_is_rejected_before_any_call(executor, fake):
    big = "x" * (2 * 1024 * 1024)
    with pytest.raises(RunRejected) as err:
        executor.submit(spec(env={"BIG": big}))
    assert "1 MiB" in str(err.value) and "xxxx" not in str(err.value)
    assert isinstance(err.value, HardwareUnavailable)
    assert fake.calls == []


def test_a_rejected_job_is_a_dead_run(executor, fake):
    fake.fail_next("POST", "jobs", 422, reason="Invalid",
                   message="spec.template.spec.containers[0].image: Invalid value")
    with pytest.raises(RunRejected) as err:
        executor.submit(spec())
    assert isinstance(err.value, HardwareUnavailable)
    assert RUN in str(err.value) and "Invalid value" in str(err.value)
    assert fake.objects(*JOBS) == {}


@pytest.mark.parametrize("code", [403, 404, 409, 500])
def test_a_forbidden_or_failing_create_is_unavailable(executor, fake, code):
    fake.fail_next("POST", "jobs", code)
    with pytest.raises(ExecutorUnavailable) as err:
        executor.submit(spec())
    assert not isinstance(err.value, RunRejected)


def test_a_failed_secret_create_removes_the_job(executor, fake):
    fake.fail_next("POST", "secrets", 500)
    with pytest.raises(ExecutorUnavailable) as err:
        executor.submit(spec())
    assert not isinstance(err.value, RunRejected)
    assert fake.objects(*JOBS) == {} and fake.objects(*WORKLOADS) == {}
    assert fake.calls[-1].method == "DELETE"
    assert fake.calls[-1].query == {"propagationPolicy": "Foreground"}

    fake.fail_next("POST", "secrets", 422)
    with pytest.raises(RunRejected, match="environment Secret"):
        executor.submit(spec())
    assert fake.objects(*JOBS) == {}


def test_a_failed_pull_secret_create_removes_the_job_and_its_env_secret(executor, fake):
    auth = RegistryAuth(username="u", password="p", server="ghcr.io")
    calls = 0

    real_create = executor.api.create

    def create(path, body, *, redact=False):
        nonlocal calls
        calls += 1
        if calls == 3:
            fake.fail_next("POST", "secrets", 500)
        return real_create(path, body, redact=redact)

    executor.api.create = create
    with pytest.raises(ExecutorUnavailable):
        executor.submit(spec(image=f"ghcr.io/acme/private@{DIGEST}", user_image=True,
                             registry_auth=auth))
    assert fake.objects(*JOBS) == {} and fake.objects(*SECRETS) == {}


def test_secret_values_never_reach_errors_or_logs(executor, fake, caplog):
    caplog.set_level(logging.DEBUG)
    fake.fail_next("POST", "secrets", 422, message="data[API_TOKEN]: tok-SENTINEL is invalid")
    with pytest.raises(RunRejected) as err:
        executor.submit(spec())
    assert "SENTINEL" not in str(err.value)
    assert "SENTINEL" not in repr(err.value.__cause__) and err.value.__suppress_context__
    fake.fail_next("POST", "jobs", 500, message="boom")
    with pytest.raises(ExecutorUnavailable) as err:
        executor.submit(spec())
    assert "SENTINEL" not in str(err.value)
    executor.submit(spec())
    assert "SENTINEL" not in json.dumps(fake.objects(*JOBS))
    assert "SENTINEL" not in caplog.text
    assert "SENTINEL" not in repr(executor)


# -- cancel -------------------------------------------------------------------


def _admitted(fake, *, started=True, phase="Running", state=None):
    fake.set_job_status(NAME, suspend=False, active=1)
    return fake.add_pod(
        NAME, phase=phase, start_time=NOW if started else None,
        state=state if state is not None else ({"running": {}} if started else
                                                {"waiting": {"reason": "ContainerCreating"}}),
    )


def test_cancel_of_a_running_pod_sets_its_deadline_and_keeps_it(executor, fake):
    executor.submit(spec())
    pod = _admitted(fake)
    fake.calls.clear()
    executor.cancel(HANDLE)
    patches = [c for c in fake.calls if c.method == "PATCH"]
    assert len(patches) == 1
    assert patches[0].path.endswith(f"/pods/{pod}")
    assert patches[0].body == {"spec": {"activeDeadlineSeconds": 1}}
    assert not [c for c in fake.calls if c.method == "DELETE"]
    assert NAME in fake.objects(*JOBS) and NAME in fake.objects(*SECRETS)
    assert fake.objects("", "pods")[pod]["spec"]["activeDeadlineSeconds"] == 1


def test_cancel_of_a_suspended_job_deletes_it(executor, fake):
    executor.submit(spec())
    fake.calls.clear()
    executor.cancel(HANDLE)
    deletes = [c for c in fake.calls if c.method == "DELETE"]
    assert len(deletes) == 1 and deletes[0].resource == "jobs"
    assert deletes[0].query == {"propagationPolicy": "Foreground"}
    assert fake.objects(*JOBS) == {} and fake.objects(*SECRETS) == {}
    assert fake.objects(*WORKLOADS) == {}


def test_cancel_of_an_admitted_job_whose_pod_never_started_deletes_it(executor, fake):
    executor.submit(spec())
    _admitted(fake, started=False, phase="Pending")
    executor.cancel(HANDLE)
    assert fake.objects(*JOBS) == {} and fake.objects("", "pods") == {}


def test_cancel_is_idempotent(executor, fake):
    executor.cancel(HANDLE)  # no Job at all
    assert [(c.method, c.verb) for c in fake.calls] == [("GET", "get")]
    executor.submit(spec())
    for condition in ("Complete", "Failed"):
        fake.set_job_status(NAME, conditions=[{"type": condition, "status": "True"}])
        fake.add_pod(NAME, phase="Running", start_time=NOW, state={"running": {}})
        fake.calls.clear()
        executor.cancel(HANDLE)
        assert not [c for c in fake.calls if c.method in ("PATCH", "DELETE")]


def test_cancel_raises_when_the_api_is_down(executor, fake):
    fake.fail_next("GET", "jobs", 500)
    with pytest.raises(ExecutorUnavailable):
        executor.cancel(HANDLE)
    executor.submit(spec())
    _admitted(fake)
    fake.fail_next("GET", "pods", 500)
    with pytest.raises(ExecutorUnavailable):
        executor.cancel(HANDLE)


def test_cancel_tolerates_a_pod_that_vanished_meanwhile(executor, fake):
    executor.submit(spec())
    _admitted(fake)
    fake.fail_next("PATCH", "pods", 404)
    executor.cancel(HANDLE)  # no raise


# -- logs ---------------------------------------------------------------------


def test_logs_of_a_failed_pod_are_served(executor, fake):
    executor.submit(spec())
    fake.add_pod(NAME, phase="Failed", state={"terminated": {"exitCode": 1}},
                 log=b"hello\n" * 10)
    assert executor.logs(HANDLE, 1000) == b"hello\n" * 10
    log_call = [c for c in fake.calls if c.resource == "pods/log"][-1]
    assert log_call.query == {"container": "run", "limitBytes": "1000", "timestamps": "false"}
    assert executor.logs(HANDLE, 8) == b"hello\nhe"


def test_logs_pick_the_newest_pod(executor, fake):
    executor.submit(spec())
    fake.add_pod(NAME, phase="Failed", state={"terminated": {"exitCode": 1}}, log=b"old",
                 created=NOW + dt.timedelta(minutes=5))
    fake.add_pod(NAME, phase="Running", state={"running": {}}, log=b"new",
                 created=NOW + dt.timedelta(minutes=9))
    fake.add_pod(NAME, phase="Failed", state={"terminated": {"exitCode": 1}}, log=b"older",
                 created=NOW)
    assert executor.logs(HANDLE, 100) == b"new"


def test_logs_without_a_pod_or_a_started_container_are_empty(executor, fake, caplog):
    executor.submit(spec())
    with caplog.at_level(logging.WARNING):
        assert executor.logs(HANDLE, 100) == b""
    assert [r.getMessage() for r in caplog.records] == ["process run logs unavailable"]
    assert caplog.records[0].run == NAME
    caplog.clear()
    fake.add_pod(NAME, phase="Pending", state={"waiting": {"reason": "ErrImagePull"}})
    with caplog.at_level(logging.WARNING):
        assert executor.logs(HANDLE, 100) == b""
    assert [r.getMessage() for r in caplog.records] == ["process run logs unavailable"]
    caplog.clear()
    fake.fail_next("GET", "pods", 500)
    with caplog.at_level(logging.WARNING):
        assert executor.logs(HANDLE, 100) == b""
    assert len(caplog.records) == 1


# -- reap, list, probe ----------------------------------------------------------


def test_reap_deletes_the_job_foreground_and_its_secrets(executor, fake):
    auth = RegistryAuth(username="u", password="p", server="ghcr.io")
    executor.submit(spec(image=f"ghcr.io/acme/private@{DIGEST}", user_image=True,
                         registry_auth=auth))
    fake.add_pod(NAME, phase="Failed", state={"terminated": {"exitCode": 0}})
    fake.calls.clear()
    executor.reap(HANDLE)
    deletes = [(c.resource, c.path.rsplit("/", 1)[1], c.query) for c in fake.calls
               if c.method == "DELETE"]
    assert deletes == [
        ("jobs", NAME, {"propagationPolicy": "Foreground"}),
        ("secrets", NAME, {}),
        ("secrets", f"{NAME}-pull", {}),
    ]
    for key in (JOBS, SECRETS, WORKLOADS, ("", "pods")):
        assert fake.objects(*key) == {}


def test_reap_is_idempotent_and_never_raises(executor, fake, caplog):
    executor.submit(spec())
    executor.reap(HANDLE)
    with caplog.at_level(logging.WARNING):
        executor.reap(HANDLE)
    assert caplog.records == []
    executor.submit(spec())
    fake.fail_next("DELETE", "jobs", 500)
    with caplog.at_level(logging.WARNING):
        executor.reap(HANDLE)
    assert [r.getMessage() for r in caplog.records] == ["process run reap failed"]
    assert caplog.records[0].run == NAME
    # The Secret delete still ran after the Job delete failed.
    assert NAME not in fake.objects(*SECRETS)


def test_list_launched_reads_labels_and_creation_times(executor, fake):
    executor.submit(spec())
    scanner = KubernetesExecutor(api=executor.api, _suffix=lambda: "ffffff")
    scanner.submit(spec(kind="image_scan", run_id=SCAN, process_id="img", profile=None,
                        image="stac-higher-image-scanner:local"))
    fake.objects(*JOBS)["stranger"] = {
        "metadata": {"name": "stranger", "labels": {}, "creationTimestamp": "x"}
    }
    launched = sorted(executor.list_launched(), key=lambda e: e.kind)
    assert [(e.handle, e.run_id, e.kind) for e in launched] == [
        (RunHandle(f"stac-scan-{SCAN}-ffffff", "kubernetes"), SCAN, "image_scan"),
        (RunHandle(NAME, "kubernetes"), RUN, "process"),
    ]
    created = fake.objects(*JOBS)[NAME]["metadata"]["creationTimestamp"]
    process = launched[1]
    assert process.created_at == dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
    assert process.created_at.tzinfo is not None


@pytest.mark.parametrize("code", [403, 500])
def test_list_launched_raises_on_any_api_error(executor, fake, code):
    fake.fail_next("GET", "jobs", code)
    with pytest.raises(ExecutorUnavailable):
        executor.list_launched()


def test_probe_lists_at_most_one_job_and_checks_isolation(executor, fake):
    executor.probe()
    assert fake.calls[0].verb == "list"
    assert fake.calls[0].query == {"labelSelector": "stac-higher.run-id", "limit": "1"}
    assert fake.calls[1].resource == "networkpolicies" and fake.calls[1].verb == "get"
    fake.fail_next("GET", "jobs", 403)
    with pytest.raises(ExecutorUnavailable):
        executor.probe()


# -- network isolation fails closed (lead security ruling, addendum A18) --------


def _bare_executor(bare, token_file):
    token_file.write_text(bare.token)
    return KubernetesExecutor(api=KubeApi(KubeConfig(bare.url, token_file, None)),
                              _suffix=lambda: "a1b2c3")


def test_no_run_starts_without_the_default_deny_policy(token_file):
    with FakeKube(default_deny=False) as bare:
        ex = _bare_executor(bare, token_file)
        with pytest.raises(ExecutorUnavailable) as err:
            ex.submit(spec())
        assert not isinstance(err.value, RunRejected)  # requeue, no attempt spent
        assert "stac-higher-runs" in str(err.value)
        assert "stac-higher-runs-default-deny" in str(err.value)
        assert not [c for c in bare.calls if c.method == "POST"]
        with pytest.raises(ExecutorUnavailable, match="default-deny"):
            ex.probe()


@pytest.mark.parametrize(
    ("policy_spec", "why"),
    [
        ({"podSelector": {"matchLabels": {"app": "x"}}, "policyTypes": ["Ingress", "Egress"]},
         "podSelector: {}"),
        ({"podSelector": {"matchExpressions": [
            {"key": "stac-higher.run-kind", "operator": "Exists"}]},
          "policyTypes": ["Ingress", "Egress"]}, "podSelector: {}"),
        ({"podSelector": {}, "policyTypes": ["Ingress"]}, "Ingress and Egress"),
        ({"podSelector": {}, "policyTypes": ["Ingress", "Egress"], "egress": [{}]},
         "allows traffic"),
    ],
)
def test_a_policy_that_does_not_isolate_is_refused(executor, fake, policy_spec, why):
    fake.objects("networking.k8s.io", "networkpolicies")[
        "stac-higher-runs-default-deny"]["spec"] = policy_spec
    with pytest.raises(ExecutorUnavailable, match=why):
        executor.submit(spec())
    assert fake.objects(*JOBS) == {}


def test_a_passed_isolation_check_is_cached_briefly(executor, fake):
    ticks = iter([0.0, 1.0, ISOLATION_CHECK_TTL_SECONDS + 1.0, ISOLATION_CHECK_TTL_SECONDS + 2])
    executor._monotonic = lambda: next(ticks)
    executor._suffix = iter(["000001", "000002", "000003", "000004"]).__next__
    executor.submit(spec())
    executor.submit(spec())
    policies = [c for c in fake.calls if c.resource == "networkpolicies"]
    assert len(policies) == 1
    executor.submit(spec())  # past the TTL: read again
    assert len([c for c in fake.calls if c.resource == "networkpolicies"]) == 2
    # A failed check is never cached: the policy disappears, the next run
    # past the TTL is refused, and so is the one after.
    del fake.objects("networking.k8s.io", "networkpolicies")["stac-higher-runs-default-deny"]
    reset_isolation_cache()
    with pytest.raises(ExecutorUnavailable):
        executor.submit(spec())


@pytest.mark.parametrize(
    ("level", "hosts", "why"),
    [
        ("hosts", ("example.com",), "host allow-list cannot be enforced"),
        ("inputs", (), "network level 'inputs'"),
        ("open", (), "network level 'open'"),
    ],
)
def test_a_run_asking_for_network_it_cannot_enforce_is_rejected(executor, fake, level, hosts,
                                                               why):
    with pytest.raises(RunRejected, match=why):
        executor.submit(spec(network_level=level, network_hosts=hosts))
    assert fake.calls == []


def test_run_pods_carry_their_network_level(executor, fake):
    executor.submit(spec())
    template = fake.objects(*JOBS)[NAME]["spec"]["template"]
    assert template["metadata"]["labels"]["stac-higher.network-level"] == "isolated"
    assert template["spec"]["automountServiceAccountToken"] is False


def test_from_settings_reads_the_k8s_settings(tmp_path):
    from pipeline.config import Settings

    settings = Settings.from_env(env={
        "PROCESS_EXECUTOR": "kubernetes",
        "PROCESS_K8S_API_URL": "http://127.0.0.1:1",
        "PROCESS_K8S_TOKEN_FILE": str(tmp_path / "t"),
        "PROCESS_K8S_NAMESPACE": "runs",
        "PROCESS_K8S_DEFAULT_QUEUE": "q",
        "PROCESS_K8S_RUN_SERVICE_ACCOUNT": "sa",
        "PROCESS_K8S_REGISTRY_MIRRORS": '{"ghcr.io": "m.test/g"}',
    })
    ex = KubernetesExecutor.from_settings(settings)
    assert (ex.namespace, ex.default_queue, ex.run_service_account) == ("runs", "q", "sa")
    assert dict(ex.registry_mirrors) == {"ghcr.io": "m.test/g"}
    assert ex.name == "kubernetes"
    assert ex.submitted_phase == "pending_capacity"
    assert ex.enforces_local_capacity is False



# -- review C1: an orphaned Job from a failed submit -----------------------------


def test_a_job_create_whose_answer_was_lost_is_discarded(executor, fake):
    """The POST succeeded server-side but the client saw a timeout: the Job
    (no Secret yet) must not sit in Kueue's queue holding quota."""
    real_create = executor.api.create

    def create(path, body, *, redact=False):
        created = real_create(path, body, redact=redact)
        if path.endswith("/jobs"):
            raise ExecutorUnavailable("kubernetes POST jobs unreachable: timed out")
        return created

    executor.api.create = create
    with pytest.raises(ExecutorUnavailable):
        executor.submit(spec())
    assert fake.objects(*JOBS) == {} and fake.objects(*WORKLOADS) == {}


@pytest.mark.parametrize("code", [500, 504])
def test_a_failed_job_create_tries_to_remove_what_it_may_have_created(executor, fake, code):
    fake.fail_next("POST", "jobs", code)
    with pytest.raises(ExecutorUnavailable):
        executor.submit(spec())
    deletes = [c for c in fake.calls if c.method == "DELETE"]
    assert len(deletes) == 1 and deletes[0].path.endswith(f"/jobs/{NAME}")


@pytest.mark.parametrize("code", [409, 422])
def test_a_conflict_or_rejection_deletes_nothing(executor, fake, code):
    """409: the name belongs to an existing Job we must not touch; 422:
    nothing was created."""
    fake.fail_next("POST", "jobs", code)
    with pytest.raises((ExecutorUnavailable, RunRejected)):
        executor.submit(spec())
    assert not [c for c in fake.calls if c.method == "DELETE"]


# -- review C6, C10, C12 ---------------------------------------------------------


def test_cancel_keeps_a_finished_pod_whose_job_has_not_noticed_yet(executor, fake):
    """Review C6: the pod ran and finished; deleting the Job now would lose
    its log. Nothing to stop: leave it for the Job controller and reap."""
    executor.submit(spec())
    fake.set_job_status(NAME, suspend=False, active=0)
    fake.add_pod(NAME, phase="Failed", start_time=NOW,
                 state={"terminated": {"exitCode": 1}})
    fake.calls.clear()
    executor.cancel(HANDLE)
    assert not [c for c in fake.calls if c.method in ("PATCH", "DELETE")]
    assert NAME in fake.objects(*JOBS)


def test_a_foreground_delete_that_lingers_reads_as_deleting(executor, fake):
    """Review C10: a foreground delete keeps the Job (deletionTimestamp set)
    until its pods are gone; status() reads it as row 8."""
    fake.linger_deletes = True
    executor.submit(spec())
    executor.cancel(HANDLE)  # never started: foreground delete
    assert NAME in fake.objects(*JOBS)
    assert executor.status(HANDLE).detail == "the run's Job is being deleted outside the platform"
    executor.reap(HANDLE)  # a second delete of a deleting Job is fine
    fake.finish_deletes()
    assert fake.objects(*JOBS) == {} and fake.objects(*SECRETS) == {}
    assert executor.status(HANDLE).phase == "lost"


def test_a_workload_created_later_reads_pending_then_its_message(executor, fake):
    """Review C10: Kueue creates the Workload asynchronously (row 9)."""
    fake.kueue_async = True
    executor.submit(spec())
    assert fake.objects(*WORKLOADS) == {}
    state = executor.status(HANDLE)
    assert (state.phase, state.detail) == ("pending_capacity", None)
    fake.create_pending_workloads()
    assert executor.status(HANDLE).detail.startswith("couldn't assign flavors")


def test_the_secret_cap_counts_decoded_bytes(executor, fake):
    """Review C12: the API server caps the decoded data, not its base64."""
    from pipeline.process.kubernetes_executor import SECRET_MAX_BYTES

    # 900 KiB decoded is ~1.2 MiB of base64: allowed
    executor.submit(spec(env={"BIG": "x" * (900 * 1024)}))
    assert NAME in fake.objects(*SECRETS)
    with pytest.raises(RunRejected):
        executor.submit(spec(env={"BIG": "x" * SECRET_MAX_BYTES}))
