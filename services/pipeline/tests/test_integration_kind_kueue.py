"""K-6: Kueue, the admission policies, least privilege and network isolation
on a real cluster (kind).

Skips unless ``KIND_INTEGRATION=1`` (see ``test_integration_kind_executor.py``).
Every executor call runs as the pipeline's ServiceAccount; the admin
kubeconfig is the harness's (``tests/_kind.py``).

The K-5 hand-off items this module settles (infra/kubernetes/README.md,
"What a real cluster must prove"): 2 (preemption leaves the Job un-failed;
Kueue clears ``Evicted`` on readmission), 5 (Kueue's job-webhook denial and
the executor's mapping), 10 (the admission policy admits what the executor
builds; preemption reads ``pending_capacity`` with no attempt spent) and 11
(NetworkPolicy is enforced, then a run cannot reach IMDS or the internet).
Each unverified one is a recording scenario (K6-R22): its fixture never
raises, and each test's failure message names the outcome that occurred."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import json
import socket
import time

import pytest

import _admission_rules as rules
import _kind
from _kind import NAMESPACE, code_spec, kget, kubectl, observe, printing
from _process_fake import FakeProcessRepo
from pipeline.process import kubernetes_executor as kx
from pipeline.process import kubernetes_manifests as km
from pipeline.process.executor import (
    ExecutorUnavailable,
    ExitStatus,
    RegistryAuth,
    RunHandle,
    RunRejected,
    RunState,
)
from pipeline.process.kube_api import KubeApiError
from pipeline.process.reconcile import Reconciler

pytestmark = _kind.skip_unless_kind
_kind.require()  # at import: a half-configured CI run fails, never skips

JOBS = f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs"
#: A server-side dry run: admission (policies and webhooks) runs, nothing persists.
DRY_RUN_JOBS = f"{JOBS}?dryRun=All"
PODS = f"/api/v1/namespaces/{NAMESPACE}/pods"
DRY_RUN_SECRETS = f"/api/v1/namespaces/{NAMESPACE}/secrets?dryRun=All"
NETPOL_NAMESPACE = "k6-netpol-probe"


@pytest.fixture(autouse=True)
def _clean():
    kx.reset_isolation_cache()
    _kind.clean_namespace()
    yield
    _kind.clean_namespace()


def _ctx(**overrides) -> km.JobContext:
    base = dict(namespace=NAMESPACE, default_queue="process-runs",
                run_service_account="stac-higher-run", registry_mirrors={})
    return km.JobContext(**{**base, **overrides})


def _built_job(spec=None, *, ctx: km.JobContext | None = None, suffix: str = "c0ffee") -> dict:
    """A Job exactly as the executor builds it (its naming included)."""
    spec = spec or code_spec("pass")
    name = km.job_name(spec.kind, spec.run_id, suffix)
    pull = f"{name}-pull" if spec.registry_auth else None
    return km.build_job(spec, name=name, ctx=ctx or _ctx(), pull_secret=pull)


def _refusal(path: str, body: dict) -> KubeApiError | None:
    """POST as the pipeline; the API error, or None when admitted."""
    try:
        _kind.api().create(path, body)
    except KubeApiError as err:
        return err
    return None


class _Store:
    """The Reconciler's object store: records what it was asked to put."""

    def __init__(self) -> None:
        self.keys: list[str] = []

    def put_object(self, **kwargs) -> None:
        self.keys.append(kwargs["Key"])


# -- capacity --------------------------------------------------------------------


def test_a_second_run_waits_for_capacity_then_runs():
    ex = _kind.executor()
    a = ex.submit(code_spec(printing(15)))
    with _kind.reaping(ex, a):
        _kind.wait_state(ex, a, {"running"})
        b = ex.submit(code_spec("pass"))
        with _kind.reaping(ex, b):
            # Right after submit B reads pending_capacity with no detail (no
            # Workload yet, or no QuotaReserved condition yet): wait for Kueue's words.
            waiting = _kind.wait_for(lambda: ex.status(b),
                                     lambda s: s.phase == "pending_capacity" and bool(s.detail),
                                     what="B waiting for capacity with Kueue's message",
                                     timeout=30)
            quota = _kind.conditions(_kind.workload_of(b)).get("QuotaReserved") or {}
            observe("capacity-wait", detail=waiting.detail, reason=quota.get("reason"),
                    message=quota.get("message"))
            assert waiting.detail and "cpu" in waiting.detail.lower(), waiting
            seen: list = []
            while ex.status(a).phase == "running":
                assert ex.status(b).phase != "running", "B ran while A held the only CPU"
                time.sleep(1)
            state = _kind.wait_state(ex, b, {"exited", "lost"}, seen=seen, timeout=90)
            assert state.phase == "exited" and state.exit == ExitStatus(0), (state, seen)


# -- preemption (K-5 hand-off items 2 and 10) -------------------------------------


