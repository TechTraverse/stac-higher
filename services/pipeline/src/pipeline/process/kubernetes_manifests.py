"""What one run is made of on Kubernetes, as pure functions (K-5, spec §6.1).

A run is ONE Job (Kueue admits it; its pod runs the image) plus ONE Secret
carrying the run's complete environment, owned by the Job; a private user
image adds a pull Secret. Everything here is a function of the
:class:`~pipeline.process.executor.RunSpec`, the profile's
``backend.kubernetes`` block and the settings — no I/O and no logging, so the
security-relevant shape is pinned by plain dict assertions
(``tests/test_kubernetes_manifests.py``):

- The environment lives ONLY in the Secret (``envFrom``): anyone with
  ``get jobs`` must not read a run's credentials or code (ADR 0013).
- The run's timeout is the POD's ``activeDeadlineSeconds``; the Job's is the
  timeout plus slack (K5-R4): a Job past its own deadline has its pods
  DELETED, taking the log an operator most wants with them, while the kubelet
  kills a pod past its deadline and keeps it.
- Every pod is Pod Security ``restricted``: uid 10001, no privilege
  escalation, every capability dropped, RuntimeDefault seccomp, a read-only
  root with memory-backed scratch, no ServiceAccount token, no service links
  (K5-R13, K5-R14). ``RunSpec.network`` is not read: isolation is R-8's
  NetworkPolicy, selected by ``stac-higher.run-kind`` (K5-R15).
- ``requests == limits`` for cpu, memory and the GPU resource: what Kueue
  counts is what the pod gets (Guaranteed QoS).
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from pipeline.process.executor import (
    IMAGE_ID_LABEL,
    PROCESS_ID_LABEL,
    RUN_ID_LABEL,
    RUN_KIND_LABEL,
    RUN_UID,
    HardwareUnavailable,
    RunSpec,
)
from pipeline.process.hardware import KubernetesBackend, kubernetes_backend

#: Kueue's labels (v0.19.2 ``pkg/controller/constants``).
QUEUE_LABEL = "kueue.x-k8s.io/queue-name"
PRIORITY_LABEL = "kueue.x-k8s.io/priority-class"
KUEUE_JOB_UID_LABEL = "kueue.x-k8s.io/job-uid"
#: The Job controller's label on the pods it creates.
POD_JOB_LABEL = "batch.kubernetes.io/job-name"
#: Karpenter (EKS Auto Mode) never consolidates a node away from under a
#: pod carrying this (K5-R6).
DO_NOT_DISRUPT = "karpenter.sh/do-not-disrupt"
#: The Workload API version Kueue v0.19.2 stores (Task 0 addendum).
KUEUE_API_VERSION = "v1beta2"
#: The one container of every run pod.
CONTAINER = "run"
#: The Job's deadline is the run's timeout plus this (K5-R4): the backstop
#: for a pod that never starts; the pod's own deadline is the timeout.
JOB_DEADLINE_SLACK_SECONDS = 600
#: SIGTERM, then SIGKILL after this — Docker's stop grace (K-4).
TERMINATION_GRACE_SECONDS = 10
#: The platform images' WORKDIR (services/process-runtime, image-scanner):
#: writable on a read-only root.
PLATFORM_WORKDIRS = {"process": "/home/runner", "image_scan": "/home/scanner"}
NAME_PREFIX = {"process": "stac-run-", "image_scan": "stac-scan-"}
#: Image scans run at the `triggered` priority (lead ruling).
SCAN_PRIORITY = "triggered"

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_JOB_NAME_RE = re.compile(rf"^stac-(run|scan)-({_UUID})-([0-9a-f]{{6}})$")
_KIND_BY_WORD = {"run": "process", "scan": "image_scan"}


@dataclass(frozen=True)
class JobContext:
    """The settings a manifest needs (the executor builds it from its own
    fields)."""

    namespace: str
    default_queue: str
    run_service_account: str
    registry_mirrors: Mapping[str, str]


def job_name(kind: str, run_id: str, suffix: str) -> str:
    """``stac-run-{run_id}-{suffix}`` (``stac-scan-`` for a scan): one name
    per SUBMISSION (K5-R2), so a resubmission never collides with a
    predecessor still being deleted. 52 / 53 characters for a uuid run id:
    the pod (+6) and Kueue's Workload (``job-…-xxxxx``) stay within 63."""
    return f"{NAME_PREFIX.get(kind, NAME_PREFIX['process'])}{run_id}-{suffix}"


