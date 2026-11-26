"""K-5 (plan Task 6): KubernetesExecutor.status — the Job, its Kueue Workload
and its newest pod onto K-4's five phases, first matching row wins."""

from __future__ import annotations

import datetime as dt

import pytest

from _kube_executor_fixtures import NAME, NOW, spec
from pipeline.process import kubernetes_executor as kx
from pipeline.process.executor import ExecutorUnavailable, ExitStatus, RunHandle, oom_error

HANDLE = RunHandle(NAME, "kubernetes")
T0 = NOW - dt.timedelta(minutes=10)


def _ts(when: dt.datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def _fresh_isolation_cache():
    kx.reset_isolation_cache()
    yield
    kx.reset_isolation_cache()


@pytest.fixture
def submitted(executor):
    executor.submit(spec())
    return executor


def _admit(fake, **status):
    fake.set_job_status(NAME, suspend=False, active=1, **status)


def _fail(fake, reason="BackoffLimitExceeded", message="Job has reached the specified backoff"):
    fake.set_job_status(NAME, suspend=False, active=0, conditions=[
        {"type": "Failed", "status": "True", "reason": reason, "message": message},
    ])


def _terminated(code, reason="Error", message=None, finished=NOW):
    state = {"exitCode": code, "reason": reason, "finishedAt": _ts(finished)}
    if message:
        state["message"] = message
    return {"terminated": state}


# -- the table ------------------------------------------------------------------


def test_a_deleted_job_is_lost(executor):
    state = executor.status(HANDLE)
    assert (state.phase, state.detail) == ("lost", kx.JOB_GONE_DETAIL)
    assert kx.JOB_GONE_DETAIL == "the run's Job no longer exists"


def test_a_complete_job_exited_zero(submitted, fake):
    fake.set_job_status(NAME, suspend=False, conditions=[
        {"type": "Complete", "status": "True"},
    ])
    fake.calls.clear()
    state = submitted.status(HANDLE)
    assert state.phase == "exited" and state.exit == ExitStatus(0)
    assert state.finished_at is not None
    assert [c.verb for c in fake.calls] == ["get"]  # reads only what it needs


@pytest.mark.parametrize(
    ("pod_kwargs", "job_reason", "reason"),
    [
        ({"conditions": [{"type": "DisruptionTarget", "status": "True",
                          "reason": "DeletionByTaintManager", "message": "node gone"}]},
         "BackoffLimitExceeded", "DeletionByTaintManager"),
        ({"reason": "Evicted", "message": "The node was low on resource: memory."},
         "BackoffLimitExceeded", "Evicted"),
    ],
)
def test_a_disrupted_pod_is_lost_with_its_reason(submitted, fake, pod_kwargs, job_reason,
                                                 reason):
    fake.add_pod(NAME, phase="Failed", start_time=T0, **pod_kwargs)
    _fail(fake, reason=job_reason)
    state = submitted.status(HANDLE)
    assert state.phase == "lost" and state.exit is None
    assert state.detail.startswith(f"{kx.DISRUPTION_EXIT} ({reason})")


def test_a_pod_failure_policy_failure_is_lost_even_without_the_pod(submitted, fake):
    """K-6 A6: the disrupted pod may be deleted before status() reads it."""
    _fail(fake, reason="PodFailurePolicy",
          message="Pod stac-higher-runs/x has condition DisruptionTarget matching FailJob rule")
    state = submitted.status(HANDLE)
    assert state.phase == "lost"
    assert state.detail.startswith(f"{kx.DISRUPTION_EXIT} (PodFailurePolicy): Pod ")


def test_a_pod_past_its_deadline_is_timed_out_with_its_exit_code(submitted, fake):
    fake.add_pod(NAME, phase="Failed", start_time=T0, reason="DeadlineExceeded",
                 message="Pod was active on the node longer than the specified deadline",
                 state=_terminated(143))
    _fail(fake)
    state = submitted.status(HANDLE)
    assert state.phase == "exited"
    assert state.exit == ExitStatus(143, timed_out=True)
    assert state.finished_at == NOW
    # A container killed without an exit code reads as SIGKILL.
    fake.add_pod(NAME, phase="Failed", start_time=T0, reason="DeadlineExceeded")
    assert submitted.status(HANDLE).exit == ExitStatus(137, timed_out=True)


def test_a_job_past_its_deadline_is_timed_out(submitted, fake):
    _fail(fake, reason="DeadlineExceeded", message="Job was active longer than specified")
    state = submitted.status(HANDLE)
    assert state.exit == ExitStatus(137, timed_out=True)


def test_an_oom_kill_names_the_memory_limit(submitted, fake):
    fake.add_pod(NAME, phase="Failed", start_time=T0, state=_terminated(137, "OOMKilled"))
    _fail(fake)
    state = submitted.status(HANDLE)
    assert state.exit == ExitStatus(137, error=oom_error(1024))


def test_a_failed_job_exits_with_the_containers_code(submitted, fake):
    fake.add_pod(NAME, phase="Failed", start_time=T0, state=_terminated(3))
    _fail(fake)
    assert submitted.status(HANDLE).exit == ExitStatus(3)
    fake.add_pod(NAME, phase="Failed", start_time=T0,
                 state=_terminated(128, "ContainerCannotRun", "exec format error"))
    assert submitted.status(HANDLE).exit == ExitStatus(
        128, error="ContainerCannotRun: exec format error"
    )


def test_a_failed_job_without_a_pod_has_no_exit_code(submitted, fake):
    _fail(fake, reason="BackoffLimitExceeded", message="gone")
    state = submitted.status(HANDLE)
    assert state.exit == ExitStatus(-1, error="the run's Job failed (BackoffLimitExceeded): gone")


def test_a_job_being_deleted_is_lost(submitted, fake):
    fake.objects("batch", "jobs")[NAME]["metadata"]["deletionTimestamp"] = _ts(NOW)
    state = submitted.status(HANDLE)
    assert (state.phase, state.detail) == ("lost", kx.JOB_DELETING_DETAIL)


def test_a_just_created_job_is_pending(executor, fake):
    fake.kueue = True
    executor.submit(spec())
    for workload in list(fake.objects("kueue.x-k8s.io", "workloads")):
        del fake.objects("kueue.x-k8s.io", "workloads")[workload]
    assert executor.status(HANDLE).phase == "pending_capacity"
    assert executor.status(HANDLE).detail is None


def test_a_missing_kueue_api_reads_as_no_workload(submitted, fake):
    fake.kueue = False
    state = submitted.status(HANDLE)
    assert (state.phase, state.detail) == ("pending_capacity", None)


def test_a_deactivated_workload_spends_an_attempt(submitted, fake):
    fake.set_workload(NAME, active=False, conditions=[
        {"type": "QuotaReserved", "status": "False", "reason": "WaitingForQuota",
         "message": "waiting"},
        {"type": "Evicted", "status": "True", "reason": "Deactivated",
         "message": "The workload is deactivated due to exceeding the maximum number of "
                    "re-queuing retries"},
    ])
    state = submitted.status(HANDLE)
    assert state.phase == "exited"
    assert state.exit.exit_code == -1
    assert state.exit.error.startswith(kx.DEACTIVATED_PREFIX + "The workload is deactivated")


@pytest.mark.parametrize(
    ("reason", "want"),
    [("Preempted", "preempted: "), ("PodsReadyTimeout", "evicted (PodsReadyTimeout): ")],
)
def test_a_preempted_job_is_pending_capacity_not_lost(submitted, fake, reason, want):
    fake.set_workload(NAME, conditions=[
        {"type": "Evicted", "status": "True", "reason": reason, "message": "by a higher one"},
        {"type": "QuotaReserved", "status": "False", "reason": "WaitingForQuota",
         "message": "waiting"},
    ])
    state = submitted.status(HANDLE)
    assert state.phase == "pending_capacity"
    assert state.detail == f"{want}by a higher one"
    assert kx.PREEMPTED_PREFIX == "preempted: "


@pytest.mark.parametrize(
    "reason", ["WaitingForQuota", "ExceedsMaxQuota", "Misconfigured", "Suspended", "Pending"]
)
def test_quota_pending_carries_kueues_message(submitted, fake, reason):
    """K-6 A1: mapped by the condition's status; the reason is detail only."""
    fake.set_workload(NAME, conditions=[
        {"type": "QuotaReserved", "status": "False", "reason": reason,
         "message": "insufficient quota for cpu in flavor default"},
    ])
    state = submitted.status(HANDLE)
    assert state.phase == "pending_capacity"
    assert state.detail == "insufficient quota for cpu in flavor default"


def test_an_admitted_job_not_yet_unsuspended_is_starting(submitted, fake):
    fake.set_workload(NAME, conditions=[{"type": "QuotaReserved", "status": "True"},
                                        {"type": "Admitted", "status": "True"}])
    assert submitted.status(HANDLE).phase == "starting"


def test_an_unsuspended_job_without_a_pod_is_starting(submitted, fake):
    _admit(fake)
    state = submitted.status(HANDLE)
    assert (state.phase, state.detail) == ("starting", None)


def test_an_unschedulable_pod_is_waiting_for_capacity(submitted, fake):
    _admit(fake)
    fake.add_pod(NAME, phase="Pending", conditions=[{
        "type": "PodScheduled", "status": "False", "reason": "Unschedulable",
        "message": "0/3 nodes are available: 3 Insufficient nvidia.com/gpu.",
    }])
    state = submitted.status(HANDLE)
    assert state.phase == "pending_capacity"
    assert state.detail == (
        f"{kx.WAITING_FOR_NODE_PREFIX}0/3 nodes are available: 3 Insufficient nvidia.com/gpu."
    )
    assert kx.WAITING_FOR_NODE_PREFIX == "waiting for a node: "


@pytest.mark.parametrize("reason", sorted(kx.IMMEDIATE_START_FAILURES))
def test_an_invalid_image_fails_at_once(submitted, fake, reason):
    _admit(fake)
    fake.add_pod(NAME, phase="Pending", start_time=NOW,
                 state={"waiting": {"reason": reason, "message": "bad ref"}})
    state = submitted.status(HANDLE)
    assert state.exit == ExitStatus(-1, error=f"the run could not start ({reason}): bad ref")


@pytest.mark.parametrize("reason", sorted(kx.GRACED_START_FAILURES))
def test_a_pull_failure_is_starting_within_the_grace(submitted, fake, clock, reason):
    _admit(fake)
    fake.add_pod(NAME, phase="Pending", start_time=NOW - dt.timedelta(seconds=299),
                 state={"waiting": {"reason": reason, "message": "manifest unknown"}})
    state = submitted.status(HANDLE)
    assert (state.phase, state.detail) == ("starting", f"{reason}: manifest unknown")


@pytest.mark.parametrize("reason", sorted(kx.GRACED_START_FAILURES))
def test_a_pull_failure_past_the_grace_exits(submitted, fake, reason):
    _admit(fake)
    fake.add_pod(NAME, phase="Pending", start_time=NOW - dt.timedelta(seconds=300),
                 state={"waiting": {"reason": reason, "message": "manifest unknown"}})
    state = submitted.status(HANDLE)
    assert state.exit == ExitStatus(
        -1, error=f"the run could not start ({reason}): manifest unknown"
    )
    assert state.exit.timed_out is False


def test_the_grace_falls_back_to_the_pods_creation_time(submitted, fake):
    _admit(fake)
    fake.add_pod(NAME, phase="Pending", created=NOW - dt.timedelta(seconds=400),
                 state={"waiting": {"reason": "ErrImagePull", "message": "x"}})
    assert submitted.status(HANDLE).phase == "exited"


def test_a_ready_pod_is_running(submitted, fake):
    _admit(fake, ready=1)
    fake.add_pod(NAME, phase="Running", start_time=T0, state={"running": {}})
    assert submitted.status(HANDLE).phase == "running"
    _admit(fake, ready=0)
    assert submitted.status(HANDLE).phase == "running"  # the container runs


def test_a_creating_container_is_starting(submitted, fake):
    _admit(fake)
    fake.add_pod(NAME, phase="Pending", start_time=NOW,
                 state={"waiting": {"reason": "ContainerCreating"}})
    state = submitted.status(HANDLE)
    assert (state.phase, state.detail) == ("starting", "ContainerCreating")


def test_a_pod_that_finished_before_the_job_noticed_stays_running(submitted, fake):
    """Deviation from row 19: never back to `starting` once it ran."""
    _admit(fake)
    fake.add_pod(NAME, phase="Succeeded", start_time=T0, state=_terminated(0, "Completed"))
    assert submitted.status(HANDLE).phase == "running"


# -- cross-cutting ----------------------------------------------------------------


@pytest.mark.parametrize("resource", ["jobs", "pods", "workloads"])
def test_status_raises_when_the_api_is_down(submitted, fake, resource):
    if resource == "pods":
        _admit(fake)
    fake.fail_next("GET", resource, 500)
    with pytest.raises(ExecutorUnavailable):
        submitted.status(HANDLE)


def test_details_are_truncated(submitted, fake):
    fake.set_workload(NAME, conditions=[
        {"type": "QuotaReserved", "status": "False", "reason": "WaitingForQuota",
         "message": "x" * 2000},
    ])
    assert len(submitted.status(HANDLE).detail) == kx.DETAIL_MAX_CHARS == 500


def test_status_reads_only_what_it_needs(submitted, fake):
    fake.calls.clear()
    submitted.status(HANDLE)  # suspended: Job + Workload, no pods
    assert [(c.verb, c.resource) for c in fake.calls] == [("get", "jobs"), ("list", "workloads")]
    _admit(fake)
    fake.calls.clear()
    submitted.status(HANDLE)  # running: Job + pods, no Workload
    assert [(c.verb, c.resource) for c in fake.calls] == [("get", "jobs"), ("list", "pods")]
    _fail(fake)
    fake.calls.clear()
    submitted.status(HANDLE)  # failed: Job + pods (rows 3-7 need the pod)
    assert [(c.verb, c.resource) for c in fake.calls] == [("get", "jobs"), ("list", "pods")]


def test_the_pure_mapping_is_callable_directly():
    job = {"metadata": {"name": NAME}, "spec": {"suspend": True}, "status": {}}
    state = kx._job_state(job, None, None, now=NOW)
    assert state.phase == "pending_capacity"