async def _preemption() -> dict:
    """A triggered run A, running, is preempted by an interactive run B.
    Records the states, the Job's and Workload's conditions, and what K-4's
    real Reconciler writes to the (in-memory) ledger (K6-R18)."""
    ex = _kind.executor()
    repo = FakeProcessRepo(runtime={"kind": "inline_python", "retry": {"max_attempts": 3}})
    store = _Store()

    async def nothing(*_args) -> None:
        return None

    reconciler = Reconciler(repo=repo, executor=ex, settings=_kind.settings(),
                            storage_client=store, enqueue_finalize=nothing,
                            enqueue_run_now=nothing)
    rec: dict = {"a_states": [], "a_job_conditions": [], "a_workload_conditions": [],
                 "job_failed": None}
    now = dt.datetime.now(dt.UTC)
    run_id = await repo.enqueue_run(
        process_id="k6-process", revision_id="rev-1", source_id="k6-source",
        input_items=[{"item_id": "a"}], deferred_until=None, is_test=False,
        association_id=None,
    )
    claimed = await repo.claim_run(run_id, now, backend="kubernetes")
    a = await asyncio.to_thread(ex.submit, code_spec(printing(25), run_id=run_id))
    b: RunHandle | None = None
    rec["a"] = a.id
    try:
        await repo.record_submission(
            run_id, claimed_at=claimed.claimed_at, handle=a.id, backend="kubernetes",
            phase="pending_capacity", submitted_at=now, credentials_expire_at=None,
        )
        await asyncio.to_thread(_kind.wait_state, ex, a, {"running"})
        b = await asyncio.to_thread(ex.submit, code_spec(printing(10), priority="interactive"))

        def look() -> None:
            state = ex.status(a)
            job = _kind.job_of(a)
            workload = _kind.workload_of(a)
            for key, value in (("a_states", (state.phase, state.detail)),
                               ("a_job_conditions", _kind.condition_summary(job)),
                               ("a_workload_conditions", _kind.condition_summary(workload))):
                if not rec[key] or rec[key][-1] != value:
                    rec[key].append(value)
            failed = _kind.conditions(job).get("Failed") or {}
            if failed.get("status") == "True":
                rec["job_failed"] = f"{failed.get('reason')}: {failed.get('message')}"
            rec["last_state"] = state

        # Up to 90 s for A to be preempted (or for the Job to fail instead).
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            await asyncio.to_thread(look)
            if rec["last_state"].phase != "running" or rec["job_failed"]:
                break
            await asyncio.sleep(1)
        rec["preempted"] = rec["last_state"].phase == "pending_capacity"
        if rec["preempted"]:
            run = await repo.get_submitted_run(run_id, backend="kubernetes")
            rec["reconcile_while_preempted"] = await reconciler.reconcile(run)
            row = repo._row(run_id)
            rec["row_while_preempted"] = {k: row.get(k) for k in
                                          ("status", "attempts", "phase", "executor_handle")}
        # B finishes; A is readmitted (same Job) and runs to the end.
        try:
            b_state = await asyncio.to_thread(_kind.wait_state, ex, b, {"exited", "lost"})
            rec["b_state"] = b_state
        except AssertionError as err:
            rec["b_error"] = str(err)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            await asyncio.to_thread(look)
            phase = rec["last_state"].phase
            if phase == "running" and "readmitted_evicted" not in rec:
                rec["readmitted_evicted"] = _kind.condition_summary(
                    _kind.workload_of(a)).get("Evicted", "absent")
            if phase in ("exited", "lost"):
                break
            await asyncio.sleep(1)
        rec["a_final"] = rec["last_state"]
        run = await repo.get_submitted_run(run_id, backend="kubernetes")
        rec["final_reconcile"] = await reconciler.reconcile(run) if run else "not submitted"
        row = repo._row(run_id)
        rec["final_row"] = {k: row.get(k) for k in ("status", "attempts", "phase")}
        rec["logs_stored"] = len(store.keys)
    except Exception as err:  # recorded; the tests name what it means
        rec["scenario_error"] = repr(err)
    finally:
        for handle in (a, b):
            if handle is not None:
                await asyncio.to_thread(ex.reap, handle)
    observe("preemption", **{k: v for k, v in rec.items() if k != "last_state"})
    return rec


@pytest.fixture(scope="module")
def preemption() -> dict:
    _kind.clean_namespace()
    try:
        rec = asyncio.run(_preemption())
    finally:
        _kind.clean_namespace()
    # A scenario that broke early still answers every test (with its error).
    for key in ("preempted", "reconcile_while_preempted", "row_while_preempted", "a_final",
                "final_reconcile", "final_row", "logs_stored", "readmitted_evicted"):
        rec.setdefault(key, None)
    if rec["a_final"] is None:
        rec["a_final"] = RunState("missing", detail=rec.get("scenario_error"))
    rec["row_while_preempted"] = rec["row_while_preempted"] or {}
    rec["final_row"] = rec["final_row"] or {}
    return rec