def parse_job_name(name: object) -> tuple[str, str] | None:
    """``(kind, run_id)`` of one of our Job names, or None for anything else."""
    if not isinstance(name, str):
        return None
    match = _JOB_NAME_RE.match(name)
    if match is None:
        return None
    return _KIND_BY_WORD[match.group(1)], match.group(2)


def rewrite_image(image: str, mirrors: Mapping[str, str]) -> str:
    """Swap the registry host for its mirror prefix (K5-R12), keeping path,
    tag and digest. The first ``/`` component is a host only when it contains
    ``.`` or ``:`` or is ``localhost`` (Docker's rule); an unmapped host, or a
    reference without one, is unchanged."""
    first, sep, rest = image.partition("/")
    if not sep or not ("." in first or ":" in first or first == "localhost"):
        return image
    prefix = mirrors.get(first)
    if not prefix:
        return image
    return f"{prefix.rstrip('/')}/{rest}"


def run_labels(spec: RunSpec) -> dict[str, str]:
    """The labels on the Job, its pod template and its Secrets."""
    labels = {RUN_ID_LABEL: spec.run_id, RUN_KIND_LABEL: spec.kind}
    if spec.kind == "image_scan":
        labels[IMAGE_ID_LABEL] = spec.process_id
    else:
        labels[PROCESS_ID_LABEL] = spec.process_id
    return labels


def _block(spec: RunSpec) -> KubernetesBackend:
    return kubernetes_backend(spec.profile) if spec.profile is not None else KubernetesBackend()


def _resources(spec: RunSpec, block: KubernetesBackend) -> dict[str, str]:
    """requests == limits. ``cpu_exempt`` (a Docker-host concession, K-3) is
    not read: a Guaranteed pod cannot be "uncapped" (addendum A7)."""
    resources = {"cpu": f"{round(spec.cpu * 1000)}m", "memory": f"{spec.memory_mb}Mi"}
    if spec.gpu_count > 0:
        gpu_resources = [
            name for name, source in block.extended_resources.items() if source == "gpu_count"
        ]
        if not gpu_resources:
            profile_id = spec.profile.id if spec.profile is not None else "unknown"
            raise HardwareUnavailable(
                f"profile {profile_id!r} asks for GPUs but has no "
                "backend.kubernetes.extended_resources entry for gpu_count"
            )
        for name in gpu_resources:
            resources[name] = str(spec.gpu_count)
    return resources


def _owner(name: str, owner_uid: str) -> list[dict]:
    return [{
        "apiVersion": "batch/v1", "kind": "Job", "name": name, "uid": owner_uid,
        "controller": False, "blockOwnerDeletion": False,
    }]


