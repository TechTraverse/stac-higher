"""Kubernetes executor: one Job + one Secret per submission, Kueue-admitted
(K-5, process-compute spec §6, ADR 0019).

**What one run is.** A Job ``stac-run-{run_id}-{hex6}`` (``stac-scan-`` for
an image scan) in ``PROCESS_K8S_NAMESPACE``, labelled for Kueue
(``queue-name``, ``priority-class``), plus a Secret of the same name holding
the run's COMPLETE environment, owned by the Job; a private user image adds a
``{job}-pull`` Secret. The manifests are pure functions
(``kubernetes_manifests.py``); this module is the I/O.

**Decisions** (plan rulings): the name is per SUBMISSION, not per run, so a
resubmission never collides with a predecessor still being deleted and the
ledger's fences see a new handle (K5-R2). The Job is created first and the
Secret second, owned by the Job's uid, so it dies with the Job and the
pipeline needs no ``patch secrets`` (K5-R3). The run's timeout is the POD's
``activeDeadlineSeconds`` (the kubelet kills the containers and KEEPS the
pod, so its log survives), the Job's deadline a backstop (K5-R4). ``cancel``
stops a started pod by lowering that deadline and keeps it for ``logs``;
``reap`` deletes (K5-R5).

**ADR 0013 invariants on this backend.** The environment reaches the run only
through its Secret (never the Job spec, an exception text or a log line);
limits are the pod's requests == limits, set at create time; ``reap`` is
explicit (no ``ttlSecondsAfterFinished``); ``list_launched`` raises on any
API error, never answers ``[]`` for an outage. The egress boundary FAILS
CLOSED (addendum A18): no run starts while the runs namespace lacks its
default-deny NetworkPolicy (checked by ``submit`` and ``probe``, cached
:data:`ISOLATION_CHECK_TTL_SECONDS`), and a run asking for network access no
policy here can express is refused (``RunRejected``). There is no setting
that turns the check off; tests serve the policy from the fake API server.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
import secrets
import threading
import time
import urllib.parse
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pipeline.config import (
    DEFAULT_PROCESS_K8S_DEFAULT_QUEUE,
    DEFAULT_PROCESS_K8S_NAMESPACE,
    DEFAULT_PROCESS_K8S_RUN_SERVICE_ACCOUNT,
)
from pipeline.images.reference import is_image_digest, is_image_reference
from pipeline.process.executor import (
    RESYNC,
    RUN_ID_LABEL,
    RUN_KIND_LABEL,
    Executor,
    ExecutorUnavailable,
    ExitStatus,
    ImagePullFailed,
    LaunchedRun,
    RunEvent,
    RunHandle,
    RunRejected,
    RunSpec,
    RunState,
    oom_error,
)
from pipeline.process.kube_api import KubeApi, KubeApiError, KubeNotFound
from pipeline.process.kubernetes_manifests import (
    CONTAINER,
    DEFAULT_DENY_POLICY,
    KUEUE_API_VERSION,
    KUEUE_JOB_UID_LABEL,
    POD_JOB_LABEL,
    JobContext,
    build_env_secret,
    build_job,
    build_pull_secret,
    isolation_problem,
    job_name,
    network_refusal,
    parse_job_name,
)

if TYPE_CHECKING:
    from pipeline.config import Settings

logger = logging.getLogger(__name__)

#: The API server's cap on a Secret's data.
SECRET_MAX_BYTES = 1024 * 1024
#: K5-R5: what ``cancel`` lowers a started pod's deadline to.
CANCEL_POD_DEADLINE_SECONDS = 1
#: K5-R7: how long a pod may wait on an image pull or its config before the
#: run is failed (an attempt spent).
START_FAILURE_GRACE_SECONDS = 300
#: Waiting reasons that will never resolve on their own: fail at once.
IMMEDIATE_START_FAILURES = frozenset({"InvalidImageName", "ErrImageNeverPull"})
#: Waiting reasons that may be transient (a registry blip, a Secret racing
#: the pod): fail only after the grace.
GRACED_START_FAILURES = frozenset({
    "ErrImagePull", "ImagePullBackOff", "CreateContainerConfigError", "CreateContainerError",
})
#: How much of the backend's words a detail or an error carries.
DETAIL_MAX_CHARS = 500
#: The status texts (K-6 A5: exported so the kind leg asserts on them).
JOB_GONE_DETAIL = "the run's Job no longer exists"
JOB_DELETING_DETAIL = "the run's Job is being deleted outside the platform"
DISRUPTION_EXIT = "the run's pod was disrupted"
PREEMPTED_PREFIX = "preempted: "
WAITING_FOR_NODE_PREFIX = "waiting for a node: "
DEACTIVATED_PREFIX = "Kueue deactivated the run: "
START_FAILURE_PREFIX = "the run could not start"
#: The Job controller's reason when our podFailurePolicy rule (FailJob on a
#: DisruptionTarget pod condition) failed the Job (K-6 A6).
POD_FAILURE_POLICY_REASON = "PodFailurePolicy"
#: A container killed without an exit code reads as SIGKILL.
SIGKILL_EXIT_CODE = 137

#: K5-R21: the server ends each watch after this (``timeoutSeconds``), so a
#: watch reconnects with a fresh token at least this often.
WATCH_TIMEOUT_SECONDS = 300
#: A watch silent this long (no event, no bookmark) is a dead connection.
#: Both are read at call time (K-6 A3).
WATCH_IDLE_SECONDS = 360
#: A watch the server closed sooner than this is resumed after a pause, so a
#: misbehaving server cannot spin the loop.
_WATCH_MIN_LIFETIME_SECONDS = 1.0

#: How long a passed isolation check is trusted (addendum A18): one API read
#: per minute per namespace, not one per run. A failed check is never cached.
ISOLATION_CHECK_TTL_SECONDS = 60.0

#: (api_url, namespace) -> when the isolation check last passed (monotonic).
#: Module-level because executors are built per tick (K5-R18).
_isolation_passed: dict[tuple[str, str], float] = {}
_isolation_lock = threading.Lock()


def reset_isolation_cache() -> None:
    """Forget every passed isolation check (tests)."""
    with _isolation_lock:
        _isolation_passed.clear()


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def k8s_time(raw: object) -> dt.datetime | None:
    """An RFC 3339 API timestamp (``…Z``, optional fraction), or None."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def _require_pinned(image: str) -> None:
    """Spec §8.4 (Docker's rule and words): a user image runs only by digest."""
    reference, sep, digest = image.partition("@")
    if not sep or not is_image_reference(reference) or not is_image_digest(digest):
        raise ImagePullFailed(f"refusing to launch a user image not pinned by digest: {image!r}")