def test_preemption_leaves_the_job_unfailed(preemption):
    """Item 2: Kueue's suspension of a backoffLimit-0 Job with podFailurePolicy
    must not fail the Job."""
    failed = preemption["job_failed"]
    context = (f"states={preemption['a_states']} Job={preemption['a_job_conditions']} "
               f"Workload={preemption['a_workload_conditions']}")
    if failed and failed.startswith(kx.POD_FAILURE_POLICY_REASON):
        pytest.fail("OUTCOME B: the preempted pod carried DisruptionTarget and the Job failed "
                    f"as PodFailurePolicy ({failed}); every preemption reads lost (a requeue "
                    f"and a new submission). A fix/ PR must not FailJob on it. {context}")
    if failed:
        pytest.fail("OUTCOME C: the Job controller counted the preempted pod as a failure "
                    f"({failed}); every preemption spends an attempt. A fix/ PR is needed "
                    f"(K5-R6). {context}")
    assert preemption["preempted"], (
        "OUTCOME D: A was never seen waiting after B was submitted — no preemption "
        f"happened (check WorkloadPriorityClasses and the ClusterQueue's preemption). "
        f"{preemption.get('scenario_error', '')} {context}")


def test_preemption_spends_no_attempt(preemption):
    """Item 10 through K-4's real Reconciler: the row stays running, attempt 1."""
    assert preemption["reconcile_while_preempted"] in ("phase", "unchanged"), (
        preemption.get("scenario_error"), preemption["a_states"])
    row = preemption["row_while_preempted"]
    assert row["status"] == "running" and row["attempts"] == 1, row
    assert row["phase"] == "pending_capacity" and row["executor_handle"] == preemption["a"], row


def test_a_preempted_run_completes_on_readmission(preemption):
    final = preemption["a_final"]
    assert final.phase == "exited" and final.exit == ExitStatus(0), (
        final, preemption["a_states"], preemption.get("b_error"))
    assert preemption["final_reconcile"] == "succeeded", preemption["final_reconcile"]
    assert preemption["final_row"]["attempts"] == 1, preemption["final_row"]
    assert preemption["logs_stored"] == 1


def test_kueue_clears_evicted_on_readmission(preemption):
    """Item 2's second half: a readmitted Workload's Evicted is not left True
    (else a re-suspended Job reads as preempted rather than starting, row 11)."""
    seen = preemption.get("readmitted_evicted")
    assert seen is not None, f"A never ran again: {preemption['a_states']}"
    assert not str(seen).startswith("True"), (
        f"OUTCOME: Kueue left Evicted={seen} on the readmitted Workload. Cosmetic for a "
        "running Job (row 11 is read only while suspended); a fix/ PR should make row 11 "
        "require QuotaReserved=False.")


# -- waitForPodsReady, eviction, flavors ------------------------------------------


def test_an_unschedulable_run_is_evicted_then_deactivated():
    """Kueue admits (the flavor has no labels) but no node matches the
    selector: row 15 while it waits, then waitForPodsReady (kind: 30 s, one
    requeue) evicts, requeues and deactivates it, and the run dies (K5-R8)."""
    ex = _kind.executor()
    profile = _kind.profile_with(node_selector={"stac-higher.io/flavor": "nowhere"})
    handle = ex.submit(code_spec("pass", profile=profile))
    with _kind.reaping(ex, handle):
        seen: list = []
        try:
            state = _kind.wait_state(ex, handle, {"exited", "lost"}, seen=seen, timeout=240)
        except AssertionError as err:
            state = None
            seen.append(("timeout", str(err)[:300]))
        workload = _kind.workload_of(handle)
        observe("unschedulable", seen=seen,
                workload_active=((workload or {}).get("spec") or {}).get("active"),
                workload_conditions=_kind.condition_summary(workload),
                job_conditions=_kind.condition_summary(_kind.job_of(handle)))
        assert any(p == "pending_capacity" and (d or "").startswith(kx.WAITING_FOR_NODE_PREFIX)
                   for p, d in seen), f"never read as waiting for a node: {seen}"
        assert state is not None and state.phase == "exited", seen
        assert (state.exit.error or "").startswith(kx.DEACTIVATED_PREFIX), (state, seen)
        assert ((workload or {}).get("spec") or {}).get("active") is False, workload


