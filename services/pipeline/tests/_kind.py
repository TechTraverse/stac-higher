"""The kind leg's harness (K-6, plan Task 5 and addendum A).

Two identities, kept apart (K6-R3):

- The executor under test is built ONLY from the pipeline ServiceAccount's
  token (``PROCESS_K8S_TOKEN_FILE``), exactly as the worker builds it
  (``build_executor``), so least privilege is proven against the real
  authorizer. :func:`api` is the same identity, for reviews and raw probes.
- The kind admin kubeconfig is used ONLY through :func:`kubectl`, for fault
  injection (delete a Job, evict a pod, remove a policy) and observation (pod
  status, Workload conditions). Nothing here builds a ``KubeApi`` or an
  executor from admin credentials.

The integration modules skip unless ``KIND_INTEGRATION=1``; with it set and a
variable missing, importing this module's :func:`require` fails collection
(K6-R16), so a half-configured CI run fails instead of skipping.

Each unverified K-5 assumption is settled by a recording scenario (K6-R22):
:func:`observe` prints what was seen and appends it to
``$KIND_OUT/observations.jsonl`` (object status and test-made text only --
never Secret data), which CI uploads on every run."""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import datetime as dt
import json
import os
import subprocess
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from pipeline.config import Settings
from pipeline.process.executor import Executor, RunHandle, RunSpec, RunState
from pipeline.process.factory import build_executor
from pipeline.process.hardware import HardwareProfile, load_hardware_profiles
from pipeline.process.kube_api import KubeApi
from pipeline.process.kubernetes_manifests import KUEUE_JOB_UID_LABEL, POD_JOB_LABEL

ENABLED = os.environ.get("KIND_INTEGRATION") == "1"
REQUIRED_ENV = ("PROCESS_K8S_API_URL", "PROCESS_K8S_TOKEN_FILE", "PROCESS_K8S_CA_FILE",
                "KIND_ADMIN_KUBECONFIG", "KIND_RUNTIME_IMAGE")
NAMESPACE = "stac-higher-runs"
PIPELINE_NAMESPACE = "stac-higher"
PIPELINE_SA = "stac-higher-pipeline"
PIPELINE_USER = f"system:serviceaccount:{PIPELINE_NAMESPACE}:{PIPELINE_SA}"
WAIT_SECONDS = 120
MARKER = "K6-MARKER"
REPO = Path(__file__).resolve().parents[3]
KIND_PROFILES = REPO / "infra" / "hardware-profiles" / "kind.json"
DEFAULT_DENY_FILE = REPO / "infra" / "kubernetes" / "runs-default-deny.yaml"

skip_unless_kind = pytest.mark.skipif(
    not ENABLED, reason="KIND_INTEGRATION is not set (the kind CI leg sets it)"
)


def require() -> None:
    """K6-R16: with ``KIND_INTEGRATION=1``, every variable must be set."""
    if not ENABLED:
        return
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            f"KIND_INTEGRATION=1 but {missing} unset — run scripts/kind-up.sh and "
            "source its kind.env"
        )


def env_file(path: Path) -> dict[str, str]:
    """A ``KEY=value`` pin file (infra/kind/versions.env, infra/kueue/kueue.env)."""
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _sep, value = line.partition("=")
            values[key] = value
    return values


# -- the pipeline's identity ---------------------------------------------------


def settings(**overrides: str) -> Settings:
    return Settings.from_env({**os.environ, "PROCESS_EXECUTOR": "kubernetes", **overrides})


def executor(**overrides: str) -> Executor:
    """The worker's own construction path (C2), as the pipeline."""
    return build_executor(settings(**overrides))


def api(**overrides: str) -> KubeApi:
    """A raw client as the pipeline (reviews, denial probes)."""
    return KubeApi.from_settings(settings(**overrides))


# -- the admin's view, through kubectl only ------------------------------------


def kubectl(*args: str, input: str | None = None, check: bool = True,
            timeout: float = 90) -> subprocess.CompletedProcess[str]:
    """``kubectl --kubeconfig $KIND_ADMIN_KUBECONFIG …``. Callers never pass
    Secret data in or ask for it out."""
    cmd = ["kubectl", "--kubeconfig", os.environ["KIND_ADMIN_KUBECONFIG"], *args]
    done = subprocess.run(cmd, input=input, capture_output=True, text=True,
                          timeout=timeout, check=False)
    if check and done.returncode != 0:
        raise AssertionError(
            f"kubectl {' '.join(args)} failed ({done.returncode}): "
            f"{done.stderr.strip()[:2000]} {done.stdout.strip()[:500]}"
        )
    return done


