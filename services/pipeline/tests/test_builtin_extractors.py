"""The built-in extractor registry (X-1, X-queue spec §5).

The registry document itself is exercised by ``test_contract_fixtures.py``
alongside every other cross-runtime fixture; this file holds what is specific
to the registry — the derivations both runtimes depend on, and the image pin
check that keeps ``Dockerfile.stactools`` and the registry from disagreeing
about what is installed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.process.builtin import (
    BuiltinRegistryError,
    dockerfile_pins,
    parse_builtin_extractors,
    pin_drift,
)

REPO = Path(__file__).resolve().parents[3]
FIXTURE = REPO / "tests" / "contract-fixtures" / "builtin-extractors.json"
DOCKERFILE = REPO / "services" / "process-runtime" / "Dockerfile.stactools"

REGISTRY = parse_builtin_extractors(json.loads(FIXTURE.read_text()))


def test_every_entry_derives_an_importable_module():
    """The image's build-time smoke test imports exactly this string for every
    entry (spec §6), so the derivation is part of the contract."""
    modules = {entry.id: entry.module for entry in REGISTRY}
    assert modules["stactools-goes"] == "stactools.goes"
    assert modules["stactools-goes-glm"] == "stactools.goes_glm"
    assert modules["stactools-noaa-mrms-qpe"] == "stactools.noaa_mrms_qpe"
    for entry in REGISTRY:
        assert entry.module.startswith("stactools.")
        assert "-" not in entry.module


def test_the_curated_set_is_pinnable_and_split_by_access():
    # Eleven, not the spec's fourteen: noaa-nwm, noaa-sst and hls exist only as
    # untagged GitHub repos and have never been released to PyPI, so they carry
    # no pin (lead's call 2026-09-04; I-107).
    assert len(REGISTRY) == 11
    anonymous = {e.id for e in REGISTRY if e.access == "anonymous"}
    assert anonymous == {
        "stactools-goes",
        "stactools-goes-glm",
        "stactools-noaa-hrrr",
        "stactools-noaa-mrms-qpe",
        "stactools-noaa-cdr",
    }
    # The rest are the credentialed archives whose live gate is owed (I-105).
    assert len(REGISTRY) - len(anonymous) == 6
    for entry in REGISTRY:
        assert entry.pin == f"{entry.package}=={entry.version}"


def test_duplicate_ids_are_refused():
    doc = {"extractors": [json.loads(FIXTURE.read_text())["extractors"][0]] * 2}
    with pytest.raises(BuiltinRegistryError, match="duplicate id"):
        parse_builtin_extractors(doc)


def test_dockerfile_pins_ignores_comments():
    text = "\n".join(
        [
            "FROM base",
            "# stactools-goes==9.9.9  <- a commented-out pin must not count",
            'RUN pip install --no-cache-dir "stactools-goes==0.1.8" \\',
            "      stactools-noaa-cdr==0.2.1",
        ]
    )
    assert dockerfile_pins(text) == {
        "stactools-goes": "0.1.8",
        "stactools-noaa-cdr": "0.2.1",
    }


def test_pin_drift_reports_every_kind_of_disagreement():
    entries = [e for e in REGISTRY if e.id in {"stactools-goes", "stactools-noaa-cdr"}]
    ok = "\n".join(f"RUN pip install {e.pin}" for e in entries)
    assert pin_drift(entries, ok) == []

    missing = f"RUN pip install {entries[0].pin}"
    assert any("not pinned in the image" in p for p in pin_drift(entries, missing))

    wrong = ok.replace("==0.1.8", "==0.1.7")
    assert any("in the image" in p and "in the registry" in p for p in pin_drift(entries, wrong))

    extra = ok + "\nRUN pip install stactools-landsat==0.5.0"
    assert any("no registry entry" in p for p in pin_drift(entries, extra))


@pytest.mark.skipif(
    not DOCKERFILE.exists(),
    reason="services/process-runtime/Dockerfile.stactools lands with X-2; the check arms itself",
)
def test_the_stactools_image_pins_exactly_the_registry():
    assert pin_drift(REGISTRY, DOCKERFILE.read_text()) == []