def test_an_evicted_pod_is_lost_not_failed():
    """A pod the cluster disrupts (the Eviction API) is `lost`: requeued with
    no attempt spent (K5-R6, A6's podFailurePolicy)."""
    ex = _kind.executor()
    handle = ex.submit(code_spec(printing(120)))
    with _kind.reaping(ex, handle):
        _kind.wait_state(ex, handle, {"running"})
        pod = _kind.pod_of(handle)["metadata"]["name"]
        eviction = {"apiVersion": "policy/v1", "kind": "Eviction",
                    "metadata": {"name": pod, "namespace": NAMESPACE}}
        kubectl("create", "--raw", f"/api/v1/namespaces/{NAMESPACE}/pods/{pod}/eviction",
                "-f", "-", input=json.dumps(eviction))
        seen: list = []
        state = _kind.wait_state(ex, handle, {"lost", "exited"}, seen=seen, timeout=60)
        job = _kind.job_of(handle)
        observe("eviction", seen=seen, job_conditions=_kind.condition_summary(job))
        assert state.phase == "lost" and (state.detail or "").startswith(kx.DISRUPTION_EXIT), (
            f"OUTCOME: an evicted run read {state}; Job={_kind.condition_summary(job)}. If the "
            "Job failed as BackoffLimitExceeded, the podFailurePolicy rule did not match "
            "(no DisruptionTarget on the pod): a fix/ PR on K-5's A6 mapping.")


#: A taint only this test puts on the node, and the flavor that tolerates it.
TAINT = "k6.example/runs-node"


def test_a_flavors_tolerations_reach_the_admitted_pods():
    """For R-8 (#144): Kueue injects a ResourceFlavor's `tolerations` into the
    pods of a Job admitted on it, so a tainted runs node takes image scans and
    runs that carry no toleration of their own."""
    ex = _kind.executor()
    node = kubectl("get", "nodes", "-o", "jsonpath={.items[0].metadata.name}").stdout
    objects = [
        {"apiVersion": "kueue.x-k8s.io/v1beta2", "kind": "ResourceFlavor",
         "metadata": {"name": "k6-tainted"},
         "spec": {"tolerations": [{"key": TAINT, "operator": "Exists",
                                   "effect": "NoSchedule"}]}},
        {"apiVersion": "kueue.x-k8s.io/v1beta2", "kind": "ClusterQueue",
         "metadata": {"name": "k6-tolerations"},
         "spec": {"namespaceSelector": {"matchLabels": {
                      "kubernetes.io/metadata.name": NAMESPACE}},
                  "resourceGroups": [{"coveredResources": ["cpu", "memory"], "flavors": [{
                      "name": "k6-tainted", "resources": [
                          {"name": "cpu", "nominalQuota": "1"},
                          {"name": "memory", "nominalQuota": "4Gi"}]}]}]}},
        {"apiVersion": "kueue.x-k8s.io/v1beta2", "kind": "LocalQueue",
         "metadata": {"name": "k6-tolerations", "namespace": NAMESPACE},
         "spec": {"clusterQueue": "k6-tolerations"}},
    ]
    handle = None
    try:
        for obj in objects:
            _kind.kapply(obj)
        kubectl("wait", "--for=condition=Active", "clusterqueue/k6-tolerations",
                "--timeout=60s")
        kubectl("-n", NAMESPACE, "wait", "--for=condition=Active",
                "localqueue/k6-tolerations", "--timeout=60s")
        kubectl("taint", "nodes", node, f"{TAINT}=true:NoSchedule")
        handle = ex.submit(code_spec("pass", profile=_kind.profile_with(queue="k6-tolerations")))
        seen: list = []
        state = _kind.wait_state(ex, handle, {"exited", "lost"}, seen=seen, timeout=120)
        pod = _kind.pod_of(handle) or {}
        tolerations = (pod.get("spec") or {}).get("tolerations") or []
        observe("flavor-tolerations", seen=seen, pod_tolerations=tolerations)
        assert any(t.get("key") == TAINT for t in tolerations), tolerations
        assert state.phase == "exited" and state.exit == ExitStatus(0), (state, seen)
    finally:
        kubectl("taint", "nodes", node, f"{TAINT}-", check=False)
        if handle is not None:
            ex.reap(handle)
        _kind.clean_namespace()
        kubectl("-n", NAMESPACE, "delete", "localqueue", "k6-tolerations",
                "--ignore-not-found", "--timeout=60s", check=False)
        kubectl("delete", "clusterqueue", "k6-tolerations", "--ignore-not-found",
                "--timeout=60s", check=False)
        kubectl("delete", "resourceflavor", "k6-tainted", "--ignore-not-found",
                "--timeout=60s", check=False)


# -- admission: Kueue's webhook and the runs namespace's policies ------------------