def kget(resource: str, name: str | None = None, *, namespace: str | None = NAMESPACE,
         selector: str | None = None) -> Any:
    """``get -o json``; None when the named object does not exist."""
    args = ["get", resource]
    if name:
        args.append(name)
    if namespace:
        args += ["-n", namespace]
    if selector:
        args += ["-l", selector]
    done = kubectl(*args, "-o", "json", check=False)
    if done.returncode != 0:
        if "NotFound" in done.stderr or "not found" in done.stderr:
            return None
        raise AssertionError(f"kubectl get {resource} {name or ''}: {done.stderr.strip()}")
    return json.loads(done.stdout)


def kapply(doc: Mapping[str, Any]) -> None:
    kubectl("apply", "-f", "-", input=json.dumps(doc))


def job_of(handle: RunHandle) -> dict | None:
    return kget("jobs", handle.id)


def _newest(items: list[dict]) -> dict | None:
    return max(items, key=lambda o: o["metadata"].get("creationTimestamp") or "",
               default=None)


def pod_of(handle: RunHandle) -> dict | None:
    """The Job's newest pod (admin)."""
    pods = kget("pods", selector=f"{POD_JOB_LABEL}={handle.id}")
    return _newest((pods or {}).get("items") or [])


def workload_of(handle: RunHandle) -> dict | None:
    """The Job's Kueue Workload, by the Job's uid (admin)."""
    job = job_of(handle)
    if job is None:
        return None
    uid = job["metadata"]["uid"]
    found = kget("workloads.kueue.x-k8s.io", selector=f"{KUEUE_JOB_UID_LABEL}={uid}")
    return _newest((found or {}).get("items") or [])


def conditions(obj: Mapping[str, Any] | None) -> dict[str, dict]:
    """``status.conditions`` by type ({} for None)."""
    if not obj:
        return {}
    return {c["type"]: c for c in (obj.get("status") or {}).get("conditions") or []}


def condition_summary(obj: Mapping[str, Any] | None) -> dict[str, str]:
    """``{type: "status/reason"}`` — the shape the recordings keep."""
    return {t: f"{c.get('status')}/{c.get('reason') or ''}" for t, c in conditions(obj).items()}


# -- what a run is -------------------------------------------------------------


def standard_profile() -> HardwareProfile:
    return load_hardware_profiles(KIND_PROFILES).standard


def profile_with(**kubernetes: Any) -> HardwareProfile:
    """``standard`` with its parsed kubernetes block replaced."""
    base = standard_profile()
    return dataclasses.replace(
        base, kubernetes=dataclasses.replace(base.kubernetes, **kubernetes)
    )


def code_spec(code: str, *, run_id: str | None = None, timeout_seconds: int = 120,
              memory_mb: int = 256, cpu: float = 1.0, priority: str = "triggered",
              env: Mapping[str, str] | None = None,
              profile: HardwareProfile | None = None) -> RunSpec:
    """An inline_python run on the real platform runtime image."""
    return RunSpec(
        run_id=run_id or str(uuid.uuid4()),
        process_id="k6-process",
        image=os.environ["KIND_RUNTIME_IMAGE"],
        env={**(env or {}),
             "STAC_HIGHER_PROCESS_CODE_B64": base64.b64encode(code.encode()).decode()},
        memory_mb=memory_mb,
        timeout_seconds=timeout_seconds,
        cpu=cpu,
        priority=priority,
        profile=profile or standard_profile(),
        kind="process",
    )


def printing(seconds: float) -> str:
    """Code that prints :data:`MARKER` then sleeps."""
    return f"import time\nprint({MARKER!r}, flush=True)\ntime.sleep({seconds})\n"


# -- waiting -------------------------------------------------------------------


def wait_for[T](probe: Callable[[], T], done: Callable[[T], bool], *, what: str,
                timeout: float = WAIT_SECONDS, interval: float = 1.0) -> T:
    deadline = time.monotonic() + timeout
    last: Any = None
    while True:
        last = probe()
        if done(last):
            return last
        if time.monotonic() >= deadline:
            raise AssertionError(f"{what}: not reached in {timeout}s; last={last!r}")
        time.sleep(interval)


def wait_state(ex: Executor, handle: RunHandle, phases: set[str], *,
               seen: list[tuple[str, str | None]] | None = None,
               timeout: float = WAIT_SECONDS) -> RunState:
    """Poll ``status()`` until its phase is one of ``phases``; every distinct
    ``(phase, detail)`` is appended to ``seen``."""
    seen = seen if seen is not None else []

    def probe() -> RunState:
        state = ex.status(handle)
        if not seen or seen[-1] != (state.phase, state.detail):
            seen.append((state.phase, state.detail))
        return state

    return wait_for(probe, lambda s: s.phase in phases,
                    what=f"{handle.id} reaching {sorted(phases)} (seen {seen})",
                    timeout=timeout)


def wait_log(ex: Executor, handle: RunHandle, needle: bytes, *, timeout: float = 60) -> bytes:
    return wait_for(lambda: ex.logs(handle, 65536), lambda b: needle in b,
                    what=f"{needle!r} in the log of {handle.id}", timeout=timeout)


