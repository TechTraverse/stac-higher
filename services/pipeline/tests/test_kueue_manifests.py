"""K-6: static checks of infra/kueue, infra/kind, the runs namespace's
default-deny policy and infra/hardware-profiles/kind.json — no cluster.

The kind leg (`.github/workflows/kind.yml`) proves these objects on a real
API server; here they are pinned against the executor's own constants, so a
rename on either side fails in the ordinary pipeline job first."""

from __future__ import annotations

import ast
import copy
import re
from pathlib import Path

import pytest
import yaml

import _admission_rules as rules
from _kube_executor_fixtures import NAME, spec
from pipeline.config import (
    DEFAULT_PROCESS_K8S_DEFAULT_QUEUE,
    DEFAULT_PROCESS_K8S_NAMESPACE,
    DEFAULT_PROCESS_K8S_RUN_SERVICE_ACCOUNT,
)
from pipeline.process import kubernetes_executor as kx
from pipeline.process import kubernetes_manifests as km
from pipeline.process.executor import PRIORITIES
from test_kubernetes_rbac import vap_job_violations, vap_pod_update_violations

REPO = Path(__file__).resolve().parents[3]
KUEUE = REPO / "infra" / "kueue"
CONFIG = KUEUE / "config"


def _load(path: Path) -> list[dict]:
    """Every YAML document in ``path``, without the empty ones."""
    return [doc for doc in yaml.safe_load_all(path.read_text()) if doc is not None]


def _env_file(path: Path) -> dict[str, str]:
    """A ``KEY=value`` file (``#`` comments, blank lines) as bash sources it."""
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        assert sep and re.fullmatch(r"[A-Z][A-Z0-9_]*", key), f"{path.name}: bad line {line!r}"
        assert key not in values, f"{path.name}: {key} set twice"
        values[key] = value
    return values


def _duration_seconds(raw: str) -> int:
    """A Go duration as Kueue's config writes it (``30s``, ``10m``, ``1h``)."""
    match = re.fullmatch(r"(\d+)([smh])", raw)
    assert match, f"unsupported duration {raw!r}"
    return int(match.group(1)) * {"s": 1, "m": 60, "h": 3600}[match.group(2)]


# -- Task 1: the pin and the controller configuration ------------------------

#: The four keys in which the kind config may differ from production (K6-R7).
KIND_TIMING_KEYS = (
    ("waitForPodsReady", "timeout"),
    ("waitForPodsReady", "requeuingStrategy", "backoffLimitCount"),
    ("waitForPodsReady", "requeuingStrategy", "backoffBaseSeconds"),
    ("waitForPodsReady", "requeuingStrategy", "backoffMaxSeconds"),
)


def _pop(doc: dict, path: tuple[str, ...]) -> object:
    for key in path[:-1]:
        doc = doc[key]
    return doc.pop(path[-1])


def test_kueue_env_pins_one_release():
    env = _env_file(KUEUE / "kueue.env")
    assert set(env) == {"KUEUE_VERSION", "KUEUE_MANIFESTS_SHA256"}
    # K6-R1: a new 0.20 patch is a bump; a new minor is a plan change.
    assert re.fullmatch(r"v0\.20\.\d+", env["KUEUE_VERSION"])
    assert re.fullmatch(r"[0-9a-f]{64}", env["KUEUE_MANIFESTS_SHA256"])


def test_the_production_config_is_what_k6_decided():
    [config] = _load(CONFIG / "production.yaml")
    assert config["apiVersion"] == "config.kueue.x-k8s.io/v1beta2"
    assert config["kind"] == "Configuration"
    assert config["manageJobsWithoutQueueName"] is False
    assert config["integrations"]["frameworks"] == ["batch/job"]
    wait = config["waitForPodsReady"]
    assert "enable" not in wait  # v1beta2 has none: present = on
    assert wait["timeout"] == "10m"
    assert wait["blockAdmission"] is False
    assert wait["requeuingStrategy"] == {
        "timestamp": "Eviction", "backoffLimitCount": 3,
        "backoffBaseSeconds": 60, "backoffMaxSeconds": 3600,
    }


def test_the_kind_config_differs_only_in_wait_for_pods_ready_timing():
    [production] = _load(CONFIG / "production.yaml")
    [kind] = _load(CONFIG / "kind.yaml")
    production, kind = copy.deepcopy(production), copy.deepcopy(kind)
    kind_values = [_pop(kind, path) for path in KIND_TIMING_KEYS]
    for path in KIND_TIMING_KEYS:
        _pop(production, path)
    assert kind == production
    assert kind_values == ["30s", 1, 5, 10]


def test_pods_ready_timeout_outlasts_the_executors_start_grace():
    """K6-R23 (K-5 hand-off item 6): Kueue must not evict a pod still pulling
    its image before the executor's own start-failure grace would fail it."""
    [production] = _load(CONFIG / "production.yaml")
    timeout = _duration_seconds(production["waitForPodsReady"]["timeout"])
    assert timeout > kx.START_FAILURE_GRACE_SECONDS, (
        f"waitForPodsReady.timeout {timeout}s must exceed the executor's "
        f"START_FAILURE_GRACE_SECONDS ({kx.START_FAILURE_GRACE_SECONDS}s)"
    )


