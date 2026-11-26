"""K-6: static checks of infra/kueue, infra/kind, the runs namespace's
default-deny policy and infra/hardware-profiles/kind.json — no cluster.

The kind leg (`.github/workflows/kind.yml`) proves these objects on a real
API server; here they are pinned against the executor's own constants, so a
rename on either side fails in the ordinary pipeline job first."""

from __future__ import annotations

import copy
import re
from pathlib import Path

import yaml

from pipeline.process import kubernetes_executor as kx

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