def _conditions(obj: Mapping) -> dict[str, dict]:
    """``status.conditions`` by type."""
    status = obj.get("status") or {}
    return {
        str(c.get("type")): c for c in status.get("conditions") or [] if isinstance(c, dict)
    }


def _is_true(conditions: Mapping[str, dict], kind: str) -> bool:
    return (conditions.get(kind) or {}).get("status") == "True"


def _newest(items: list[dict]) -> dict | None:
    """The item with the latest creationTimestamp (ties by name)."""
    epoch = dt.datetime.min.replace(tzinfo=dt.UTC)
    return max(
        items,
        key=lambda o: (
            k8s_time((o.get("metadata") or {}).get("creationTimestamp")) or epoch,
            str((o.get("metadata") or {}).get("name") or ""),
        ),
        default=None,
    )


def _clip(text: object) -> str:
    return str(text or "")[:DETAIL_MAX_CHARS]


def _words(reason: object, message: object) -> str:
    """``reason: message`` (or whichever is present), clipped."""
    parts = [str(p) for p in (reason, message) if p]
    return _clip(": ".join(parts))


def _container_status(pod: Mapping) -> dict:
    for status in (pod.get("status") or {}).get("containerStatuses") or []:
        if isinstance(status, dict) and status.get("name", CONTAINER) == CONTAINER:
            return status
    return {}