# -- Task 2: the Kueue objects, the admission policy, the default-deny ------

COMMON = KUEUE / "common"
KIND = KUEUE / "kind"
K8S = REPO / "infra" / "kubernetes"
RBAC_TESTS = Path(__file__).resolve().parent / "test_kubernetes_rbac.py"


def _docs(*dirs: Path) -> list[dict]:
    return [doc for d in dirs for path in sorted(d.glob("*.yaml")) for doc in _load(path)]


def _one(docs: list[dict], kind: str, name: str | None = None) -> dict:
    found = [d for d in docs if d["kind"] == kind
             and (name is None or d["metadata"]["name"] == name)]
    assert len(found) == 1, f"expected one {kind} {name or ''}, found {len(found)}"
    return found[0]


def _cluster_queue() -> dict:
    return _one(_load(KIND / "clusterqueue.yaml"), "ClusterQueue")


def test_every_kueue_object_is_v1beta2():
    kueue_docs = [d for d in _docs(COMMON, KIND)
                  if d["apiVersion"].startswith("kueue.x-k8s.io/")]
    assert kueue_docs
    for doc in kueue_docs:
        assert doc["apiVersion"] == f"kueue.x-k8s.io/{km.KUEUE_API_VERSION}" == (
            "kueue.x-k8s.io/v1beta2"
        ), doc["metadata"]["name"]


def test_priority_classes_are_the_executors():
    classes = {d["metadata"]["name"]: d for d in _load(COMMON / "workloadpriorityclasses.yaml")}
    assert set(classes) == set(PRIORITIES)
    assert classes["interactive"]["value"] == 1000
    assert classes["triggered"]["value"] == 100
    assert km.SCAN_PRIORITY in classes


def test_the_local_queue_is_the_executors_default():
    queue = _one(_load(COMMON / "localqueue.yaml"), "LocalQueue")
    assert queue["metadata"]["name"] == DEFAULT_PROCESS_K8S_DEFAULT_QUEUE
    assert queue["metadata"]["namespace"] == DEFAULT_PROCESS_K8S_NAMESPACE
    assert queue["spec"]["clusterQueue"] == _cluster_queue()["metadata"]["name"]


def test_the_cluster_queue_is_what_k6_decided():
    spec = _cluster_queue()["spec"]
    assert spec["queueingStrategy"] == "BestEffortFIFO"
    assert spec["preemption"] == {"withinClusterQueue": "LowerPriority",
                                  "reclaimWithinCohort": "Never"}
    assert spec["namespaceSelector"] == {
        "matchLabels": {"kubernetes.io/metadata.name": DEFAULT_PROCESS_K8S_NAMESPACE}
    }
    flavors = {d["metadata"]["name"] for d in _load(KIND / "resourceflavors.yaml")}
    for group in spec["resourceGroups"]:
        assert group["coveredResources"] == ["cpu", "memory"]
        for flavor in group["flavors"]:
            assert flavor["name"] in flavors
            assert [r["name"] for r in flavor["resources"]] == ["cpu", "memory"]


def test_the_runs_namespace_default_deny_is_what_the_executor_requires():
    """K6-R19: the shipped policy passes K-5's own fail-closed check."""
    policy = _one(_load(K8S / "runs-default-deny.yaml"), "NetworkPolicy")
    assert policy["metadata"]["name"] == km.DEFAULT_DENY_POLICY
    assert policy["metadata"]["namespace"] == DEFAULT_PROCESS_K8S_NAMESPACE
    assert km.isolation_problem(policy, DEFAULT_PROCESS_K8S_NAMESPACE) is None


def _policies() -> list[dict]:
    return _load(COMMON / "admission-policy.yaml")


def test_the_admission_policies_cover_the_runs_namespace():
    docs = _policies()
    for name in (rules.JOB_POLICY, rules.POD_UPDATE_POLICY):
        policy = _one(docs, "ValidatingAdmissionPolicy", name)
        binding = _one(docs, "ValidatingAdmissionPolicyBinding", name)
        assert binding["spec"]["policyName"] == name
        assert binding["spec"]["validationActions"] == ["Deny"]
        assert binding["spec"]["matchResources"] == {"namespaceSelector": {
            "matchLabels": {"kubernetes.io/metadata.name": DEFAULT_PROCESS_K8S_NAMESPACE}
        }}
        assert policy["spec"]["failurePolicy"] == "Fail"
        for validation in policy["spec"]["validations"]:
            # K6-R21: 422 -> RunRejected, never 403 -> a silent requeue.
            assert validation["reason"] == "Invalid", validation["message"]
    [jobs] = _one(docs, "ValidatingAdmissionPolicy", rules.JOB_POLICY)["spec"][
        "matchConstraints"]["resourceRules"]
    assert jobs == {"apiGroups": ["batch"], "apiVersions": ["v1"],
                    "operations": ["CREATE", "UPDATE"], "resources": ["jobs"]}
    pods = _one(docs, "ValidatingAdmissionPolicy", rules.POD_UPDATE_POLICY)["spec"]
    assert pods["matchConstraints"]["resourceRules"] == [{
        "apiGroups": [""], "apiVersions": ["v1"], "operations": ["UPDATE"],
        "resources": ["pods"],
    }]