def test_a_kueue_webhook_denial_kills_the_run():
    """Item 5: Kueue's job webhook refuses a queue name that is not a DNS
    subdomain (v0.20.8 ValidateQueueName, K6-R24). Record the status and
    message; the executor must make it RunRejected (a dead run), not an outage."""
    bad = "Not_A_Queue"
    probe = _refusal(DRY_RUN_JOBS, _built_job(ctx=_ctx(default_queue=bad),
                                              spec=code_spec("pass", profile=_kind.profile_with(
                                                  queue=None))))
    observe("kueue-webhook-denial",
            status=getattr(probe, "status", None), reason=getattr(probe, "reason", None),
            message=getattr(probe, "server_message", None),
            admission_denied=getattr(probe, "admission_denied", None))
    assert probe is not None, (
        f"Kueue's webhook admitted queue-name {bad!r}: this probe no longer provokes a "
        "denial on this Kueue release; pick another field ValidateJobOnCreate refuses")
    ex = _kind.executor(PROCESS_K8S_DEFAULT_QUEUE=bad)
    spec = code_spec("pass", profile=_kind.profile_with(queue=None))
    try:
        handle = ex.submit(spec)
    except RunRejected:
        pass
    except ExecutorUnavailable as err:
        pytest.fail(f"OUTCOME: the denial came back as {probe.status} {probe.reason} "
                    f"({probe.server_message!r}) and the executor treated it as an outage "
                    f"(requeue forever): {err}. A fix/ PR on KubeApiError.admission_denied.")
    else:
        ex.reap(handle)
        pytest.fail("the executor's submit was admitted although the dry run was refused")
    assert not (kget("jobs", selector=f"stac-higher.run-id={spec.run_id}") or {}).get("items")


def test_a_run_with_an_unknown_priority_dies_rather_than_requeues():
    ex = _kind.executor()
    spec = code_spec("pass", priority="urgent")
    with pytest.raises(RunRejected) as err:
        ex.submit(spec)
    assert "[priority-class]" in str(err.value), err.value
    assert not (kget("jobs", selector=f"stac-higher.run-id={spec.run_id}") or {}).get("items")


def _variants() -> list[tuple[str, dict]]:
    digest = "sha256:" + "b" * 64
    user = dataclasses.replace(
        code_spec("pass"), image=f"registry.example/acme/tool@{digest}", user_image=True,
        registry_auth=RegistryAuth(username="u", password="p", server="registry.example"))
    scan = dataclasses.replace(code_spec("pass"), kind="image_scan", profile=None)
    return [
        ("process", _built_job()),
        ("interactive", _built_job(code_spec("pass", priority="interactive"))),
        ("user-image", _built_job(user)),
        ("image-scan", _built_job(scan)),
    ]


def _built_secrets() -> list[tuple[str, dict]]:
    """The env and pull Secrets the executor builds for a private user image."""
    digest = "sha256:" + "b" * 64
    run = dataclasses.replace(
        code_spec("pass"), image=f"registry.example/acme/tool@{digest}", user_image=True,
        registry_auth=RegistryAuth(username="u", password="p", server="registry.example"))
    name = km.job_name(run.kind, run.run_id, "c0ffee")
    owner = "00000000-0000-4000-8000-000000000001"
    return [("env", km.build_env_secret(run, name=name, owner_uid=owner, ctx=_ctx())),
            ("pull", km.build_pull_secret(run, name=name, owner_uid=owner, ctx=_ctx()))]


def test_every_job_the_executor_builds_is_admitted():
    """Item 10: the policies, PSA restricted and Kueue's webhooks admit every
    shape build_job makes, and both Secret types (a server dry run, as the
    pipeline)."""
    refused = {name: f"{err.status} {err.server_message}"
               for name, job in _variants() if (err := _refusal(DRY_RUN_JOBS, job))}
    refused.update({f"secret-{name}": f"{err.status} {err.server_message}"
                    for name, secret in _built_secrets()
                    if (err := _refusal(DRY_RUN_SECRETS, secret))})
    assert refused == {}, refused


@pytest.mark.parametrize("rule", rules.SECRET_RULES, ids=lambda r: r.rule_id)
def test_each_secret_rule_refuses_its_violation(rule):
    """K-6 review R5: no Secret the cluster would fill with a token."""
    _name, secret = _built_secrets()[0]
    rule.mutate(secret)
    err = _refusal(DRY_RUN_SECRETS, secret)
    assert err is not None, f"[{rule.rule_id}] the server admitted the violating Secret"
    observe("secret-rule", rule=rule.rule_id, status=err.status, message=err.server_message)
    assert err.status == 422 and f"[{rule.rule_id}]" in err.server_message, (
        err.status, err.server_message)


@pytest.mark.parametrize("rule", rules.JOB_RULES,
                         ids=[f"{r.rule_id}-{i}" for i, r in enumerate(rules.JOB_RULES)])
def test_each_job_rule_refuses_its_violation(rule):
    """K6-R21: the CEL catches what its pure twin catches (same mutation)."""
    job = _built_job()
    rule.mutate(job)
    err = _refusal(DRY_RUN_JOBS, job)
    assert err is not None, f"[{rule.rule_id}] the server admitted the violating Job"
    observe("job-rule", rule=rule.rule_id, status=err.status, message=err.server_message)
    assert err.status == 422, (err.status, err.server_message)
    if not rule.api_may_answer_first:
        assert f"[{rule.rule_id}]" in err.server_message, err.server_message
        assert rules.JOB_POLICY in err.server_message, err.server_message