def _memory_mb(pod: Mapping) -> int | None:
    """The run container's memory limit in MB (``<n>Mi`` / ``<n>Gi``)."""
    for container in (pod.get("spec") or {}).get("containers") or []:
        if isinstance(container, dict) and container.get("name") == CONTAINER:
            raw = str(((container.get("resources") or {}).get("limits") or {}).get("memory") or "")
            for suffix, factor in (("Mi", 1), ("Gi", 1024)):
                if raw.endswith(suffix) and raw[: -len(suffix)].isdigit():
                    return int(raw[: -len(suffix)]) * factor
    return None


def _job_state(job: Mapping, workload: Mapping | None, pod: Mapping | None, *,
               now: dt.datetime) -> RunState:
    """The Job, its Workload (while suspended) and its newest pod onto K-4's
    phases — the plan's Task 6 table, first matching row wins. Kueue owns
    ``pending_capacity``, the kubelet ``starting``/``running``, the Job
    controller ``exited``; only an affirmed absence or a disruption is
    ``lost``. Pure, so the table is testable on hand-built dicts."""
    meta = job.get("metadata") or {}
    spec = job.get("spec") or {}
    status = job.get("status") or {}
    conditions = _conditions(job)
    pod = pod or {}
    pod_status = pod.get("status") or {}
    pod_conditions = _conditions(pod)
    container = _container_status(pod)
    state = container.get("state") or {}
    terminated = state.get("terminated") if isinstance(state.get("terminated"), dict) else None
    waiting = state.get("waiting") if isinstance(state.get("waiting"), dict) else None

    if _is_true(conditions, "Complete"):  # row 2
        finished = (k8s_time((terminated or {}).get("finishedAt"))
                    or k8s_time(conditions["Complete"].get("lastTransitionTime"))
                    or k8s_time(status.get("completionTime")))
        return RunState("exited", exit=ExitStatus(0), finished_at=finished)

    if _is_true(conditions, "Failed"):
        failed = conditions["Failed"]
        job_reason = str(failed.get("reason") or "")
        finished = (k8s_time((terminated or {}).get("finishedAt"))
                    or k8s_time(failed.get("lastTransitionTime")))
        disruption = pod_conditions.get("DisruptionTarget")
        if job_reason == POD_FAILURE_POLICY_REASON:  # row 3 (K-6 A6)
            return RunState("lost", detail=_clip(
                f"{DISRUPTION_EXIT} ({job_reason}): {failed.get('message') or ''}"))
        if disruption is not None and disruption.get("status") == "True":  # row 3
            return RunState("lost", detail=_clip(
                f"{DISRUPTION_EXIT} ({disruption.get('reason') or 'DisruptionTarget'}): "
                f"{disruption.get('message') or ''}"))
        if pod_status.get("reason") == "Evicted":  # row 3
            return RunState("lost", detail=_clip(
                f"{DISRUPTION_EXIT} (Evicted): {pod_status.get('message') or ''}"))
        code = terminated.get("exitCode") if terminated else None
        code = code if isinstance(code, int) else None
        if pod_status.get("reason") == "DeadlineExceeded" or job_reason == "DeadlineExceeded":
            return RunState(  # row 4
                "exited", exit=ExitStatus(code or SIGKILL_EXIT_CODE, timed_out=True),
                finished_at=finished,
            )
        if terminated is not None:
            reason = terminated.get("reason")
            if reason == "OOMKilled":  # row 5
                return RunState("exited", exit=ExitStatus(
                    code or SIGKILL_EXIT_CODE, error=oom_error(_memory_mb(pod))),
                    finished_at=finished)
            error = None if reason in (None, "Error", "Completed") else _words(
                reason, terminated.get("message"))
            return RunState(  # row 6
                "exited", exit=ExitStatus(code if code is not None else -1, error=error),
                finished_at=finished,
            )
        return RunState("exited", exit=ExitStatus(-1, error=_clip(  # row 7
            f"the run's Job failed ({job_reason or 'Failed'}): {failed.get('message') or ''}"
        )), finished_at=finished)

    if meta.get("deletionTimestamp"):  # row 8
        return RunState("lost", detail=JOB_DELETING_DETAIL)

    if spec.get("suspend"):
        if workload is None:  # row 9
            return RunState("pending_capacity")
        wl_conditions = _conditions(workload)
        if (workload.get("spec") or {}).get("active") is False:  # row 10
            words = next((c.get("message") for c in (wl_conditions.get("Evicted"),
                                                     wl_conditions.get("QuotaReserved"))
                          if c and c.get("message")), "")
            return RunState("exited", exit=ExitStatus(
                -1, error=_clip(f"{DEACTIVATED_PREFIX}{words}")))
        quota_reserved = _is_true(wl_conditions, "QuotaReserved")
        if _is_true(wl_conditions, "Evicted") and not quota_reserved:  # row 11
            evicted = wl_conditions["Evicted"]
            reason = str(evicted.get("reason") or "")
            message = evicted.get("message") or ""
            detail = (f"{PREEMPTED_PREFIX}{message}" if reason == "Preempted"
                      else f"evicted ({reason}): {message}")
            return RunState("pending_capacity", detail=_clip(detail))
        if quota_reserved:  # row 13: Kueue is unsuspending it
            return RunState("starting")
        # row 12: by the condition's STATUS; the reason is never read (K-6 A1)
        message = (wl_conditions.get("QuotaReserved") or {}).get("message")
        return RunState("pending_capacity", detail=_clip(message) if message else None)

    if not pod:  # row 14
        return RunState("starting")
    scheduled = pod_conditions.get("PodScheduled") or {}
    if scheduled.get("status") == "False" and scheduled.get("reason") == "Unschedulable":
        return RunState("pending_capacity", detail=_clip(  # row 15 (K5-R9)
            f"{WAITING_FOR_NODE_PREFIX}{scheduled.get('message') or ''}"))
    if waiting is not None:
        reason = str(waiting.get("reason") or "")
        failure = _clip(f"{START_FAILURE_PREFIX} ({reason}): {waiting.get('message') or ''}")
        if reason in IMMEDIATE_START_FAILURES:  # row 16
            return RunState("exited", exit=ExitStatus(-1, error=failure))
        if reason in GRACED_START_FAILURES:  # row 17 (K5-R7)
            since = (k8s_time(pod_status.get("startTime"))
                     or k8s_time((pod.get("metadata") or {}).get("creationTimestamp")))
            if since is not None and (now - since).total_seconds() >= START_FAILURE_GRACE_SECONDS:
                return RunState("exited", exit=ExitStatus(-1, error=failure))
            return RunState("starting", detail=_words(reason, waiting.get("message")))
    ready = status.get("ready")
    if (isinstance(ready, int) and ready >= 1) or "running" in state or terminated is not None:
        # row 18; a container that already terminated while the Job
        # controller has not yet noticed stays `running` (never back to
        # `starting` once it ran).
        return RunState("running")
    return RunState(  # row 19
        "starting", detail=_clip(waiting.get("reason")) if waiting and waiting.get("reason")
        else None,
    )


