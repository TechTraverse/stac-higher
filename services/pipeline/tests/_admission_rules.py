"""K-6 (K6-R21): ONE table tying the runs namespace's admission policy
(``infra/kueue/common/admission-policy.yaml``, CEL) to K-5's pure rules
(``vap_job_violations`` / ``vap_pod_update_violations`` in
``tests/test_kubernetes_rbac.py``, Python).

Every CEL validation's message starts with ``[<rule id>]``. Each rule id maps
to the problem string the pure rules report for the same violation, and to a
mutation that commits it:

- ``test_kueue_manifests.py`` requires the YAML's ids, this table's ids and the
  problem strings the pure functions can return to correspond exactly, and
  that each mutation makes the pure rules report exactly its problem;
- ``test_integration_kind_kueue.py`` applies the same mutations on a real API
  server, as the pipeline's identity, and expects a 422 naming the rule id.

So a rule added on one side and not the other fails the ordinary pipeline
job, and a CEL expression that does not catch what its Python twin catches
fails the kind leg."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass

from pipeline.process.executor import RUN_ID_LABEL, RUN_KIND_LABEL
from pipeline.process.kubernetes_manifests import (
    NETWORK_LEVEL_LABEL,
    PRIORITY_LABEL,
    QUEUE_LABEL,
    SCANNER_NETWORK_LEVEL,
)

#: The policies (and their bindings, same names) in admission-policy.yaml.
JOB_POLICY = "stac-higher-runs-jobs"
POD_UPDATE_POLICY = "stac-higher-runs-pod-updates"
SECRET_POLICY = "stac-higher-runs-secrets"
POLICIES = (JOB_POLICY, POD_UPDATE_POLICY, SECRET_POLICY)


def _pod(job: dict) -> dict:
    return job["spec"]["template"]["spec"]


def _set(path: tuple[str, ...], value: object) -> Callable[[dict], None]:
    def mutate(obj: dict) -> None:
        target = obj
        for key in path[:-1]:
            target = target.setdefault(key, {})
        target[path[-1]] = copy.deepcopy(value)
    return mutate


@dataclass(frozen=True)
class JobRule:
    """A rule on Job CREATE. ``mutate`` takes a Job the executor built and
    breaks exactly this rule. ``api_may_answer_first``: the API server's own
    validation refuses the same Job before admission runs (422 either way)."""

    rule_id: str
    problem: str
    mutate: Callable[[dict], None]
    api_may_answer_first: bool = False
    #: Other rules the same violation also breaks, as (rule id, problem), in
    #: the policy's order (the API server reports the FIRST failing rule).
    also: tuple[tuple[str, str], ...] = ()
    #: The mutation names an owner Job (OWNER_NAME / OWNER_UID placeholders,
    #: replaced by a real run Job's on kind).
    owner: bool = False

    @property
    def problems(self) -> list[str]:
        return [self.problem, *(problem for _id, problem in self.also)]

    @property
    def rule_ids(self) -> list[str]:
        return [self.rule_id, *(rule_id for rule_id, _problem in self.also)]


OWNER_NAME = "OWNER-JOB-NAME"
OWNER_UID = "00000000-0000-4000-8000-0000000000ff"
_SUSPENDED = ("suspended", "suspended")


def _owner(controller: bool) -> Callable[[dict], None]:
    def mutate(job: dict) -> None:
        job["metadata"]["ownerReferences"] = [{
            "apiVersion": "batch/v1", "kind": "Job", "name": OWNER_NAME, "uid": OWNER_UID,
            "controller": controller, "blockOwnerDeletion": False,
        }]
    return mutate


def _memory_without_size(job: dict) -> None:
    del _pod(job)["volumes"][0]["emptyDir"]["sizeLimit"]


def _disk_backed_scratch(job: dict) -> None:
    del _pod(job)["volumes"][0]["emptyDir"]["medium"]  # node disk, not memory


def _liveness_probe(job: dict) -> None:
    # A kubelet-run request from the node, outside every NetworkPolicy.
    _pod(job)["containers"][0]["livenessProbe"] = {
        "httpGet": {"host": "169.254.169.254", "path": "/", "port": 80}}


def _pre_stop_hook(job: dict) -> None:
    _pod(job)["containers"][0]["lifecycle"] = {
        "preStop": {"tcpSocket": {"host": "10.0.0.1", "port": 5432}}}


def _over_long_job_deadline(job: dict) -> None:
    job["spec"]["activeDeadlineSeconds"] = 10 * 86_400


def _long_grace(job: dict) -> None:
    _pod(job)["terminationGracePeriodSeconds"] = 3600


def _prebuilt_workload(job: dict) -> None:
    # Bind the Job to an existing Workload instead of getting its own.
    job["metadata"]["labels"]["kueue.x-k8s.io/prebuilt-workload-name"] = "someone-elses"


def _kueue_template_annotation(job: dict) -> None:
    job["spec"]["template"]["metadata"]["annotations"][
        "kueue.x-k8s.io/workload-allowed-resource-flavors"] = "any-flavor"


def _provreq_annotation(job: dict) -> None:
    # A sibling Kueue domain.
    job["metadata"]["annotations"] = {"provreq.kueue.x-k8s.io/maxRunDurationSeconds": "60"}


def _drop_queue_label(job: dict) -> None:
    del job["metadata"]["labels"][QUEUE_LABEL]


def _unknown_priority(job: dict) -> None:
    job["metadata"]["labels"][PRIORITY_LABEL] = "urgent"


def _scanner_process_run(job: dict) -> None:
    # A process Job (stac-run-…) whose pods would get the scanner's egress;
    # the Job's own labels say the same, so only the pair rule is broken.
    for labels in (job["metadata"]["labels"], job["spec"]["template"]["metadata"]["labels"]):
        labels[NETWORK_LEVEL_LABEL] = SCANNER_NETWORK_LEVEL


def _scan_shaped_process_job(job: dict) -> None:
    # A stac-run-… Job dressed as a scan: the pair is allowed, the name is not.
    for labels in (job["metadata"]["labels"], job["spec"]["template"]["metadata"]["labels"]):
        labels[RUN_KIND_LABEL] = "image_scan"
        labels[NETWORK_LEVEL_LABEL] = SCANNER_NETWORK_LEVEL


def _template_says_otherwise(job: dict) -> None:
    # The Job's metadata names one run, its pods another.
    job["spec"]["template"]["metadata"]["labels"][RUN_ID_LABEL] = (
        "00000000-0000-4000-8000-000000000000"
    )


def _projected_token_volume(job: dict) -> None:
    # A ServiceAccount token mounted by hand, automount or not.
    _pod(job)["volumes"].append({"name": "token", "projected": {"sources": [
        {"serviceAccountToken": {"path": "token", "expirationSeconds": 3600}}]}})


def _config_map_env_from(job: dict) -> None:
    _pod(job)["containers"][0]["envFrom"].append({"configMapRef": {"name": "any-config"}})


def _prefixed_env_from(job: dict) -> None:
    _pod(job)["containers"][0]["envFrom"][0]["prefix"] = "OTHER_"


def _secret_key_env(job: dict) -> None:
    _pod(job)["containers"][0]["env"] = [{"name": "K6_STOLEN", "valueFrom": {
        "secretKeyRef": {"name": "another-run", "key": "API_TOKEN"}}}]


def _pinned_node(job: dict) -> None:
    _pod(job)["nodeName"] = "any-node"  # skips the scheduler, Kueue and taints


def _host_aliases(job: dict) -> None:
    _pod(job)["hostAliases"] = [{"ip": "10.0.0.1", "hostnames": ["object-store"]}]


def _under_declared_cpu(job: dict) -> None:
    resources = _pod(job)["containers"][0]["resources"]
    resources["requests"] = {**resources["requests"], "cpu": "10m"}  # Kueue counts requests


def _no_job_deadline(job: dict) -> None:
    del job["spec"]["activeDeadlineSeconds"]


def _default_service_account(job: dict) -> None:
    _pod(job)["serviceAccountName"] = "default"


def _automount_token(job: dict) -> None:
    _pod(job)["automountServiceAccountToken"] = True


def _secret_volume(job: dict) -> None:
    _pod(job)["volumes"].append({"name": "other", "secret": {"secretName": "another-run"}})


def _foreign_pull_secret(job: dict) -> None:
    _pod(job)["imagePullSecrets"] = [{"name": "another-run"}]


def _init_container(job: dict) -> None:
    init = copy.deepcopy(_pod(job)["containers"][0])
    init["name"] = "init"
    _pod(job)["initContainers"] = [init]


def _ephemeral_container(job: dict) -> None:
    run = _pod(job)["containers"][0]
    _pod(job)["ephemeralContainers"] = [{
        "name": "debug", "image": run["image"],
        "securityContext": copy.deepcopy(run["securityContext"]),
    }]


def _plain_env(job: dict) -> None:
    _pod(job)["containers"][0]["env"] = [{"name": "K6_PLAIN", "value": "visible-in-the-job"}]


def _foreign_env_from(job: dict) -> None:
    _pod(job)["containers"][0]["envFrom"].append({"secretRef": {"name": "another-run"}})


JOB_RULES: tuple[JobRule, ...] = (
    # Without a queue label Kueue does not suspend the Job either.
    JobRule("queue-label", "queue label", _drop_queue_label, also=(_SUSPENDED,)),
    JobRule("priority-class", "priority class", _unknown_priority),
    JobRule("run-labels", "run labels", _scanner_process_run),
    JobRule("run-labels", "run labels", _scan_shaped_process_job),
    JobRule("template-labels", "template labels", _template_says_otherwise),
    JobRule("service-account", "serviceAccountName", _default_service_account),
    JobRule("no-token", "automountServiceAccountToken", _automount_token),
    JobRule("volumes", "volumes", _secret_volume),
    JobRule("pull-secrets", "imagePullSecrets", _foreign_pull_secret),
    JobRule("no-init-containers", "initContainers", _init_container),
    # A pod template may not carry ephemeral containers at all; the rule is
    # defence in depth behind the API server's own validation.
    JobRule("no-ephemeral-containers", "ephemeralContainers", _ephemeral_container,
            api_may_answer_first=True),
    JobRule("volumes", "volumes", _projected_token_volume),
    JobRule("volumes", "volumes", _memory_without_size),
    JobRule("volumes", "volumes", _disk_backed_scratch),
    JobRule("no-env", "env", _plain_env),
    JobRule("no-env", "env", _secret_key_env),
    JobRule("env-from", "envFrom", _foreign_env_from),
    JobRule("env-from", "envFrom", _config_map_env_from),
    JobRule("env-from", "envFrom", _prefixed_env_from),
    JobRule("kueue-keys", "kueue keys", _prebuilt_workload),
    JobRule("kueue-keys", "kueue keys", _kueue_template_annotation),
    JobRule("kueue-keys", "kueue keys", _provreq_annotation),
    JobRule("placement", "placement", _pinned_node),
    JobRule("host", "host", _host_aliases),
    JobRule("resources", "resources", _under_declared_cpu),
    JobRule("deadlines", "deadlines", _no_job_deadline),
    JobRule("deadlines", "deadlines", _over_long_job_deadline),
    JobRule("deadlines", "deadlines", _long_grace),
    JobRule("no-probes", "probes", _liveness_probe),
    JobRule("no-probes", "probes", _pre_stop_hook),
    # Owned by a run Job but not its controller: Kueue still suspends it.
    JobRule("no-owner", "owner references", _owner(controller=False), owner=True),
    # Controlled by a run Job: Kueue leaves it unsuspended, so [suspended]
    # (earlier in the policy) is what the API server reports.
    JobRule("suspended", "suspended", _owner(controller=True), owner=True,
            also=(("no-owner", "owner references"),)),
)


@dataclass(frozen=True)
class PodUpdateRule:
    """A rule on a Pod UPDATE by the pipeline. ``mutate(pod)`` turns a copy
    of a live pod into the updated pod; ``patch(pod)`` is the merge patch the
    pipeline's identity sends on kind to the same effect."""

    rule_id: str
    problem: str
    mutate: Callable[[dict], None]
    patch: Callable[[dict], dict]
    api_may_answer_first: bool = False