def test_the_pipeline_may_change_a_run_pod_only_by_lowering_its_deadline():
    """K6-R21 on Pod UPDATE: each forbidden patch is refused (422, its rule
    id), a relabel included; the executor's own cancel patch is admitted."""
    ex = _kind.executor()
    handle = ex.submit(code_spec(printing(300), timeout_seconds=600))
    with _kind.reaping(ex, handle):
        _kind.wait_state(ex, handle, {"running"})
        pod = _kind.pod_of(handle)
        path = f"{PODS}/{pod['metadata']['name']}"
        outcomes = {}
        for rule in rules.POD_UPDATE_RULES:
            try:
                _kind.api().patch(path, rule.patch(pod))
                outcomes[rule.rule_id] = "ADMITTED"
            except KubeApiError as err:
                outcomes[rule.rule_id] = f"{err.status} {err.server_message}"
        observe("pod-update-rules", outcomes=outcomes)
        for rule in rules.POD_UPDATE_RULES:
            outcome = outcomes[rule.rule_id]
            assert outcome.startswith("422"), (rule.rule_id, outcomes)
            if not rule.api_may_answer_first:
                assert f"[{rule.rule_id}]" in outcome, (rule.rule_id, outcomes)
        after = _kind.pod_of(handle)
        assert after["metadata"]["labels"] == pod["metadata"]["labels"]  # no relabel
        ex.cancel(handle)  # the one patch the pipeline may make
        state = _kind.wait_state(ex, handle, {"exited", "lost"}, timeout=90)
        assert state.phase == "exited", state


# -- least privilege on the real authorizer ----------------------------------------


def test_the_pipeline_cannot_do_what_it_must_not():
    api = _kind.api()
    for call in (lambda: api.get(f"/api/v1/namespaces/{NAMESPACE}/secrets/any-run"),
                 lambda: api.create(PODS, {"apiVersion": "v1", "kind": "Pod",
                                           "metadata": {"name": "bare"},
                                           "spec": {"containers": [{"name": "c",
                                                                    "image": "x"}]}})):
        with pytest.raises(KubeApiError) as err:
            call()
        assert err.value.status == 403 and not err.value.admission_denied, err.value
    denied = [
        (NAMESPACE, "", "secrets", "", verb) for verb in ("get", "list", "watch")
    ] + [
        (NAMESPACE, "", "pods", "exec", "create"),
        (NAMESPACE, "", "pods", "eviction", "create"),
        (NAMESPACE, "", "serviceaccounts", "token", "create"),
        (NAMESPACE, "batch", "jobs", "", "patch"),
        (NAMESPACE, "batch", "jobs", "", "update"),
        ("default", "batch", "jobs", "", "create"),
        ("", "", "nodes", "", "list"),
        ("", "kueue.x-k8s.io", "clusterqueues", "", "create"),
        ("", "kueue.x-k8s.io", "clusterqueues", "", "update"),
        (NAMESPACE, "kueue.x-k8s.io", "localqueues", "", "create"),
        (NAMESPACE, "kueue.x-k8s.io", "localqueues", "", "update"),
        (NAMESPACE, "kueue.x-k8s.io", "workloads", "", "delete"),
        (NAMESPACE, "kueue.x-k8s.io", "workloads", "", "patch"),
        ("", "admissionregistration.k8s.io", "validatingadmissionpolicies", "", "delete"),
        (NAMESPACE, "networking.k8s.io", "networkpolicies", "", "delete"),
        (NAMESPACE, "rbac.authorization.k8s.io", "roles", "", "create"),
        (NAMESPACE, "rbac.authorization.k8s.io", "rolebindings", "", "create"),
        ("kueue-system", "", "configmaps", "", "get"),
    ]
    allowed = []
    for namespace, group, resource, sub, verb in denied:
        attributes = {"verb": verb, "group": group, "resource": resource}
        if namespace:
            attributes["namespace"] = namespace
        if sub:
            attributes["subresource"] = sub
        review = api.create("/apis/authorization.k8s.io/v1/selfsubjectaccessreviews", {
            "apiVersion": "authorization.k8s.io/v1", "kind": "SelfSubjectAccessReview",
            "spec": {"resourceAttributes": attributes}})
        if review["status"].get("allowed"):
            allowed.append(attributes)
    assert allowed == [], allowed


# -- network isolation (K-5 hand-off item 11; K6-R20) ------------------------------

IMDS = "169.254.169.254"
#: How long a new run pod's egress may stay open before enforcement catches
#: up (measured ~0.5 s on kind v0.33.0; generous so only a regression fails).
STARTUP_WINDOW_BOUND_S = 10.0


def _external_ip() -> str:
    """An internet address the runner itself reaches (the job downloaded from it)."""
    return socket.getaddrinfo("github.com", 443, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]