def _job_event(obj: object) -> RunEvent | None:
    """A Job watch object -> a wake-up for its run (identity only, K-4 R1),
    or None for anything not ours."""
    if not isinstance(obj, dict):
        return None
    meta = obj.get("metadata")
    if not isinstance(meta, dict):
        return None
    labels = meta.get("labels") or {}
    run_id = labels.get(RUN_ID_LABEL) if isinstance(labels, dict) else None
    name = meta.get("name")
    if not isinstance(run_id, str) or not run_id or not isinstance(name, str):
        return None
    kind = labels.get(RUN_KIND_LABEL)
    return RunEvent(run_id=run_id, handle_id=name,
                    kind=kind if isinstance(kind, str) and kind else "process")


def _workload_event(obj: object) -> RunEvent | None:
    """A Workload watch object -> a wake-up for its Job's run. Kueue does not
    copy the Job's labels onto its Workload, so the run is read from the
    owning Job's name (K5-R21)."""
    if not isinstance(obj, dict):
        return None
    meta = obj.get("metadata")
    if not isinstance(meta, dict):
        return None
    for owner in meta.get("ownerReferences") or []:
        if not isinstance(owner, dict) or owner.get("kind") != "Job":
            continue
        parsed = parse_job_name(owner.get("name"))
        if parsed is not None:
            kind, run_id = parsed
            return RunEvent(run_id=run_id, handle_id=str(owner["name"]), kind=kind)
    return None