def build_job(spec: RunSpec, *, name: str, ctx: JobContext, pull_secret: str | None) -> dict:
    """The run's Job. Raises :class:`HardwareUnavailable` when the profile
    cannot carry the GPU count. Assumes a valid spec (``submit`` refuses an
    unpinned user image first)."""
    block = _block(spec)
    resources = _resources(spec, block)
    labels = run_labels(spec)
    priority = SCAN_PRIORITY if spec.kind == "image_scan" else spec.priority
    size = f"{spec.memory_mb}Mi"
    scratch = {"/tmp": "tmp"}
    workdir = None if spec.user_image else PLATFORM_WORKDIRS.get(spec.kind)
    if workdir:
        scratch[workdir] = "workdir"
    container: dict = {
        "name": CONTAINER,
        "image": spec.image if spec.registry_auth else rewrite_image(
            spec.image, ctx.registry_mirrors
        ),
        # The COMPLETE environment, from the run's own Secret (ADR 0013).
        "envFrom": [{"secretRef": {"name": name}}],
        "resources": {"requests": dict(resources), "limits": dict(resources)},
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "privileged": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        },
        "volumeMounts": [{"name": vol, "mountPath": path} for path, vol in scratch.items()],
    }
    if spec.entrypoint:
        container["command"] = list(spec.entrypoint)
    if spec.cmd:
        container["args"] = list(spec.cmd)
    pod: dict = {
        "restartPolicy": "Never",
        # K5-R4: the run's budget, on the pod, so a timed-out pod is kept.
        "activeDeadlineSeconds": spec.timeout_seconds,
        "terminationGracePeriodSeconds": TERMINATION_GRACE_SECONDS,
        "serviceAccountName": ctx.run_service_account,
        "automountServiceAccountToken": False,
        "enableServiceLinks": False,
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": RUN_UID,
            "runAsGroup": RUN_UID,
            "fsGroup": RUN_UID,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "containers": [container],
        # Memory-backed and sized by memory_mb: filling it is an OOM kill
        # (the run's fault), never a node eviction (K5-R13).
        "volumes": [
            {"name": vol, "emptyDir": {"medium": "Memory", "sizeLimit": size}}
            for vol in scratch.values()
        ],
    }
    if block.node_selector:
        pod["nodeSelector"] = dict(block.node_selector)
    if block.tolerations:
        pod["tolerations"] = [dict(t) for t in block.tolerations]
    if block.runtime_class:
        pod["runtimeClassName"] = block.runtime_class
    if pull_secret:
        pod["imagePullSecrets"] = [{"name": pull_secret}]
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": ctx.namespace,
            "labels": {
                **labels,
                QUEUE_LABEL: block.queue or ctx.default_queue,
                PRIORITY_LABEL: priority,
            },
        },
        "spec": {
            # The platform owns retry (outcome_transition), never the Job.
            "backoffLimit": 0,
            "completions": 1,
            "parallelism": 1,
            "activeDeadlineSeconds": spec.timeout_seconds + JOB_DEADLINE_SLACK_SECONDS,
            "template": {
                "metadata": {"labels": dict(labels),
                             "annotations": {DO_NOT_DISRUPT: "true"}},
                "spec": pod,
            },
        },
    }


def _b64(value: str | bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return base64.b64encode(raw).decode("ascii")


def build_env_secret(spec: RunSpec, *, name: str, owner_uid: str, ctx: JobContext) -> dict:
    """The run's complete environment, owned by its Job (K5-R3) so it dies
    with it. Immutable; never logged."""
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "immutable": True,
        "metadata": {
            "name": name,
            "namespace": ctx.namespace,
            "labels": run_labels(spec),
            "ownerReferences": _owner(name, owner_uid),
        },
        "data": {key: _b64(value) for key, value in spec.env.items()},
    }


def build_pull_secret(spec: RunSpec, *, name: str, owner_uid: str, ctx: JobContext
                      ) -> dict | None:
    """A ``kubernetes.io/dockerconfigjson`` Secret ``{name}-pull`` for a user
    image with a registry credential (K5-R12), owned by the Job; None
    without one."""
    auth = spec.registry_auth
    if auth is None:
        return None
    server = auth.server or spec.image.split("/", 1)[0]
    config = {"auths": {server: {
        "username": auth.username,
        "password": auth.password,
        "auth": _b64(f"{auth.username}:{auth.password}"),
    }}}
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "kubernetes.io/dockerconfigjson",
        "immutable": True,
        "metadata": {
            "name": f"{name}-pull",
            "namespace": ctx.namespace,
            "labels": run_labels(spec),
            "ownerReferences": _owner(name, owner_uid),
        },
        "data": {".dockerconfigjson": _b64(json.dumps(config))},
    }