def diagnose_log_loss(handle: RunHandle) -> str:
    """K6-R12: why a stopped run's log is not readable, and what follows."""
    pod = pod_of(handle)
    if pod is None:
        return ("pod gone — something deleted it: a K-5 bug (the stop must keep the pod), "
                "not the read-before-stop fallback")
    status = pod.get("status") or {}
    container = next(iter(status.get("containerStatuses") or []), {})
    return (f"pod present (phase={status.get('phase')}, reason={status.get('reason')}, "
            f"container={container.get('state')}) — log lost by the kubelet: take the "
            "read-before-stop fallback (KubernetesExecutor._stop_keeping_the_log, I-178)")


# -- cleanup -------------------------------------------------------------------


def clean_namespace() -> None:
    """K6-R17: no Job, run Secret, pod or Workload left in the runs namespace."""
    kubectl("delete", "jobs", "--all", "-n", NAMESPACE, "--cascade=foreground",
            "--wait=true", "--timeout=90s", "--ignore-not-found", timeout=120)
    kubectl("delete", "secrets", "-l", "stac-higher.run-id", "-n", NAMESPACE,
            "--wait=true", "--ignore-not-found")
    kubectl("delete", "pods", "--all", "-n", NAMESPACE, "--wait=true", "--timeout=90s",
            "--ignore-not-found", "--grace-period=1", timeout=120)

    def left() -> tuple[int, int]:
        pods = (kget("pods") or {}).get("items") or []
        workloads = (kget("workloads.kueue.x-k8s.io") or {}).get("items") or []
        return len(pods), len(workloads)

    wait_for(left, lambda n: n == (0, 0), what="an empty runs namespace (pods, workloads)",
             timeout=120, interval=2)


@contextlib.contextmanager
def reaping(ex: Executor, *handles: RunHandle):
    """Reap every handle on the way out, whatever the test did."""
    try:
        yield
    finally:
        for handle in handles:
            with contextlib.suppress(Exception):
                ex.reap(handle)


# -- recordings (K6-R22) -------------------------------------------------------


def observe(scenario: str, **facts: Any) -> None:
    """Print ``facts`` and append them to ``$KIND_OUT/observations.jsonl``."""
    record = {"at": dt.datetime.now(dt.UTC).isoformat(), "scenario": scenario, **facts}
    line = json.dumps(record, default=repr, sort_keys=True)
    print(f"K6-OBSERVATION {line}")
    out = os.environ.get("KIND_OUT")
    if out:
        with open(Path(out) / "observations.jsonl", "a") as handle:
            handle.write(line + "\n")


# -- bare probe pods (network controls; never in the runs namespace's Jobs) ----


def probe_pod(namespace: str, name: str, code: str, *, env: Mapping[str, str] | None = None,
              labels: Mapping[str, str] | None = None, wait: bool = True,
              timeout: float = 120) -> tuple[int | None, str]:
    """Run ``code`` on the runtime image in a bare pod (admin) and return its
    exit code and log; with ``wait=False`` return at once (a server)."""
    env = {**(env or {}),
           "STAC_HIGHER_PROCESS_CODE_B64": base64.b64encode(code.encode()).decode()}
    kapply({
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": name, "namespace": namespace, "labels": dict(labels or {})},
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "securityContext": {"runAsNonRoot": True, "runAsUser": 10001,
                                "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [{
                "name": "probe", "image": os.environ["KIND_RUNTIME_IMAGE"],
                "env": [{"name": k, "value": v} for k, v in env.items()],
                "securityContext": {"allowPrivilegeEscalation": False,
                                    "capabilities": {"drop": ["ALL"]}},
            }],
        },
    })
    if not wait:
        return None, ""
    pod = wait_for(lambda: kget("pods", name, namespace=namespace),
                   lambda p: p is not None and p["status"].get("phase") in ("Succeeded",
                                                                           "Failed"),
                   what=f"probe pod {namespace}/{name} finishing", timeout=timeout)
    state = next(iter(pod["status"].get("containerStatuses") or []), {}).get("state") or {}
    code_ = (state.get("terminated") or {}).get("exitCode")
    log = kubectl("logs", name, "-n", namespace, check=False).stdout
    return code_, log


#: Probe code: try each target in K6_TARGETS ([[label, host, port, tries]]),
#: print one JSON line ``K6-NET {label: true|false}`` and exit 0.
REACH_CODE = """
import json, os, socket, time
results = {}
for label, host, port, tries in json.loads(os.environ["K6_TARGETS"]):
    ok = False
    for attempt in range(tries):
        try:
            socket.create_connection((host, port), timeout=3).close()
            ok = True
            break
        except OSError:
            time.sleep(1)
    results[label] = ok
print("K6-NET " + json.dumps(results), flush=True)
"""


def reach_results(log: str) -> dict[str, bool] | None:
    for line in log.splitlines():
        if line.startswith("K6-NET "):
            return json.loads(line[len("K6-NET "):])
    return None