def test_the_admission_policy_names_the_executors_values():
    """The literals in the CEL are the executor's and K-5's manifests'."""
    docs = _policies()
    jobs = _one(docs, "ValidatingAdmissionPolicy", rules.JOB_POLICY)
    by_id = {rules.rule_id_of(v["message"]): v["expression"]
             for v in jobs["spec"]["validations"]}
    assert km.QUEUE_LABEL in by_id["queue-label"]
    assert km.PRIORITY_LABEL in by_id["priority-class"]
    [literal] = re.findall(r"in \[([^\]]*)\]", by_id["priority-class"])
    assert tuple(re.findall(r"'([^']*)'", literal)) == PRIORITIES
    assert f"'{DEFAULT_PROCESS_K8S_RUN_SERVICE_ACCOUNT}'" in by_id["service-account"]
    [condition] = _one(docs, "ValidatingAdmissionPolicy", rules.POD_UPDATE_POLICY)["spec"][
        "matchConditions"]
    [subject] = _one(_load(K8S / "pipeline-rolebinding.yaml"), "RoleBinding")["subjects"]
    user = f"system:serviceaccount:{subject['namespace']}:{subject['name']}"
    assert f"'{user}'" in condition["expression"]


def _pure_problems(function: str) -> set[str]:
    """Every problem string ``function`` in test_kubernetes_rbac.py can return
    (the constants it appends), read from its source."""
    tree = ast.parse(RBAC_TESTS.read_text())
    [node] = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function]
    found = set()
    for call in ast.walk(node):
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == "append" and call.args):
            arg = call.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.add(arg.value)
            elif isinstance(arg, ast.Name):  # e.g. problems.append(key)
                for loop in ast.walk(node):
                    if (isinstance(loop, ast.For) and isinstance(loop.target, ast.Name)
                            and loop.target.id == arg.id and isinstance(loop.iter, ast.Tuple)):
                        found.update(e.value for e in loop.iter.elts
                                     if isinstance(e, ast.Constant))
            else:
                raise AssertionError(f"{function}: cannot read problem {ast.dump(arg)}")
    return found


def test_the_cel_and_k5s_pure_rules_are_the_same_rules():
    """K6-R21: the YAML's rule ids, _admission_rules' table and the problems
    K-5's pure rules can report correspond exactly."""
    docs = _policies()
    yaml_ids = {
        rules.rule_id_of(v["message"])
        for name in (rules.JOB_POLICY, rules.POD_UPDATE_POLICY)
        for v in _one(docs, "ValidatingAdmissionPolicy", name)["spec"]["validations"]
    }
    assert None not in yaml_ids, "every validation message must start with [rule-id]"
    assert yaml_ids == rules.RULE_IDS
    job_ids = {rules.rule_id_of(v["message"]) for v in _one(
        docs, "ValidatingAdmissionPolicy", rules.JOB_POLICY)["spec"]["validations"]}
    assert job_ids == {r.rule_id for r in rules.JOB_RULES}
    assert _pure_problems("vap_job_violations") == {r.problem for r in rules.JOB_RULES}
    assert _pure_problems("vap_pod_update_violations") == {
        r.problem for r in rules.POD_UPDATE_RULES}


def _built_job() -> dict:
    ctx = km.JobContext(DEFAULT_PROCESS_K8S_NAMESPACE, DEFAULT_PROCESS_K8S_DEFAULT_QUEUE,
                        DEFAULT_PROCESS_K8S_RUN_SERVICE_ACCOUNT, {})
    return km.build_job(spec(), name=NAME, ctx=ctx, pull_secret=None)


@pytest.mark.parametrize("rule", rules.JOB_RULES, ids=lambda r: r.rule_id)
def test_each_job_mutation_breaks_exactly_its_rule(rule):
    job = _built_job()
    assert vap_job_violations(job) == []
    rule.mutate(job)
    assert vap_job_violations(job) == [rule.problem]


@pytest.mark.parametrize("rule", rules.POD_UPDATE_RULES, ids=lambda r: r.rule_id)
def test_each_pod_update_mutation_breaks_exactly_its_rule(rule):
    template = _built_job()["spec"]["template"]
    old = {"metadata": copy.deepcopy(template["metadata"]),
           "spec": copy.deepcopy(template["spec"])}
    new = copy.deepcopy(old)
    rule.mutate(new)
    assert vap_pod_update_violations(old, new) == [rule.problem]
