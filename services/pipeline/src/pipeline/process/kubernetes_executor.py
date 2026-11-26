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
    RUN_ID_LABEL,
    RUN_KIND_LABEL,
    Executor,
    ExecutorUnavailable,
    ImagePullFailed,
    LaunchedRun,
    RunEvent,
    RunHandle,
    RunRejected,
    RunSpec,
    RunState,
)
from pipeline.process.kube_api import KubeApi, KubeApiError, KubeNotFound
from pipeline.process.kubernetes_manifests import (
    CONTAINER,
    DEFAULT_DENY_POLICY,
    KUEUE_API_VERSION,
    POD_JOB_LABEL,
    JobContext,
    build_env_secret,
    build_job,
    build_pull_secret,
    isolation_problem,
    job_name,
    network_refusal,
)

if TYPE_CHECKING:
    from pipeline.config import Settings

logger = logging.getLogger(__name__)

#: The API server's cap on a Secret's data.
SECRET_MAX_BYTES = 1024 * 1024
#: K5-R5: what ``cancel`` lowers a started pod's deadline to.
CANCEL_POD_DEADLINE_SECONDS = 1
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

    def status(self, handle: RunHandle) -> RunState:  # Task 6
        raise NotImplementedError

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

    async def watch(self) -> AsyncIterator[RunEvent]:  # Task 7
        raise NotImplementedError
        yield  # pragma: no cover
