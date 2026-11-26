"""Shared fixtures for the K-5 Kubernetes executor tests: a started
:class:`FakeKube`, a token file, an executor wired to both, and a RunSpec
builder."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from _fake_kube import FakeKube
from pipeline.process.executor import RunSpec
from pipeline.process.hardware import parse_hardware_profiles
from pipeline.process.kube_api import KubeApi, KubeConfig
from pipeline.process.kubernetes_executor import KubernetesExecutor

RUN = "0b9f8a52-3c1d-4e5f-8a9b-0c1d2e3f4a5b"
RUN2 = "1c0a9b63-4d2e-4f60-9bac-1d2e3f4a5b6c"
SCAN = "7c6d5e4f-3a2b-4c1d-9e8f-7a6b5c4d3e2f"
PROCESS = "11111111-2222-3333-4444-555555555555"
SUFFIX = "a1b2c3"
NAME = f"stac-run-{RUN}-{SUFFIX}"
DIGEST = "sha256:" + "a" * 64
RUNTIME = f"ghcr.io/techtraverse/stac-higher-process-runtime@{DIGEST}"
NOW = dt.datetime(2026, 11, 26, 13, 0, 0, tzinfo=dt.UTC)
ENV = {"API_TOKEN": "tok-SENTINEL", "STAC_HIGHER_RUN_ID": RUN}


def profile(kubernetes: dict | None = None):
    doc = {
        "version": 1,
        "profiles": [{
            "id": "standard", "label": "S", "description": "", "tier": "gpu",
            "accelerator": {"vendor": "nvidia"}, "cpu": {"min": 0.25, "max": 8, "default": 1},
            "memory_mb": {"min": 128, "max": 32768, "default": 512},
            "gpu_count": {"min": 0, "max": 1, "default": 0},
            "max_queue_wait_seconds": 900, "image": None,
            "backend": {"kubernetes": kubernetes} if kubernetes is not None else {},
        }],
    }
    return parse_hardware_profiles(doc).standard


def spec(**overrides) -> RunSpec:
    base = {
        "run_id": RUN, "process_id": PROCESS, "image": RUNTIME, "env": dict(ENV),
        "memory_mb": 1024, "timeout_seconds": 900, "cpu": 1.0,
        "profile": profile({"queue": "process-runs"}),
    }
    return RunSpec(**{**base, **overrides})


class Clock:
    def __init__(self, now: dt.datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> dt.datetime:
        return self.now


@pytest.fixture
def fake():
    with FakeKube() as server:
        yield server


@pytest.fixture
def token_file(tmp_path, fake) -> Path:
    path = tmp_path / "token"
    path.write_text(fake.token)
    return path


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def executor(fake, token_file, clock) -> KubernetesExecutor:
    return KubernetesExecutor(
        api=KubeApi(KubeConfig(fake.url, token_file, None)),
        _clock=clock,
        _suffix=lambda: SUFFIX,
    )
