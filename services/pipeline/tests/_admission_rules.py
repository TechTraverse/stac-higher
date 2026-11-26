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

from pipeline.process.kubernetes_manifests import PRIORITY_LABEL, QUEUE_LABEL

#: The policies (and their bindings, same names) in admission-policy.yaml.
JOB_POLICY = "stac-higher-runs-jobs"
POD_UPDATE_POLICY = "stac-higher-runs-pod-updates"


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


def _drop_queue_label(job: dict) -> None:
    del job["metadata"]["labels"][QUEUE_LABEL]


def _unknown_priority(job: dict) -> None:
    job["metadata"]["labels"][PRIORITY_LABEL] = "urgent"


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
    JobRule("queue-label", "queue label", _drop_queue_label),
    JobRule("priority-class", "priority class", _unknown_priority),
    JobRule("service-account", "serviceAccountName", _default_service_account),
    JobRule("no-token", "automountServiceAccountToken", _automount_token),
    JobRule("volumes", "volumes", _secret_volume),
    JobRule("pull-secrets", "imagePullSecrets", _foreign_pull_secret),
    JobRule("no-init-containers", "initContainers", _init_container),
    # A pod template may not carry ephemeral containers at all; the rule is
    # defence in depth behind the API server's own validation.
    JobRule("no-ephemeral-containers", "ephemeralContainers", _ephemeral_container,
            api_may_answer_first=True),
    JobRule("no-env", "env", _plain_env),
    JobRule("env-from", "envFrom", _foreign_env_from),
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

RULE_IDS: frozenset[str] = frozenset(
    [r.rule_id for r in JOB_RULES] + [r.rule_id for r in POD_UPDATE_RULES]
)


def rule_id_of(message: str) -> str | None:
    """``queue-label`` from ``"[queue-label] Jobs in this namespace …"``."""
    if message.startswith("[") and "]" in message:
        return message[1:message.index("]")]
    return None