#: The pure rule compares the whole spec and metadata; the CEL compares what
#: an UPDATE can change (the rest is immutable on a pod, or system-managed
#: like managedFields and resourceVersion), so both CEL groups map to it.
_ONLY_DEADLINE = "only spec.activeDeadlineSeconds may change"


def _raised_deadline(pod: dict) -> dict:
    return {"spec": {"activeDeadlineSeconds": pod["spec"]["activeDeadlineSeconds"] + 3600}}


def _scanner_label(_pod_doc: dict) -> dict:
    # Relabelling a run into the scanner's egress policy (I-184).
    return {"metadata": {"labels": {"stac-higher.network-level": "scanner"}}}


def _extra_toleration(pod: dict) -> dict:
    # Additions are the only toleration change the API server allows, so the
    # whole list is resent with one more entry (a merge patch replaces lists).
    tolerations = copy.deepcopy(pod["spec"].get("tolerations") or [])
    tolerations.append({"key": "k6.example/any", "operator": "Exists", "effect": "NoSchedule"})
    return {"spec": {"tolerations": tolerations}}


def _apply_merge_patch(patch_for: Callable[[dict], dict]) -> Callable[[dict], None]:
    def mutate(pod: dict) -> None:
        patch = patch_for(pod)
        for section, values in patch.items():
            target = pod.setdefault(section, {})
            for key, value in values.items():
                if isinstance(value, dict) and isinstance(target.get(key), dict):
                    target[key] = {**target[key], **copy.deepcopy(value)}
                else:
                    target[key] = copy.deepcopy(value)
    return mutate