@dataclass
class KubernetesExecutor(Executor):
    """See the module docstring. Building one does no I/O."""

    api: KubeApi
    namespace: str = DEFAULT_PROCESS_K8S_NAMESPACE
    default_queue: str = DEFAULT_PROCESS_K8S_DEFAULT_QUEUE
    run_service_account: str = DEFAULT_PROCESS_K8S_RUN_SERVICE_ACCOUNT
    registry_mirrors: Mapping[str, str] = field(default_factory=dict)
    name: str = "kubernetes"
    #: Test seams: "now" for the start-failure grace, and the per-submission
    #: name suffix.
    _clock: Callable[[], dt.datetime] = field(default=_utcnow, repr=False)
    _suffix: Callable[[], str] = field(default=lambda: secrets.token_hex(3), repr=False)
    _monotonic: Callable[[], float] = field(default=time.monotonic, repr=False)

    @classmethod
    def from_settings(cls, settings: Settings) -> KubernetesExecutor:
        return cls(
            api=KubeApi.from_settings(settings),
            namespace=settings.process_k8s_namespace,
            default_queue=settings.process_k8s_default_queue,
            run_service_account=settings.process_k8s_run_service_account,
            registry_mirrors=dict(settings.process_k8s_registry_mirrors),
        )

    # -- paths ----------------------------------------------------------------

    @property
    def _jobs(self) -> str:
        return f"/apis/batch/v1/namespaces/{self.namespace}/jobs"

    @property
    def _pods(self) -> str:
        return f"/api/v1/namespaces/{self.namespace}/pods"

    @property
    def _secrets(self) -> str:
        return f"/api/v1/namespaces/{self.namespace}/secrets"

    @property
    def _network_policies(self) -> str:
        return f"/apis/networking.k8s.io/v1/namespaces/{self.namespace}/networkpolicies"

    @property
    def _workloads(self) -> str:
        return f"/apis/kueue.x-k8s.io/{KUEUE_API_VERSION}/namespaces/{self.namespace}/workloads"

    @staticmethod
    def _item(collection: str, name: str) -> str:
        return f"{collection}/{urllib.parse.quote(name, safe='')}"

    def _ctx(self) -> JobContext:
        return JobContext(
            namespace=self.namespace,
            default_queue=self.default_queue,
            run_service_account=self.run_service_account,
            registry_mirrors=self.registry_mirrors,
        )

    def _job_pods(self, name: str) -> list[dict]:
        pods, _rv = self.api.list(self._pods, label_selector=f"{POD_JOB_LABEL}={name}")
        return pods

    def _delete_job(self, name: str) -> None:
        """Foreground: the Job goes once its pods (and owned Secrets) have."""
        with contextlib.suppress(KubeNotFound):
            self.api.delete(self._item(self._jobs, name), propagation="Foreground")

    def require_isolation(self) -> None:
        """Raise :class:`ExecutorUnavailable` unless the runs namespace's
        :data:`DEFAULT_DENY_POLICY` exists and denies all ingress and egress
        to every run pod (addendum A18). A passed check is cached for
        :data:`ISOLATION_CHECK_TTL_SECONDS`; a failure is not. Through
        ``submit`` the run is requeued without spending an attempt, and
        ``/health`` names the missing policy."""
        key = (self.api.config.api_url, self.namespace)
        now = self._monotonic()
        with _isolation_lock:
            passed = _isolation_passed.get(key)
        if passed is not None and now - passed < ISOLATION_CHECK_TTL_SECONDS:
            return
        try:
            policy: dict | None = self.api.get(
                self._item(self._network_policies, DEFAULT_DENY_POLICY)
            )
        except KubeNotFound:
            policy = None
        problem = isolation_problem(policy, self.namespace)
        with _isolation_lock:
            if problem is None:
                _isolation_passed[key] = now
            else:
                _isolation_passed.pop(key, None)
        if problem is not None:
            raise ExecutorUnavailable(f"{problem}; refusing to start runs")

    # -- Executor -------------------------------------------------------------

    def submit(self, spec: RunSpec) -> RunHandle:
        if spec.user_image:
            _require_pinned(spec.image)  # before any API call
        refusal = network_refusal(spec)
        if refusal is not None:
            raise RunRejected(refusal)
        name = job_name(spec.kind, spec.run_id, self._suffix())
        ctx = self._ctx()
        pull_name = f"{name}-pull" if spec.registry_auth is not None else None
        # Everything is built before the first call: a profile that cannot
        # carry the GPU count (HardwareUnavailable) creates nothing.
        job = build_job(spec, name=name, ctx=ctx, pull_secret=pull_name)
        probe_secret = build_env_secret(spec, name=name, owner_uid="", ctx=ctx)
        if len(json.dumps(probe_secret["data"])) > SECRET_MAX_BYTES:
            raise RunRejected(
                f"run {spec.run_id}'s environment is larger than a Kubernetes Secret "
                "allows (1 MiB)"
            )
        self.require_isolation()
        try:
            created = self.api.create(self._jobs, job)
        except KubeApiError as err:
            if err.status == 422:
                raise RunRejected(f"the cluster rejected run {spec.run_id}'s Job: {err}") from err
            raise
        uid = str((created.get("metadata") or {}).get("uid") or "")
        if not uid:
            self._discard_job(name, spec.run_id)
            raise ExecutorUnavailable(f"kubernetes created Job {name} without a uid")
        owned = [build_env_secret(spec, name=name, owner_uid=uid, ctx=ctx)]
        pull = build_pull_secret(spec, name=name, owner_uid=uid, ctx=ctx)
        if pull is not None:
            owned.append(pull)
        for secret in owned:
            try:
                self.api.create(self._secrets, secret, redact=True)
            except ExecutorUnavailable as err:
                # K5-R3: no Job without its environment. The Secret's
                # error is redacted at the source and never chained here.
                self._discard_job(name, spec.run_id)
                status = err.status if isinstance(err, KubeApiError) else None
                if status in (413, 422):
                    raise RunRejected(
                        f"the cluster rejected run {spec.run_id}'s environment Secret "
                        f"({status})"
                    ) from None
                raise ExecutorUnavailable(str(err)) from None
        return RunHandle(id=name, backend=self.name)

    def _discard_job(self, name: str, run_id: str) -> None:
        """Remove a Job whose submission failed half-way (its Secrets go with
        it); best effort — the reaper removes what this misses."""
        try:
            self._delete_job(name)
        except ExecutorUnavailable as err:
            logger.warning(
                "kubernetes run Job could not be removed after a failed submit",
                extra={"run_id": run_id, "run": name, "error": str(err)},
            )

    def status(self, handle: RunHandle) -> RunState:
        """One read: the Job, then only what its state needs — the newest
        pod for a running or failed Job, the Workload for a suspended one.
        A Job the API affirms is gone is ``lost``; any other error raises
        :class:`ExecutorUnavailable` (K-4: an outage is not an absence)."""
        try:
            job = self.api.get(self._item(self._jobs, handle.id))
        except KubeNotFound:
            return RunState("lost", detail=JOB_GONE_DETAIL)
        conditions = _conditions(job)
        workload = pod = None
        if not _is_true(conditions, "Complete"):
            failed = _is_true(conditions, "Failed")
            deleting = bool((job.get("metadata") or {}).get("deletionTimestamp"))
            suspended = bool((job.get("spec") or {}).get("suspend"))
            if failed or not (suspended or deleting):
                pod = _newest(self._job_pods(handle.id))
            elif suspended and not deleting:
                workload = self._workload(job)
        return _job_state(job, workload, pod, now=self._clock())

    def _workload(self, job: Mapping) -> dict | None:
        """The Job's Kueue Workload, by its job-uid label; None when there is
        none yet or Kueue is not installed (its API answers 404)."""
        uid = str((job.get("metadata") or {}).get("uid") or "")
        if not uid:
            return None
        try:
            workloads, _rv = self.api.list(
                self._workloads, label_selector=f"{KUEUE_JOB_UID_LABEL}={uid}"
            )
        except KubeNotFound:
            return None
        return _newest(workloads)

    def cancel(self, handle: RunHandle) -> None:
        """K-4's contract: stop the run idempotently and KEEP it, so the
        caller reads its log next and ``reap`` deletes it after the verdict.
        Returning does not mean stopped (the kubelet stops it within the
        grace); the caller re-reads ``status()``."""
        try:
            job = self.api.get(self._item(self._jobs, handle.id))
        except KubeNotFound:
            return
        conditions = _conditions(job)
        if _is_true(conditions, "Complete") or _is_true(conditions, "Failed"):
            return
        self._stop_keeping_the_log(handle.id)

    def _stop_keeping_the_log(self, name: str) -> None:
        """THE stop mechanism (K5-R5), isolated so it can be swapped.

        Kubernetes cannot stop a container and keep its log except through a
        deadline: deleting the Job or the pod deletes the log too. So a
        STARTED pod gets ``spec.activeDeadlineSeconds`` lowered to
        :data:`CANCEL_POD_DEADLINE_SECONDS` (a mutable pod field); the
        kubelet sends SIGTERM, waits ``terminationGracePeriodSeconds`` (10 s)
        and kills, and the pod stays ``Failed`` / ``DeadlineExceeded`` until
        ``reap``. A Job with no started pod has nothing to keep and is
        deleted (foreground).

        UNVERIFIED ON A REAL CLUSTER until K-6 (#14): that the kubelet keeps
        a deadline-killed container's log readable through ``pods/log``. If
        it does not, the fallback is to collect logs BEFORE the stop (K-4's
        cancel row reordered inside ``collecting``) and replace this body
        with a foreground delete of the Job.
        """
        started = [
            pod for pod in self._job_pods(name)
            if (pod.get("status") or {}).get("startTime")
            and (pod.get("status") or {}).get("phase") not in ("Succeeded", "Failed")
        ]
        for pod in started:
            pod_name = str((pod.get("metadata") or {}).get("name") or "")
            with contextlib.suppress(KubeNotFound):
                self.api.patch(
                    self._item(self._pods, pod_name),
                    {"spec": {"activeDeadlineSeconds": CANCEL_POD_DEADLINE_SECONDS}},
                )
        if not started:
            self._delete_job(name)

    def logs(self, handle: RunHandle, max_bytes: int) -> bytes:
        """``pods/log`` of the Job's newest pod: combined stdout/stderr, the
        first ``max_bytes`` (K5-R26). No pod, a container that never
        started, or an outage is ``b""`` and a WARNING: losing the log never
        loses the verdict."""
        try:
            pod = _newest(self._job_pods(handle.id))
            if pod is None:
                raise ExecutorUnavailable("the run's Job has no pod")
            pod_name = str((pod.get("metadata") or {}).get("name") or "")
            payload = self.api.get_raw(
                f"{self._item(self._pods, pod_name)}/log",
                query={"container": CONTAINER, "limitBytes": str(max_bytes),
                       "timestamps": "false"},
            )
        except ExecutorUnavailable as err:
            logger.warning(
                "process run logs unavailable", extra={"run": handle.id, "error": str(err)}
            )
            return b""
        return payload[:max_bytes]

    def reap(self, handle: RunHandle) -> None:
        """Delete the Job (foreground, so its pod goes first) and both
        Secrets. Explicit Secret deletes are belt and braces over the
        ownerReference GC. Never raises; a 404 is success."""
        deletes: list[Callable[[], None]] = [
            lambda: self.api.delete(self._item(self._jobs, handle.id), propagation="Foreground"),
            lambda: self.api.delete(self._item(self._secrets, handle.id)),
            lambda: self.api.delete(self._item(self._secrets, f"{handle.id}-pull")),
        ]
        for delete in deletes:
            try:
                delete()
            except KubeNotFound:
                continue
            except ExecutorUnavailable as err:
                logger.warning(
                    "process run reap failed", extra={"run": handle.id, "error": str(err)}
                )

    def list_launched(self) -> list[LaunchedRun]:
        jobs, _rv = self.api.list(self._jobs, label_selector=RUN_ID_LABEL)
        launched: list[LaunchedRun] = []
        for job in jobs:
            meta = job.get("metadata") or {}
            labels = meta.get("labels") or {}
            run_id = labels.get(RUN_ID_LABEL)
            name = meta.get("name")
            if not run_id or not name:
                continue  # not ours: never reaped on our say-so
            launched.append(LaunchedRun(
                handle=RunHandle(id=str(name), backend=self.name),
                run_id=str(run_id),
                created_at=k8s_time(meta.get("creationTimestamp")),
                kind=str(labels.get(RUN_KIND_LABEL) or "process"),
            ))
        return launched

    def probe(self) -> None:
        """One ``limit=1`` Job list (the API server answers and the Role lets
        us list, K5-R17), then the isolation precondition (addendum A18)."""
        self.api.get(self._jobs, query={"labelSelector": RUN_ID_LABEL, "limit": "1"})
        self.require_isolation()

    def _list_resource_version(self, path: str, selector: str) -> str:
        """The resourceVersion of a ``limit=1`` list: where a watch starts,
        without replaying every existing object as ADDED (the RESYNC already
        sweeps the ledger)."""
        doc = self.api.get(path, query={"labelSelector": selector, "limit": "1"})
        return str((doc.get("metadata") or {}).get("resourceVersion") or "")

    async def watch(self) -> AsyncIterator[RunEvent]:
        """K5-R21: a Job watch (our run-id label) and, when Kueue is
        installed, a Workload watch, merged into K-4 wake-ups. Each starts
        from a ``limit=1`` list's resourceVersion, asks for bookmarks, and
        resumes from the last seen or bookmarked version when the server
        ends it normally; a 410, a transport error or
        :data:`WATCH_IDLE_SECONDS` of silence on either RAISES, so K-4's
        ``run_watcher`` reconnects and resyncs. No pod watch: what only a
        pod shows (pull errors) is the run tick's (K5-R7)."""
        job_rv = await asyncio.to_thread(self._list_resource_version, self._jobs, RUN_ID_LABEL)
        try:
            workload_rv: str | None = await asyncio.to_thread(
                self._list_resource_version, self._workloads, ""
            )
        except KubeNotFound:
            workload_rv = None  # Kueue not installed: Jobs only
        yield RESYNC
        queue: asyncio.Queue[RunEvent] = asyncio.Queue()
        pumps = [asyncio.create_task(
            self._pump(self._jobs, RUN_ID_LABEL, job_rv, _job_event, queue)
        )]
        if workload_rv is not None:
            pumps.append(asyncio.create_task(
                self._pump(self._workloads, "", workload_rv, _workload_event, queue)
            ))
        getter: asyncio.Task | None = None
        try:
            while True:
                getter = asyncio.create_task(queue.get())
                done, _pending = await asyncio.wait(
                    [getter, *pumps], return_when=asyncio.FIRST_COMPLETED
                )
                if getter in done:
                    yield getter.result()
                    continue
                getter.cancel()
                for pump in pumps:
                    if pump.done():
                        pump.result()  # re-raises the pump's error
                raise ExecutorUnavailable("a kubernetes watch ended unexpectedly")
        finally:
            for task in (*pumps, getter):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)

    async def _pump(self, path: str, selector: str, rv: str,
                    mapper: Callable[[object], RunEvent | None],
                    queue: asyncio.Queue[RunEvent]) -> None:
        """One watch, resumed across normal closes; raises on anything else."""
        loop = asyncio.get_running_loop()
        while True:
            query = {
                "resourceVersion": rv,
                "allowWatchBookmarks": "true",
                "timeoutSeconds": str(WATCH_TIMEOUT_SECONDS),
            }
            if selector:
                query["labelSelector"] = selector
            opened = loop.time()
            async for event in self.api.watch(path, query=query,
                                              idle_timeout=WATCH_IDLE_SECONDS):
                obj = event.get("object")
                meta = obj.get("metadata") if isinstance(obj, dict) else None
                if isinstance(meta, dict) and meta.get("resourceVersion"):
                    rv = str(meta["resourceVersion"])
                if event.get("type") == "BOOKMARK":
                    continue
                wake = mapper(obj)
                if wake is not None:
                    await queue.put(wake)
            if loop.time() - opened < _WATCH_MIN_LIFETIME_SECONDS:
                await asyncio.sleep(_WATCH_MIN_LIFETIME_SECONDS)
