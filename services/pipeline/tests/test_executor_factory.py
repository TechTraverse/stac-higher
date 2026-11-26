"""K-5 (plan Task 8): build_executor(settings) is the one place a process-run
executor is built, and ExecutorProbe is /health's informational view of it."""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from pipeline.config import Settings
from pipeline.process.docker_executor import DockerExecutor
from pipeline.process.executor import ExecutorUnavailable
from pipeline.process.factory import EXECUTOR_PROBE_TTL_SECONDS, ExecutorProbe, build_executor
from pipeline.process.kubernetes_executor import KubernetesExecutor

SRC = Path(__file__).resolve().parents[1] / "src" / "pipeline"


def test_docker_is_the_default():
    settings = Settings()
    executor = build_executor(settings)
    assert isinstance(executor, DockerExecutor)
    assert executor.docker_host == settings.docker_host


def test_kubernetes_is_selected_by_setting(tmp_path):
    token = tmp_path / "token"
    token.write_text("t")
    settings = Settings.from_env(env={
        "PROCESS_EXECUTOR": "kubernetes",
        "PROCESS_K8S_API_URL": "http://127.0.0.1:1",  # closed: building makes no call
        "PROCESS_K8S_TOKEN_FILE": str(token),
        "PROCESS_K8S_NAMESPACE": "runs",
        "PROCESS_K8S_DEFAULT_QUEUE": "q",
        "PROCESS_K8S_RUN_SERVICE_ACCOUNT": "sa",
        "PROCESS_K8S_REGISTRY_MIRRORS": '{"ghcr.io": "m.test/g"}',
    })
    executor = build_executor(settings)
    assert isinstance(executor, KubernetesExecutor)
    assert (executor.namespace, executor.default_queue, executor.run_service_account) == (
        "runs", "q", "sa"
    )
    assert dict(executor.registry_mirrors) == {"ghcr.io": "m.test/g"}


def test_kubernetes_outside_a_cluster_is_unavailable():
    settings = Settings.from_env(env={"PROCESS_EXECUTOR": "kubernetes"})
    with pytest.raises(ExecutorUnavailable, match="PROCESS_K8S_API_URL"):
        build_executor(settings)


def test_no_inline_executor_constructions():
    allowed = {"process/factory.py", "process/docker_executor.py",
               "process/kubernetes_executor.py"}
    pattern = re.compile(r"\b(DockerExecutor|KubernetesExecutor)\(")
    offenders = [
        str(path.relative_to(SRC)) for path in SRC.rglob("*.py")
        if str(path.relative_to(SRC)) not in allowed and pattern.search(path.read_text())
    ]
    assert offenders == []


class _Stub:
    def __init__(self, error=None, delay=0.0):
        self.calls = 0
        self.error = error
        self.delay = delay

    def probe(self):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


KUBE = Settings.from_env(env={"PROCESS_EXECUTOR": "kubernetes"})


async def test_the_probe_reports_reachable_and_caches():
    stub, clock = _Stub(), _Clock()
    probe = ExecutorProbe(KUBE, clock=clock, build=lambda _s: stub)
    assert await probe() == {"name": "kubernetes", "reachable": True, "error": None}
    clock.now = EXECUTOR_PROBE_TTL_SECONDS - 1
    await probe()
    assert stub.calls == 1
    clock.now = EXECUTOR_PROBE_TTL_SECONDS + 1
    await probe()
    assert stub.calls == 2


async def test_the_probe_reports_an_outage_without_raising():
    down = ExecutorProbe(KUBE, build=lambda _s: _Stub(ExecutorUnavailable("x" * 1000)))
    result = await down()
    assert result["reachable"] is False and result["name"] == "kubernetes"
    assert len(result["error"]) <= 300

    def unbuildable(_settings):
        raise ExecutorUnavailable("PROCESS_K8S_API_URL is unset")

    result = await ExecutorProbe(KUBE, build=unbuildable)()
    assert result == {"name": "kubernetes", "reachable": False,
                      "error": "PROCESS_K8S_API_URL is unset"}

    slow = ExecutorProbe(KUBE, timeout_seconds=0.05, build=lambda _s: _Stub(delay=0.3))
    result = await slow()
    assert result["reachable"] is False and "timed out" in result["error"]


async def test_an_unexpected_probe_error_is_reported_too():
    result = await ExecutorProbe(KUBE, build=lambda _s: _Stub(RuntimeError("boom")))()
    assert result["reachable"] is False and "boom" in result["error"]
