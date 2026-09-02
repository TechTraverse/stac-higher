"""The process runtime image's blessed set and the system libs it needs.

Text pins, in the style of the app suite's migration lockstep tests. They
cannot build the image — no test does — but they catch the specific failure
that reached a running stack on 2026-09-02: the image installed `rasterio`
while the slim base lacked `libexpat1`, so `import rasterio` died with
`libexpat.so.1: cannot open shared object file` and EVERY process using the
blessed raster stack failed at import.

Nothing in the unit suites imports rasterio *inside* this image, so no amount
of pytest would have found it. What a test CAN do is refuse the combination
that was wrong: a Python package installed here without the shared library it
links against.
"""

from __future__ import annotations

from pathlib import Path

import pytest

DOCKERFILE = (
    Path(__file__).resolve().parents[2] / "process-runtime" / "Dockerfile"
).read_text()

#: Python package installed in the image -> the apt packages it needs on a
#: `python:*-slim` base beyond what its wheel bundles.
SYSTEM_DEPENDENCIES = {
    "rasterio": ("libexpat1",),
}


def test_the_dockerfile_is_where_the_tests_think_it_is():
    # A moved Dockerfile would make every negative assertion below pass
    # vacuously against an empty string.
    assert "FROM python:" in DOCKERFILE


@pytest.mark.parametrize(
    ("package", "system_packages"),
    [(pkg, libs) for pkg, libs in SYSTEM_DEPENDENCIES.items()],
    ids=list(SYSTEM_DEPENDENCIES),
)
def test_blessed_package_has_its_system_libraries(package: str, system_packages: tuple[str, ...]):
    if package not in DOCKERFILE:
        pytest.skip(f"{package} is no longer in the blessed set")
    for lib in system_packages:
        assert lib in DOCKERFILE, (
            f"{package} is installed in the runtime image but {lib} is not. "
            f"On the slim base that makes `import {package}` fail at run time "
            "for every process — invisibly to this suite."
        )


def test_apt_lists_are_dropped_in_the_layer_that_creates_them():
    # Otherwise the image carries the package index forever.
    if "apt-get install" in DOCKERFILE:
        assert "rm -rf /var/lib/apt/lists/*" in DOCKERFILE


def test_runs_do_not_execute_as_root():
    assert "useradd" in DOCKERFILE
    assert "USER runner" in DOCKERFILE


def test_code_still_arrives_by_environment_not_by_mount():
    # ADR 0013 / docker_executor.py: a bind mount is resolved by the daemon on
    # the HOST under docker-out-of-docker, so the socket proxy never needs to
    # permit mounts at all.
    assert "STAC_HIGHER_PROCESS_CODE_B64" in DOCKERFILE
    assert "VOLUME" not in DOCKERFILE
