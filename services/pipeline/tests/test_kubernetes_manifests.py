"""K-5 (plan Task 4): the Job, env Secret and pull Secret as pure functions of
the RunSpec, the profile and the settings — the security-relevant shape
pinned by plain dict assertions, no I/O."""

from __future__ import annotations

import base64
import json
import logging

import pytest

import _fake_kube
from pipeline.process import kubernetes_manifests as km
from pipeline.process.executor import (
    HardwareUnavailable,
    RegistryAuth,
    RunSpec,
)
from pipeline.process.hardware import parse_hardware_profiles

RUN = "0b9f8a52-3c1d-4e5f-8a9b-0c1d2e3f4a5b"
SCAN = "7c6d5e4f-3a2b-4c1d-9e8f-7a6b5c4d3e2f"
PROCESS = "11111111-2222-3333-4444-555555555555"
NAME = f"stac-run-{RUN}-a1b2c3"
UID = "d0e1f2a3-b4c5-d6e7-f8a9-b0c1d2e3f4a5"
DIGEST = "sha256:" + "a" * 64
RUNTIME = f"ghcr.io/techtraverse/stac-higher-process-runtime@{DIGEST}"
MIRROR_GHCR = "123.dkr.ecr.us-east-1.amazonaws.com/ghcr"
MIRROR_HUB = "123.dkr.ecr.us-east-1.amazonaws.com/docker-hub"
CTX = km.JobContext(
    namespace="stac-higher-runs",
    default_queue="process-runs",
    run_service_account="stac-higher-run",
    registry_mirrors={"ghcr.io": MIRROR_GHCR, "docker.io": MIRROR_HUB},
)
ENV = {"API_TOKEN": "tok-SENTINEL", "AWS_SECRET_ACCESS_KEY": "s3cr3t-SENTINEL", "EMPTY": ""}
GPU_BLOCK = {
    "queue": "gpu-queue",
    "node_selector": {"stac-higher.io/flavor": "gpu-l4"},
    "tolerations": [{"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}],
    "runtime_class": "nvidia",
    "extended_resources": {"nvidia.com/gpu": "gpu_count"},
}


def _profile(kubernetes=None):
    doc = {
        "version": 1,
        "profiles": [{
            "id": "standard", "label": "S", "description": "", "tier": "gpu",
            "accelerator": {"vendor": "nvidia"}, "cpu": {"min": 1, "max": 8, "default": 1},
            "memory_mb": {"min": 128, "max": 32768, "default": 512},
            "gpu_count": {"min": 0, "max": 1, "default": 0},
            "max_queue_wait_seconds": 60, "image": None,
            "backend": {"kubernetes": kubernetes} if kubernetes is not None else {},
        }],
    }
    return parse_hardware_profiles(doc).standard


def _spec(**overrides) -> RunSpec:
    base = {
        "run_id": RUN, "process_id": PROCESS, "image": RUNTIME, "env": dict(ENV),
        "memory_mb": 1024, "timeout_seconds": 900, "cpu": 1.0, "profile": _profile(GPU_BLOCK),
    }
    return RunSpec(**{**base, **overrides})


def _pod(job: dict) -> dict:
    return job["spec"]["template"]["spec"]


def _container(job: dict) -> dict:
    (container,) = _pod(job)["containers"]
    return container


def test_job_names_fit_and_round_trip():
    assert km.job_name("process", RUN, "a1b2c3") == NAME
    assert len(NAME) <= 52  # + "-xxxxx" for the pod, "job-" + "-xxxxx" for the Workload
    assert len(f"job-{NAME}-xxxxx") <= 63
    scan = km.job_name("image_scan", SCAN, "ffffff")
    assert scan == f"stac-scan-{SCAN}-ffffff" and len(scan) <= 63
    assert km.parse_job_name(NAME) == ("process", RUN)
    assert km.parse_job_name(scan) == ("image_scan", SCAN)
    for stranger in ("coredns", "stac-run-", "stac-run-x-a1b2c3", f"stac-run-{RUN}", 3, None):
        assert km.parse_job_name(stranger) is None


def test_rewrite_image_swaps_the_host_only():
    m = CTX.registry_mirrors
    assert km.rewrite_image(RUNTIME, m) == (
        f"{MIRROR_GHCR}/techtraverse/stac-higher-process-runtime@{DIGEST}"
    )
    assert km.rewrite_image(f"docker.io/library/python@{DIGEST}", m) == (
        f"{MIRROR_HUB}/library/python@{DIGEST}"
    )
    assert km.rewrite_image("ghcr.io/a/b:1.2", m) == f"{MIRROR_GHCR}/a/b:1.2"
    assert km.rewrite_image("quay.io/x/y:1", m) == "quay.io/x/y:1"
    assert km.rewrite_image("stac-higher-process-runtime:dev", m) == (
        "stac-higher-process-runtime:dev"
    )
    assert km.rewrite_image("library/python:3", m) == "library/python:3"
    assert km.rewrite_image("localhost:5000/x:1", m) == "localhost:5000/x:1"
    assert km.rewrite_image("localhost:5000/x:1", {"localhost:5000": "m.test/l"}) == (
        "m.test/l/x:1"
    )
    assert km.rewrite_image("ghcr.io.evil.test/x:1", m) == "ghcr.io.evil.test/x:1"
    assert km.rewrite_image(RUNTIME, {}) == RUNTIME


def test_the_job_is_kueue_labelled_and_never_retried():
    job = km.build_job(_spec(priority="interactive"), name=NAME, ctx=CTX, pull_secret=None)
    labels = job["metadata"]["labels"]
    assert labels["stac-higher.run-id"] == RUN
    assert labels["stac-higher.run-kind"] == "process"
    assert labels["stac-higher.process-id"] == PROCESS
    assert labels[km.QUEUE_LABEL] == "gpu-queue"
    assert labels[km.PRIORITY_LABEL] == "interactive"
    assert job["metadata"]["namespace"] == "stac-higher-runs"
    assert job["metadata"]["name"] == NAME
    spec = job["spec"]
    assert spec["backoffLimit"] == 0
    assert spec["completions"] == spec["parallelism"] == 1
    assert spec["activeDeadlineSeconds"] == 900 + km.JOB_DEADLINE_SLACK_SECONDS == 1500
    for absent in ("ttlSecondsAfterFinished", "suspend", "podFailurePolicy"):
        assert absent not in spec
    # No profile, or a profile without a queue: the deployment's default.
    for spec_ in (_spec(profile=None), _spec(profile=_profile({}))):
        job = km.build_job(spec_, name=NAME, ctx=CTX, pull_secret=None)
        assert job["metadata"]["labels"][km.QUEUE_LABEL] == "process-runs"
        assert job["metadata"]["labels"][km.PRIORITY_LABEL] == "triggered"


def test_the_pod_deadline_is_the_run_timeout():
    pod = _pod(km.build_job(_spec(timeout_seconds=123), name=NAME, ctx=CTX, pull_secret=None))
    assert pod["activeDeadlineSeconds"] == 123
    assert pod["terminationGracePeriodSeconds"] == km.TERMINATION_GRACE_SECONDS == 10
    assert pod["restartPolicy"] == "Never"


def test_the_pod_is_locked_down():
    job = km.build_job(_spec(), name=NAME, ctx=CTX, pull_secret=None)
    pod = _pod(job)
    assert pod["securityContext"] == {
        "runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001, "fsGroup": 10001,
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    assert _container(job)["securityContext"] == {
        "allowPrivilegeEscalation": False, "privileged": False,
        "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]},
    }
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert pod["serviceAccountName"] == "stac-higher-run"
    assert job["spec"]["template"]["metadata"]["annotations"] == {
        "karpenter.sh/do-not-disrupt": "true"
    }
    assert job["spec"]["template"]["metadata"]["labels"] == km.run_labels(_spec())
    text = json.dumps(job)
    for forbidden in ('"hostNetwork"', '"hostPID"', '"hostIPC"', '"hostPath"', '"hostPort"'):
        assert forbidden not in text
    assert '"privileged": true' not in text


def test_the_job_carries_no_environment():
    job = km.build_job(_spec(), name=NAME, ctx=CTX, pull_secret=None)
    text = json.dumps(job)
    for value in ENV.values():
        if value:
            assert value not in text
    assert '"env":' not in text
    assert _container(job)["envFrom"] == [{"secretRef": {"name": NAME}}]


def test_requests_equal_limits():
    job = km.build_job(
        _spec(cpu=2.5, memory_mb=16384, gpu_count=1), name=NAME, ctx=CTX, pull_secret=None
    )
    resources = _container(job)["resources"]
    want = {"cpu": "2500m", "memory": "16384Mi", "nvidia.com/gpu": "1"}
    assert resources == {"requests": want, "limits": want}
    no_gpu = _container(km.build_job(_spec(cpu=0.25), name=NAME, ctx=CTX, pull_secret=None))
    assert no_gpu["resources"]["limits"] == {"cpu": "250m", "memory": "1024Mi"}
    with pytest.raises(HardwareUnavailable, match="'standard'"):
        km.build_job(_spec(gpu_count=1, profile=_profile({})), name=NAME, ctx=CTX,
                     pull_secret=None)
    with pytest.raises(HardwareUnavailable, match="'unknown'"):
        km.build_job(_spec(gpu_count=1, profile=None), name=NAME, ctx=CTX, pull_secret=None)


def test_an_extractor_exempt_from_the_cpu_cap_on_docker_is_still_sized_here():
    """Addendum A7: requests == limits on Kubernetes; cpu_exempt is not read."""
    job = km.build_job(_spec(cpu_exempt=True), name=NAME, ctx=CTX, pull_secret=None)
    assert _container(job)["resources"]["limits"]["cpu"] == "1000m"


def test_placement_comes_from_the_profile():
    pod = _pod(km.build_job(_spec(), name=NAME, ctx=CTX, pull_secret=None))
    assert pod["nodeSelector"] == {"stac-higher.io/flavor": "gpu-l4"}
    assert pod["tolerations"] == [
        {"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}
    ]
    assert pod["runtimeClassName"] == "nvidia"
    plain = _pod(km.build_job(_spec(profile=_profile({})), name=NAME, ctx=CTX, pull_secret=None))
    for absent in ("nodeSelector", "tolerations", "runtimeClassName", "imagePullSecrets"):
        assert absent not in plain


def _mounts(job):
    pod = _pod(job)
    volumes = {v["name"]: v for v in pod["volumes"]}
    return {
        m["mountPath"]: volumes[m["name"]]["emptyDir"]
        for m in _container(job)["volumeMounts"]
    }


def test_scratch_is_memory_backed():
    memory = {"medium": "Memory", "sizeLimit": "1024Mi"}
    platform = km.build_job(_spec(), name=NAME, ctx=CTX, pull_secret=None)
    assert _mounts(platform) == {"/tmp": memory, "/home/runner": memory}
    scan = km.build_job(_spec(kind="image_scan", run_id=SCAN), name=NAME, ctx=CTX,
                        pull_secret=None)
    assert _mounts(scan) == {"/tmp": memory, "/home/scanner": memory}
    user = km.build_job(_spec(user_image=True), name=NAME, ctx=CTX, pull_secret=None)
    assert _mounts(user) == {"/tmp": memory}
    for job in (platform, scan, user):
        assert all(set(v) == {"name", "emptyDir"} for v in _pod(job)["volumes"])


def test_entrypoint_and_cmd_map_to_command_and_args():
    container = _container(km.build_job(
        _spec(entrypoint=("python", "-c"), cmd=("print(1)",)), name=NAME, ctx=CTX,
        pull_secret=None,
    ))
    assert container["command"] == ["python", "-c"] and container["args"] == ["print(1)"]
    bare = _container(km.build_job(_spec(), name=NAME, ctx=CTX, pull_secret=None))
    assert "command" not in bare and "args" not in bare
    assert bare["name"] == km.CONTAINER == "run"


def test_the_image_is_rewritten_unless_it_has_a_credential():
    public = f"docker.io/library/python@{DIGEST}"
    job = km.build_job(_spec(image=public, user_image=True), name=NAME, ctx=CTX,
                       pull_secret=None)
    assert _container(job)["image"] == f"{MIRROR_HUB}/library/python@{DIGEST}"
    private = f"ghcr.io/acme/private@{DIGEST}"
    auth = RegistryAuth(username="u", password="p", server="ghcr.io")
    job = km.build_job(_spec(image=private, user_image=True, registry_auth=auth), name=NAME,
                       ctx=CTX, pull_secret=f"{NAME}-pull")
    assert _container(job)["image"] == private
    assert _pod(job)["imagePullSecrets"] == [{"name": f"{NAME}-pull"}]
    platform = km.build_job(_spec(), name=NAME, ctx=CTX, pull_secret=None)
    assert _container(platform)["image"].startswith(MIRROR_GHCR + "/")


def test_a_scan_job():
    spec = _spec(kind="image_scan", run_id=SCAN, process_id="img-1", profile=None,
                 image="stac-higher-image-scanner:local", priority="interactive")
    name = km.job_name("image_scan", SCAN, "ffffff")
    job = km.build_job(spec, name=name, ctx=CTX, pull_secret=None)
    labels = job["metadata"]["labels"]
    assert name.startswith("stac-scan-")
    assert labels["stac-higher.run-kind"] == "image_scan"
    assert labels["stac-higher.image-id"] == "img-1"
    assert "stac-higher.process-id" not in labels
    assert labels[km.QUEUE_LABEL] == "process-runs"
    assert labels[km.PRIORITY_LABEL] == "triggered"  # lead ruling: scans are triggered
    assert _container(job)["resources"]["limits"]["cpu"] == "1000m"


def test_the_env_secret_is_owned_and_immutable():
    secret = km.build_env_secret(_spec(), name=NAME, owner_uid=UID, ctx=CTX)
    assert secret["type"] == "Opaque" and secret["immutable"] is True
    assert secret["metadata"]["name"] == NAME
    assert secret["metadata"]["namespace"] == "stac-higher-runs"
    assert secret["metadata"]["labels"] == km.run_labels(_spec())
    assert set(secret["data"]) == set(ENV)
    assert {k: base64.b64decode(v).decode() for k, v in secret["data"].items()} == ENV
    assert secret["metadata"]["ownerReferences"] == [{
        "apiVersion": "batch/v1", "kind": "Job", "name": NAME, "uid": UID,
        "controller": False, "blockOwnerDeletion": False,
    }]


def test_the_pull_secret_is_a_dockerconfigjson():
    image = f"ghcr.io/acme/private@{DIGEST}"
    for server, want in (("", "ghcr.io"), ("registry.example.test", "registry.example.test")):
        auth = RegistryAuth(username="user", password="pa:ss", server=server)
        secret = km.build_pull_secret(
            _spec(image=image, user_image=True, registry_auth=auth),
            name=NAME, owner_uid=UID, ctx=CTX,
        )
        assert secret is not None
        assert secret["type"] == "kubernetes.io/dockerconfigjson"
        assert secret["immutable"] is True
        assert secret["metadata"]["name"] == f"{NAME}-pull"
        doc = json.loads(base64.b64decode(secret["data"][".dockerconfigjson"]))
        assert doc == {"auths": {want: {
            "username": "user", "password": "pa:ss",
            "auth": base64.b64encode(b"user:pa:ss").decode(),
        }}}
        assert secret["metadata"]["ownerReferences"][0]["uid"] == UID
        assert secret["metadata"]["ownerReferences"][0]["name"] == NAME
    assert km.build_pull_secret(_spec(), name=NAME, owner_uid=UID, ctx=CTX) is None


def test_building_manifests_logs_nothing(caplog):
    auth = RegistryAuth(username="u", password="p", server="ghcr.io")
    spec = _spec(user_image=True, image=f"ghcr.io/a/b@{DIGEST}", registry_auth=auth)
    with caplog.at_level(logging.DEBUG):
        km.build_job(spec, name=NAME, ctx=CTX, pull_secret=f"{NAME}-pull")
        km.build_env_secret(spec, name=NAME, owner_uid=UID, ctx=CTX)
        km.build_pull_secret(spec, name=NAME, owner_uid=UID, ctx=CTX)
    assert caplog.records == []


def test_the_fake_serves_the_executors_kueue_version():
    assert _fake_kube.KUEUE_API_VERSION == km.KUEUE_API_VERSION
    assert _fake_kube.QUEUE_LABEL == km.QUEUE_LABEL
    assert _fake_kube.JOB_UID_LABEL == km.KUEUE_JOB_UID_LABEL
    assert _fake_kube.POD_JOB_LABEL == km.POD_JOB_LABEL
