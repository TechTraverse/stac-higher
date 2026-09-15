"""Hardware profiles (K-1, process-compute spec §3): the reader, the checkout
fallback, and the bounds check both write gates share."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.process.hardware import (
    DEFAULT_HARDWARE_CPU,
    DEFAULT_HARDWARE_GPU_COUNT,
    DEFAULT_HARDWARE_PROFILE,
    PROFILES_ENV_VAR,
    HardwareProfileError,
    check_hardware_bounds,
    hardware_profiles_path,
    load_hardware_profiles,
    parse_hardware_profiles,
)

FIXTURE = (
    Path(__file__).resolve().parents[3] / "tests" / "contract-fixtures" / "hardware-profiles.json"
)
LOCAL_SET = Path(__file__).resolve().parents[3] / "infra" / "hardware-profiles" / "local.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def test_parse_the_sample_document():
    profiles = parse_hardware_profiles(_fixture()["document"])
    assert profiles.version == 1
    assert [p.id for p in profiles.profiles] == ["standard", "gpu-l4"]
    standard = profiles.standard
    assert standard.cpu.default == DEFAULT_HARDWARE_CPU == 1.0
    assert standard.memory_mb.default == 512
    assert standard.gpu_count is None and standard.accelerator is None
    gpu = profiles.get("gpu-l4")
    assert gpu is not None and gpu.gpu_count is not None and gpu.gpu_count.max == 1
    assert gpu.accelerator == {"vendor": "nvidia", "model": "l4", "memory_gb": 24}
    assert gpu.image == "process-runtime-cuda"
    # backend stays opaque — K-3/K-5 read it
    assert gpu.backend["kubernetes"]["queue"] == "process-runs"
    assert profiles.get("nope") is None


def test_an_empty_accelerator_object_survives_parsing():
    """`accelerator: {}` is a bare-vendor-info-less GPU declaration, not the
    same thing as no accelerator — `gpu_count` still says which of the two it
    is. The reader must not fold the empty object into `None` (K-1 final
    review Item 3; the app's schema is strict and rejects this document —
    the pipeline reader is deliberately lenient)."""
    document = {
        "version": 1,
        "profiles": [
            {
                "id": "standard",
                "tier": "gpu",
                "accelerator": {},
                "cpu": {"min": 0.25, "max": 4, "default": 1},
                "memory_mb": {"min": 128, "max": 16384, "default": 512},
                "gpu_count": {"min": 0, "max": 1, "default": 0},
                "max_queue_wait_seconds": 0,
                "image": None,
                "backend": {},
            }
        ],
    }
    profiles = parse_hardware_profiles(document)
    standard = profiles.standard
    assert standard.accelerator == {}
    assert standard.gpu_count is not None and standard.gpu_count.max == 1


def test_default_constants_match_the_shipped_standard_profile():
    """Both readers default an absent `hardware` block to these; the shipped
    sets must agree or a stored revision without the block would launch
    outside the bounds the UI shows."""
    for path in (FIXTURE, LOCAL_SET):
        doc = json.loads(path.read_text())
        doc = doc.get("document", doc)
        standard = parse_hardware_profiles(doc).standard
        assert standard.id == DEFAULT_HARDWARE_PROFILE == "standard"
        assert standard.cpu.default == DEFAULT_HARDWARE_CPU
        assert DEFAULT_HARDWARE_GPU_COUNT == 0 and standard.gpu_count is None


def test_the_local_set_parses():
    profiles = load_hardware_profiles(LOCAL_SET)
    assert [p.id for p in profiles.profiles] == ["standard", "cpu-large"]
    assert profiles.get("cpu-large").backend["docker"]["capacity"] == 1


def test_path_prefers_the_env_var_then_the_checkout(tmp_path):
    custom = tmp_path / "profiles.json"
    assert hardware_profiles_path({PROFILES_ENV_VAR: str(custom)}) == custom
    assert hardware_profiles_path({}) == LOCAL_SET


def test_load_reports_an_unreadable_file(tmp_path):
    with pytest.raises(HardwareProfileError, match="could not read"):
        load_hardware_profiles(tmp_path / "missing.json")


def test_bounds_returns_the_resolved_profile():
    profiles = parse_hardware_profiles(_fixture()["document"])
    profile = check_hardware_bounds(
        profiles=profiles, profile_id="gpu-l4", cpu=4, gpu_count=1, memory_mb=16384
    )
    assert profile.id == "gpu-l4"


@pytest.mark.parametrize(
    "case", json.loads(FIXTURE.read_text())["bounds_cases"], ids=lambda c: c["name"]
)
def test_bounds_cases_from_the_fixture(case):
    profiles = parse_hardware_profiles(_fixture()["document"])
    kwargs = dict(
        profiles=profiles,
        profile_id=case["hardware"]["profile"],
        cpu=case["hardware"]["cpu"],
        gpu_count=case["hardware"]["gpu_count"],
        memory_mb=case["memory_mb"],
    )
    if case["pipeline"] == "accept":
        check_hardware_bounds(**kwargs)
    else:
        with pytest.raises(HardwareProfileError) as err:
            check_hardware_bounds(**kwargs)
        assert str(err.value) == case["reason"]


def test_checkout_fallback_is_lazy_and_survives_a_shallow_install(monkeypatch):
    """K-1 landed note: the image has this module at /app/src/pipeline/process
    (five ancestors, not six); computing the checkout fallback at import time
    raised IndexError and crash-looped the worker. Resolving it lazily — and
    tolerating a shallow path — is what lets the image boot."""
    from pipeline.process import hardware

    monkeypatch.setattr(hardware, "__file__", "/app/src/pipeline/process/hardware.py")
    path = hardware.hardware_profiles_path({})
    assert path.name == "local.json"
    override = {hardware.PROFILES_ENV_VAR: "/x/p.json"}
    assert hardware.hardware_profiles_path(override) == Path("/x/p.json")