POD_UPDATE_RULES: tuple[PodUpdateRule, ...] = (
    # The API server refuses a raised deadline itself ("must be less than or
    # equal to previous value"); the CEL rule is defence in depth.
    PodUpdateRule("pod-update-deadline", "the deadline may only be lowered",
                  _apply_merge_patch(_raised_deadline), _raised_deadline,
                  api_may_answer_first=True),
    PodUpdateRule("pod-update-metadata", _ONLY_DEADLINE,
                  _apply_merge_patch(_scanner_label), _scanner_label),
    PodUpdateRule("pod-update-spec", _ONLY_DEADLINE,
                  _apply_merge_patch(_extra_toleration), _extra_toleration),
)

@dataclass(frozen=True)
class SecretRule:
    """A rule on Secret CREATE. ``mutate`` takes a Secret the executor built."""

    rule_id: str
    problem: str
    mutate: Callable[[dict], None]


def _token_secret(secret: dict) -> None:
    secret["type"] = "kubernetes.io/service-account-token"
    secret["metadata"]["annotations"] = {"kubernetes.io/service-account.name": "stac-higher-run"}
    secret.pop("data", None)
    secret.pop("immutable", None)


SECRET_RULES: tuple[SecretRule, ...] = (
    SecretRule("secret-type", "secret type", _token_secret),
)

RULE_IDS: frozenset[str] = frozenset(
    [i for r in JOB_RULES for i in r.rule_ids] + [r.rule_id for r in POD_UPDATE_RULES]
    + [r.rule_id for r in SECRET_RULES]
)


def rule_id_of(message: str) -> str | None:
    """``queue-label`` from ``"[queue-label] Jobs in this namespace …"``."""
    if message.startswith("[") and "]" in message:
        return message[1:message.index("]")]
    return None
