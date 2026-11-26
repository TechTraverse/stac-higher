"""K-5 (plan Task 9): least privilege, proven both ways.

Every executor test runs against a fake API server that refuses whatever
``infra/kubernetes/pipeline-role.yaml`` does not grant -- that is
sufficiency. Here one lifecycle must exercise EVERY grant (necessity), and
the manifests are pinned to the settings, the chart's identities and Pod
Security ``restricted``."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path

import pytest
import yaml

from _fake_kube import ROLE_PATH, load_role_rules
from _kube_executor_fixtures import DIGEST, NAME, NOW, spec
from pipeline.config import (
    DEFAULT_PROCESS_K8S_NAMESPACE,
    DEFAULT_PROCESS_K8S_RUN_SERVICE_ACCOUNT,
)
from pipeline.process import kubernetes_executor as kx
from pipeline.process import kubernetes_manifests as km
from pipeline.process.config import MAX_TIMEOUT_SECONDS
from pipeline.process.executor import RegistryAuth, RunHandle

INFRA = ROLE_PATH.parent
VALUES = INFRA.parent / "helm" / "stac-higher" / "values.yaml"
RUN2 = "1c0a9b63-4d2e-4f60-9bac-1d2e3f4a5b6c"


def _doc(name: str) -> dict:
    return yaml.safe_load((INFRA / name).read_text())


@pytest.fixture(autouse=True)
def _fresh_isolation_cache():
    kx.reset_isolation_cache()
    yield
    kx.reset_isolation_cache()


async def test_one_lifecycle_uses_every_grant_and_nothing_else(executor, fake):
    handle = RunHandle(NAME, "kubernetes")
    await asyncio.to_thread(executor.probe)
    auth = RegistryAuth(username="u", password="p", server="ghcr.io")
    await asyncio.to_thread(executor.submit, spec(
        image=f"ghcr.io/acme/private@{DIGEST}", user_image=True, registry_auth=auth,
    ))
    assert (await asyncio.to_thread(executor.status, handle)).phase == "pending_capacity"
    fake.set_job_status(NAME, suspend=False, active=1, ready=1)
    fake.add_pod(NAME, phase="Running", start_time=NOW, state={"running": {}}, log=b"hi")
    assert (await asyncio.to_thread(executor.status, handle)).phase == "running"

    events = []
    agen = executor.watch()

    async def consume():
        async for event in agen:
            events.append(event)

    task = asyncio.create_task(consume())
    while fake.open_watches < 2:
        await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.to_thread(executor.cancel, handle)  # patches the running pod
    assert await asyncio.to_thread(executor.logs, handle, 100) == b"hi"
    await asyncio.to_thread(executor.reap, handle)
    await asyncio.to_thread(executor.list_launched)
    executor._suffix = lambda: "ffffff"
    await asyncio.to_thread(executor.submit, spec(run_id=RUN2))
    await asyncio.to_thread(executor.cancel, RunHandle(f"stac-run-{RUN2}-ffffff", "kubernetes"))

    assert fake.used == fake.granted == load_role_rules()


def test_the_role_is_namespaced_and_has_no_wildcards():
    role = _doc("pipeline-role.yaml")
    assert role["kind"] == "Role"
    assert role["metadata"]["namespace"] == DEFAULT_PROCESS_K8S_NAMESPACE
    for rule in role["rules"]:
        for key in ("apiGroups", "resources", "verbs"):
            assert "*" not in rule[key]
    granted = load_role_rules()
    for verb in ("get", "list", "watch", "update", "patch"):
        assert ("", "secrets", verb) not in granted
    assert {v for g, r, v in granted if r == "networkpolicies"} == {"get"}


def test_the_manifests_agree_with_the_settings_and_the_chart():
    namespace = _doc("namespace.yaml")
    assert namespace["kind"] == "Namespace"
    assert namespace["metadata"]["name"] == DEFAULT_PROCESS_K8S_NAMESPACE
    labels = namespace["metadata"]["labels"]
    assert labels["pod-security.kubernetes.io/enforce"] == "restricted"

    account = _doc("run-serviceaccount.yaml")
    assert account["metadata"]["name"] == DEFAULT_PROCESS_K8S_RUN_SERVICE_ACCOUNT
    assert account["metadata"]["namespace"] == DEFAULT_PROCESS_K8S_NAMESPACE
    assert account["automountServiceAccountToken"] is False
    assert "eks.amazonaws.com/role-arn" not in (account["metadata"].get("annotations") or {})

    values = yaml.safe_load(VALUES.read_text())
    binding = _doc("pipeline-rolebinding.yaml")
    assert binding["metadata"]["namespace"] == DEFAULT_PROCESS_K8S_NAMESPACE
    assert binding["roleRef"] == {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role",
                                  "name": _doc("pipeline-role.yaml")["metadata"]["name"]}
    assert binding["subjects"] == [{
        "kind": "ServiceAccount",
        "name": values["identity"]["serviceAccounts"]["pipeline"],
        "namespace": values["identity"]["namespace"],
    }]
    assert values["processRuns"]["namespace"] == DEFAULT_PROCESS_K8S_NAMESPACE


def _restricted_violations(pod: dict) -> list[str]:
    """Pod Security `restricted` (v1.34) checked in code, so a manifest change
    the namespace would reject fails here, not on a cluster."""
    problems = []
    for key in ("hostNetwork", "hostPID", "hostIPC"):
        if pod.get(key):
            problems.append(key)
    psc = pod.get("securityContext") or {}
    for volume in pod.get("volumes") or []:
        if set(volume) - {"name", "emptyDir", "configMap", "secret", "projected",
                          "downwardAPI", "persistentVolumeClaim", "ephemeral", "csi"}:
            problems.append(f"volume {volume['name']}")
    for container in pod["containers"]:
        csc = container.get("securityContext") or {}
        if csc.get("privileged"):
            problems.append("privileged")
        if csc.get("allowPrivilegeEscalation") is not False:
            problems.append("allowPrivilegeEscalation")
        if (csc.get("capabilities") or {}).get("drop") != ["ALL"]:
            problems.append("capabilities.drop")
        if set((csc.get("capabilities") or {}).get("add") or []) - {"NET_BIND_SERVICE"}:
            problems.append("capabilities.add")
        if not (csc.get("runAsNonRoot") or psc.get("runAsNonRoot")):
            problems.append("runAsNonRoot")
        if csc.get("runAsUser", psc.get("runAsUser")) == 0:
            problems.append("runAsUser 0")
        seccomp = (csc.get("seccompProfile") or psc.get("seccompProfile") or {}).get("type")
        if seccomp not in ("RuntimeDefault", "Localhost"):
            problems.append("seccompProfile")
        if any(p.get("hostPort") for p in container.get("ports") or []):
            problems.append("hostPort")
    return problems


@pytest.mark.parametrize(
    "overrides",
    [{}, {"user_image": True}, {"kind": "image_scan", "profile": None}, {"gpu_count": 0}],
)
def test_a_built_pod_satisfies_pod_security_restricted(overrides):
    ctx = km.JobContext("stac-higher-runs", "process-runs", "stac-higher-run", {})
    job = km.build_job(spec(**overrides), name=NAME, ctx=ctx, pull_secret=None)
    assert _restricted_violations(job["spec"]["template"]["spec"]) == []


def test_the_restricted_check_catches_a_violation():
    ctx = km.JobContext("stac-higher-runs", "process-runs", "stac-higher-run", {})
    pod = km.build_job(spec(), name=NAME, ctx=ctx, pull_secret=None)["spec"]["template"]["spec"]
    pod["hostNetwork"] = True
    pod["containers"][0]["securityContext"]["allowPrivilegeEscalation"] = True
    pod["volumes"].append({"name": "h", "hostPath": {"path": "/"}})
    assert set(_restricted_violations(pod)) == {"hostNetwork", "allowPrivilegeEscalation",
                                               "volume h"}


def test_the_readme_names_every_manifest():
    readme = (INFRA / "README.md").read_text()
    for path in sorted(Path(INFRA).glob("*.yaml")):
        assert path.name in readme


# -- review D2: the admission policy K-6 renders, encoded against build_job -----
#
# A compromised pipeline identity holds `jobs create`, `pods patch` and
# `secrets create` in the runs namespace. K-6's ValidatingAdmissionPolicy
# (#14) bounds what it can do with them; these are its rules, so every Job
# the executor builds is proven to pass them before a cluster sees one.

PRIORITIES = {"interactive", "triggered"}


#: K-6 lead ruling: the only (run-kind, network-level) pairs a pod template
#: may carry, by the Job's name prefix (the NetworkPolicies select on both).
SCAN_PAIR = ("image_scan", km.SCANNER_NETWORK_LEVEL)
PROCESS_PAIR = ("process", km.ENFORCEABLE_NETWORK_LEVELS[0])
#: The run labels a pod template must repeat from its Job.
TEMPLATE_RUN_LABELS = (km.RUN_KIND_LABEL, km.NETWORK_LEVEL_LABEL, km.RUN_ID_LABEL)


#: K-6 review R2: pod-template fields build_job never sets, mapped to the
#: value the API server defaults them to (None: no default, must be absent).
#: Placement and priority (scheduler, Kueue flavor and taint bypass) ...
PLACEMENT_FIELDS = {
    "nodeName": None, "priorityClassName": None, "priority": None,
    "preemptionPolicy": "PreemptLowerPriority", "affinity": None,
    "topologySpreadConstraints": None, "schedulingGates": None, "overhead": None,
    "readinessGates": None, "resourceClaims": None, "resources": None,
    "schedulerName": "default-scheduler",
}
#: ... and the host's network, names and DNS.
HOST_FIELDS = {
    "hostNetwork": False, "hostPID": False, "hostIPC": False, "hostAliases": None,
    "hostname": None, "hostnameOverride": None, "subdomain": None, "setHostnameAsFQDN": False,
    "shareProcessNamespace": False, "dnsPolicy": "ClusterFirst", "dnsConfig": None,
}


def _set_beyond_default(pod: dict, fields: dict) -> bool:
    return any(key in pod and pod[key] not in (None, default) for key, default in fields.items())


def _requests_equal_limits(container: dict) -> bool:
    """Kueue counts requests: every container must state cpu and memory
    limits and request exactly them (absent requests default to the limits)."""
    resources = container.get("resources") or {}
    limits = resources.get("limits") or {}
    requests = resources.get("requests", limits)
    return {"cpu", "memory"} <= set(limits) and requests == limits


#: K-6 re-review N2: the deadlines a run Job may carry. A run's timeout is at
#: most MAX_TIMEOUT_SECONDS (the app's TIMEOUT_SECONDS_MAX; the scan policy's
#: own cap is the same); the Job's deadline adds the executor's slack; a pod
#: ignoring SIGTERM is killed within the grace (the executor sets 10 s).
MAX_POD_DEADLINE_SECONDS = MAX_TIMEOUT_SECONDS
MAX_JOB_DEADLINE_SECONDS = MAX_TIMEOUT_SECONDS + km.JOB_DEADLINE_SLACK_SECONDS
MAX_GRACE_SECONDS = 30
#: N4: kubelet-run probes and hooks (an httpGet/tcpSocket with a host is a
#: request from the node, outside every NetworkPolicy).
PROBE_FIELDS = ("livenessProbe", "readinessProbe", "startupProbe", "lifecycle")


def kueue_mutated(job: dict) -> dict:
    """The Job as validating admission sees it. Kueue's mutating job webhook
    (mjob, v0.20.8 ``ApplyDefaultForSuspend``) runs in the mutating phase,
    before every validating policy, and suspends a Job with a queue label --
    unless its controller owner is a Job Kueue manages
    (``WorkloadShouldBeSuspended``). The model treats any controller owner as
    Kueue-managed (in the tests it is a run Job)."""
    job = copy.deepcopy(job)
    labels = job["metadata"].get("labels") or {}
    owned = any(o.get("controller") for o in job["metadata"].get("ownerReferences") or [])
    if labels.get("kueue.x-k8s.io/queue-name") and not owned:
        job["spec"]["suspend"] = True
    return job


def _disk_safe(volume: dict) -> bool:
    """N3: memory-backed and sized, as the executor builds it."""
    empty_dir = volume.get("emptyDir")
    return (set(volume) == {"name", "emptyDir"} and isinstance(empty_dir, dict)
            and empty_dir.get("medium") == "Memory" and "sizeLimit" in empty_dir)


def vap_job_violations(job: dict) -> list[str]:
    """Job CREATE in the runs namespace."""
    problems = []
    name = job["metadata"]["name"]
    labels = job["metadata"].get("labels") or {}
    if not labels.get("kueue.x-k8s.io/queue-name"):
        problems.append("queue label")
    if labels.get("kueue.x-k8s.io/priority-class") not in PRIORITIES:
        problems.append("priority class")
    template_labels = (job["spec"]["template"].get("metadata") or {}).get("labels") or {}
    expected = SCAN_PAIR if name.startswith(km.NAME_PREFIX["image_scan"]) else PROCESS_PAIR
    pair = (template_labels.get(km.RUN_KIND_LABEL), template_labels.get(km.NETWORK_LEVEL_LABEL))
    if pair != expected:
        problems.append("run labels")
    if any(key not in labels or labels.get(key) != template_labels.get(key)
           for key in TEMPLATE_RUN_LABELS):
        problems.append("template labels")
    pod = job["spec"]["template"]["spec"]
    if pod.get("serviceAccountName") != DEFAULT_PROCESS_K8S_RUN_SERVICE_ACCOUNT:
        problems.append("serviceAccountName")
    if pod.get("automountServiceAccountToken") is not False:
        problems.append("automountServiceAccountToken")
    if not all(_disk_safe(v) for v in pod.get("volumes") or []):
        problems.append("volumes")
    for secret in pod.get("imagePullSecrets") or []:
        if secret.get("name") != f"{name}-pull":
            problems.append("imagePullSecrets")
    for key in ("initContainers", "ephemeralContainers"):
        if pod.get(key):
            problems.append(key)
    for container in pod["containers"]:
        if container.get("env"):
            problems.append("env")
        for source in container.get("envFrom") or []:
            if set(source) != {"secretRef"} or source["secretRef"].get("name") != name:
                problems.append("envFrom")
    if _set_beyond_default(pod, PLACEMENT_FIELDS):
        problems.append("placement")
    if _set_beyond_default(pod, HOST_FIELDS):
        problems.append("host")
    if not all(_requests_equal_limits(c) for c in pod["containers"]):
        problems.append("resources")
    if any(key in c for c in pod["containers"] for key in PROBE_FIELDS):
        problems.append("probes")
    job_deadline = job["spec"].get("activeDeadlineSeconds")
    pod_deadline = pod.get("activeDeadlineSeconds")
    if (job_deadline is None or job_deadline > MAX_JOB_DEADLINE_SECONDS
            or pod_deadline is None or pod_deadline > MAX_POD_DEADLINE_SECONDS
            or pod.get("terminationGracePeriodSeconds", 30) > MAX_GRACE_SECONDS):
        problems.append("deadlines")
    # N1: Kueue does not suspend (or give a Workload to) a Job whose
    # controller owner is a Job it manages; such a Job would run outside the
    # quota. The executor's Jobs have no owner and are suspended by Kueue.
    if job["spec"].get("suspend") is not True:
        problems.append("suspended")
    if job["metadata"].get("ownerReferences"):
        problems.append("owner references")
    return problems


#: K-6 review R5: the Secret types the executor creates.
SECRET_TYPES = ("Opaque", "kubernetes.io/dockerconfigjson")


def vap_secret_violations(secret: dict) -> list[str]:
    """Secret CREATE in the runs namespace: no service-account-token (or any
    other) type the cluster would fill in."""
    problems = []
    if secret.get("type", "Opaque") not in SECRET_TYPES:
        problems.append("secret type")
    return problems


def vap_pod_update_violations(old: dict, new: dict) -> list[str]:
    """Pod UPDATE by the pipeline's ServiceAccount: only a lowered
    spec.activeDeadlineSeconds."""
    old_spec = {k: v for k, v in old["spec"].items() if k != "activeDeadlineSeconds"}
    new_spec = {k: v for k, v in new["spec"].items() if k != "activeDeadlineSeconds"}
    problems = []
    if old_spec != new_spec or old.get("metadata") != new.get("metadata"):
        problems.append("only spec.activeDeadlineSeconds may change")
    if new["spec"].get("activeDeadlineSeconds", 0) > old["spec"].get(
        "activeDeadlineSeconds", float("inf")
    ):
        problems.append("the deadline may only be lowered")
    return problems


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"priority": "interactive"},
        {"user_image": True, "image": f"ghcr.io/acme/private@{DIGEST}",
         "registry_auth": RegistryAuth(username="u", password="p", server="ghcr.io")},
        {"kind": "image_scan", "profile": None},
    ],
)
def test_every_built_job_passes_the_admission_policy(overrides):
    ctx = km.JobContext("stac-higher-runs", "process-runs", "stac-higher-run", {})
    run = spec(**overrides)
    name = km.job_name(run.kind, run.run_id, "a1b2c3")  # the executor's own naming
    pull = f"{name}-pull" if run.registry_auth else None
    job = km.build_job(run, name=name, ctx=ctx, pull_secret=pull)
    assert "suspend" not in job["spec"] and "ownerReferences" not in job["metadata"]
    assert vap_job_violations(kueue_mutated(job)) == []


def test_the_admission_rules_catch_what_they_exist_for():
    ctx = km.JobContext("stac-higher-runs", "process-runs", "stac-higher-run", {})
    job = km.build_job(spec(), name=NAME, ctx=ctx, pull_secret=None)
    pod = job["spec"]["template"]["spec"]
    pod["serviceAccountName"] = "default"
    pod["volumes"].append({"name": "s", "secret": {"secretName": "another-run"}})
    pod["containers"][0]["envFrom"].append({"secretRef": {"name": "another-run"}})
    del job["metadata"]["labels"]["kueue.x-k8s.io/queue-name"]
    # Without a queue label Kueue does not suspend the Job either.
    assert set(vap_job_violations(kueue_mutated(job))) == {
        "serviceAccountName", "volumes", "envFrom", "queue label", "suspended"}


def test_the_executors_only_pod_update_passes_the_admission_policy():
    ctx = km.JobContext("stac-higher-runs", "process-runs", "stac-higher-run", {})
    pod_spec = km.build_job(spec(), name=NAME, ctx=ctx, pull_secret=None)["spec"]["template"]
    old = {"metadata": {"name": "p"}, "spec": pod_spec["spec"]}
    patched = {"metadata": {"name": "p"},
               "spec": {**pod_spec["spec"],
                        "activeDeadlineSeconds": kx.CANCEL_POD_DEADLINE_SECONDS}}
    assert vap_pod_update_violations(old, patched) == []
    swapped = {"metadata": {"name": "p"}, "spec": {**pod_spec["spec"], "containers": []}}
    assert vap_pod_update_violations(old, swapped)


def test_every_secret_the_executor_builds_passes_the_admission_policy():
    ctx = km.JobContext("stac-higher-runs", "process-runs", "stac-higher-run", {})
    run = spec(image=f"ghcr.io/acme/private@{DIGEST}", user_image=True,
               registry_auth=RegistryAuth(username="u", password="p", server="ghcr.io"))
    secrets = [km.build_env_secret(run, name=NAME, owner_uid="u", ctx=ctx),
               km.build_pull_secret(run, name=NAME, owner_uid="u", ctx=ctx)]
    assert [vap_secret_violations(s) for s in secrets] == [[], []]
    assert vap_secret_violations({"type": "kubernetes.io/service-account-token"})


def test_the_deadline_bounds_admit_the_longest_run_the_platform_allows():
    ctx = km.JobContext("stac-higher-runs", "process-runs", "stac-higher-run", {})
    job = km.build_job(spec(timeout_seconds=MAX_TIMEOUT_SECONDS), name=NAME, ctx=ctx,
                       pull_secret=None)
    assert km.TERMINATION_GRACE_SECONDS <= MAX_GRACE_SECONDS
    assert vap_job_violations(kueue_mutated(job)) == []
    job["spec"]["template"]["spec"]["activeDeadlineSeconds"] += 1
    assert vap_job_violations(kueue_mutated(job)) == ["deadlines"]