@pytest.fixture(scope="module")
def network_controls() -> dict:
    """First prove this cluster ENFORCES NetworkPolicy, in a scratch namespace
    without Pod Security or our policies: a client reaches a server pod and
    the internet with no policy (positive controls), then cannot reach the
    server once a default-deny exists. Never raises (K6-R22)."""
    rec: dict = {"external": _external_ip()}
    kubectl("create", "namespace", NETPOL_NAMESPACE, check=False)
    try:
        _kind.probe_pod(NETPOL_NAMESPACE, "server", (
            "import http.server\n"
            "http.server.ThreadingHTTPServer(('', 8080), "
            "http.server.SimpleHTTPRequestHandler).serve_forever()\n"), wait=False)
        server = _kind.wait_for(
            lambda: kget("pods", "server", namespace=NETPOL_NAMESPACE),
            lambda p: bool(p and p["status"].get("podIP") and p["status"].get("phase")
                           == "Running"),
            what="the probe server running", timeout=120)
        ip = server["status"]["podIP"]
        # Positive controls for every target the run test checks (K-6 review
        # R6b): a run's False is trusted only where this pod's is True.
        before = [["server", ip, 8080, 20], ["external", rec["external"], 443, 3],
                  ["imds", IMDS, 80, 2],
                  ["api", "$KUBERNETES_SERVICE_HOST", "$KUBERNETES_SERVICE_PORT", 3]]
        code, log = _kind.probe_pod(NETPOL_NAMESPACE, "client-open", _kind.REACH_CODE,
                                    env={"K6_TARGETS": json.dumps(before)})
        rec["open"] = _kind.reach_results(log) or {"exit": code, "log": log[-500:]}
        deny = {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
                "metadata": {"name": "default-deny", "namespace": NETPOL_NAMESPACE},
                "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}}
        _kind.kapply(deny)
        time.sleep(5)  # let the CNI's policy controller see the POLICY
        # A NEW client under the policy, external first: its t0 phase shows
        # whether a pod the CNI has not learned yet gets out (K6-R27).
        after = [["external", rec["external"], 443], ["server", ip, 8080]]
        code, log = _kind.probe_pod(NETPOL_NAMESPACE, "client-denied", _kind.PHASED_REACH_CODE,
                                    env={"K6_TARGETS": json.dumps(after),
                                         "K6_WINDOW": "external"})
        rec["denied"] = _kind.reach_results(log) or {"exit": code, "log": log[-500:]}
    except Exception as err:  # recorded; the tests name what it means
        rec["error"] = repr(err)
    finally:
        kubectl("delete", "namespace", NETPOL_NAMESPACE, "--wait=false", check=False)
    observe("network-controls", **rec)
    return rec


def test_this_cluster_enforces_network_policy(network_controls):
    open_, denied = network_controls.get("open") or {}, network_controls.get("denied") or {}
    assert open_.get("server") is True, (
        f"the probe is broken: with no policy the client could not reach the server pod "
        f"({network_controls})")
    settled = denied.get("settled") or {}
    assert settled.get("server") is False and settled.get("external") is False, (
        "OUTCOME: NetworkPolicy is NOT enforced in this cluster (a default-deny changed "
        f"nothing): the kind node's CNI ignores policies; check the kind version "
        f"(>= v0.24.0 enforces) ({network_controls})")


def test_a_run_pod_cannot_reach_metadata_the_api_or_the_internet(network_controls):
    """The runs namespace's default-deny (infra/kubernetes/runs-default-deny.yaml)
    on a real run: no IMDS, no API server, no internet ONCE ENFORCEMENT HAS
    SETTLED (strict). Whether a run gets out in its first moments, before the
    CNI has learned the new pod's IP (kube-network-policies diverts only the
    IPs of pods it knows to its queue), is measured and recorded, not
    asserted: that is a property of the CNI's enforcing mode, which the
    deployment must choose (K6-R27, docs/ISSUES.md I-188, GitHub #207). The window
    must stay under STARTUP_WINDOW_BOUND_S."""
    external = network_controls["external"]
    targets = [["imds", IMDS, 80], ["external", external, 443],
               ["api", "$KUBERNETES_SERVICE_HOST", "$KUBERNETES_SERVICE_PORT"]]
    ex = _kind.executor()
    handle = ex.submit(code_spec(_kind.PHASED_REACH_CODE, timeout_seconds=180, env={
        "K6_TARGETS": json.dumps(targets), "K6_WINDOW": "external", "K6_SETTLE_S": "15"}))
    with _kind.reaping(ex, handle):
        state = _kind.wait_state(ex, handle, {"exited", "lost"}, timeout=240)
        log = ex.logs(handle, 65536).decode(errors="replace")
        results = _kind.reach_results(log) or {}
        pod = _kind.pod_of(handle) or {}
        container = next(iter((pod.get("status") or {}).get("containerStatuses") or []), {})
        t0 = results.get("t0") or {}
        observe("run-isolation", results=results, state=state,
                pod_start=(pod.get("status") or {}).get("startTime"),
                container_started=((container.get("state") or {}).get("terminated")
                                   or {}).get("startedAt"),
                host_network=(pod.get("spec") or {}).get("hostNetwork", False),
                startup_race=any(t0.values()))
        if any(t0.values()):
            print(f"::warning::K-6 startup race: a new run pod reached "
                  f"{[k for k, v in t0.items() if v]} before NetworkPolicy enforcement "
                  f"settled (window {results.get('window')})")
        assert state.phase == "exited" and state.exit.ok, (state, log[-500:])
        assert (pod.get("spec") or {}).get("hostNetwork") in (None, False), pod.get("spec")
        settled = results.get("settled")
        assert settled is not None, f"the probe printed no result: {log[-500:]}"
        controls = network_controls.get("open") or {}
        untestable = sorted(k for k in settled if controls.get(k) is not True)
        observe("run-isolation-testable", testable=sorted(set(settled) - set(untestable)),
                not_testable_here=untestable, controls=controls)
        assert "external" not in untestable, (
            "the external-host check is vacuous: even with no policy a pod could not reach "
            f"{external}:443 ({network_controls})")
        # The startup window (docs/ISSUES.md I-188, GitHub #207) is recorded,
        # not asserted; but it must stay short, or enforcement latency regressed.
        window = results.get("window") or {}
        blocked_at = window.get("first_blocked_s")
        assert blocked_at is not None and blocked_at <= STARTUP_WINDOW_BOUND_S, (
            f"OUTCOME 'enforcement latency regressed': a new run pod's egress to {external} "
            f"was first blocked at {blocked_at} s (bound {STARTUP_WINDOW_BOUND_S} s; measured "
            f"~0.5 s on kind v0.33.0). window={window}, t0={t0}")
        reached = sorted(k for k, v in settled.items() if v and k not in untestable)
        assert reached == [], (
            f"OUTCOME 'reachable throughout': {results.get('settled_at_s')} s after start a "
            f"run pod still reached {reached} (t0={t0}, window={results.get('window')}); the "
            "default-deny does not select run pods or is not enforced for them. Controls "
            f"without a policy: {controls}; not testable here: {untestable}")


# -- Kueue's webhooks narrowed to the runs namespace (K-6 review R3, I-186) ------
# Last in the module: it stops Kueue's controller and restores it.


def test_kueues_webhooks_reach_only_the_runs_namespace():
    """Installed through scripts/kueue-narrow-webhooks.py, every Kueue webhook
    with a namespace selector matches only the runs namespace, so with Kueue
    down a Deployment elsewhere still creates while a run Job is refused."""
    narrowed = {"matchLabels": {"kubernetes.io/metadata.name": NAMESPACE}}
    wide = []
    for kind in ("mutatingwebhookconfiguration", "validatingwebhookconfiguration"):
        for config in (kget(kind, namespace=None) or {}).get("items") or []:
            if not config["metadata"]["name"].startswith("kueue-"):
                continue
            wide += [h["name"] for h in config["webhooks"]
                     if "namespaceSelector" in h and h["namespaceSelector"] != narrowed]
    assert wide == [], f"Kueue webhooks still matching other namespaces: {wide}"

    deployment = {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"name": "k6-without-kueue", "namespace": "default"},
        "spec": {"replicas": 1, "selector": {"matchLabels": {"app": "k6"}},
                 "template": {"metadata": {"labels": {"app": "k6"}}, "spec": {
                     "containers": [{"name": "c", "image": _built_job()["spec"]["template"][
                         "spec"]["containers"][0]["image"]}]}}},
    }
    rec: dict = {}
    try:
        kubectl("-n", "kueue-system", "scale", "deploy/kueue-controller-manager", "--replicas=0")
        kubectl("-n", "kueue-system", "wait", "--for=delete", "pod",
                "-l", "control-plane=controller-manager", "--timeout=120s", check=False)
        elsewhere = kubectl("create", "--dry-run=server", "-f", "-",
                            input=json.dumps(deployment), check=False)
        rec["deployment_elsewhere"] = (elsewhere.returncode, elsewhere.stderr.strip()[:300])
        refused = _refusal(DRY_RUN_JOBS, _built_job())
        rec["run_job"] = (getattr(refused, "status", None),
                          getattr(refused, "server_message", None))
    finally:
        kubectl("-n", "kueue-system", "scale", "deploy/kueue-controller-manager", "--replicas=1")
        kubectl("-n", "kueue-system", "rollout", "status", "deploy/kueue-controller-manager",
                "--timeout=300s", check=False, timeout=320)
        _kind.wait_for(lambda: _refusal(DRY_RUN_JOBS, _built_job()), lambda err: err is None,
                       what="Kueue's webhook answering again", timeout=120, interval=3)
    observe("kueue-down", **rec)
    assert rec["deployment_elsewhere"][0] == 0, (
        f"with Kueue down a Deployment in `default` was refused: {rec['deployment_elsewhere']}")
    assert rec["run_job"][0] is not None, (
        "with Kueue down a run Job was admitted: Kueue's webhooks no longer guard the runs "
        "namespace")
