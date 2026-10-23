"""`ensure_runtime_image()` — the demo seeders build the process runtime image.

Without the image every run fails to start with `No such image` and is
requeued without spending an attempt, so a fresh checkout's demo shows its
pipeline graph and never an item. The seeders now build it (a cached bake is
a no-op) instead of leaving that to a README precondition.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pipeline.demo import platform

REPO_ROOT = Path(__file__).resolve().parents[3]


class _Docker:
    """Records every `docker` invocation; `present` decides `image inspect`."""

    def __init__(self, *, present: bool = True, bake_rc: int = 0):
        self.present = present
        self.bake_rc = bake_rc
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, argv, *, cwd=None, **kwargs):
        self.calls.append((list(argv), str(cwd) if cwd else None))
        if argv[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(argv, 0 if self.present else 1)
        if argv[1:3] == ["buildx", "bake"]:
            return subprocess.CompletedProcess(argv, self.bake_rc)
        raise AssertionError(f"unexpected docker call: {argv}")

    @property
    def bakes(self):
        return [call for call in self.calls if call[0][1:3] == ["buildx", "bake"]]


@pytest.fixture
def docker(monkeypatch):
    fake = _Docker()
    monkeypatch.setattr(platform.subprocess, "run", fake)
    monkeypatch.setattr(platform, "say", lambda *a, **k: None)
    monkeypatch.delenv("PROCESS_RUNTIME_IMAGE", raising=False)
    return fake


def test_default_image_matches_compose_and_the_bake_target():
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    bake = (REPO_ROOT / platform.RUNTIME_BAKE_FILE).read_text()

    assert f"PROCESS_RUNTIME_IMAGE:-{platform.DEFAULT_RUNTIME_IMAGE}" in compose
    assert f'tags       = ["{platform.DEFAULT_RUNTIME_IMAGE}"]' in bake


def test_repo_root_holds_the_bake_file():
    assert platform.REPO_ROOT == REPO_ROOT
    assert (platform.REPO_ROOT / platform.RUNTIME_BAKE_FILE).is_file()


def test_builds_the_default_image_from_the_repo_root(docker):
    platform.ensure_runtime_image()

    assert docker.bakes == [
        (
            ["docker", "buildx", "bake", "-f", platform.RUNTIME_BAKE_FILE, "runtime"],
            str(REPO_ROOT),
        )
    ]


def test_builds_even_when_the_image_exists(docker):
    # A cached bake is a no-op, and a stale image after a pull is its own trap.
    docker.present = True

    platform.ensure_runtime_image()

    assert len(docker.bakes) == 1


def test_a_failed_bake_exits_naming_the_command(docker):
    docker.bake_rc = 1

    with pytest.raises(SystemExit) as excinfo:
        platform.ensure_runtime_image()

    assert "docker buildx bake" in str(excinfo.value)


def test_no_docker_cli_exits_naming_the_command(monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(platform.subprocess, "run", missing)
    monkeypatch.setattr(platform, "say", lambda *a, **k: None)
    monkeypatch.delenv("PROCESS_RUNTIME_IMAGE", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        platform.ensure_runtime_image()

    assert "docker buildx bake" in str(excinfo.value)


def test_no_build_only_checks_the_image(docker):
    platform.ensure_runtime_image(build=False)

    assert docker.bakes == []
    assert docker.calls[0][0][:3] == ["docker", "image", "inspect"]


def test_no_build_with_the_image_missing_exits(docker):
    docker.present = False

    with pytest.raises(SystemExit) as excinfo:
        platform.ensure_runtime_image(build=False)

    message = str(excinfo.value)
    assert platform.DEFAULT_RUNTIME_IMAGE in message
    assert "docker buildx bake" in message


def test_a_custom_image_is_checked_never_built(docker, monkeypatch):
    monkeypatch.setenv("PROCESS_RUNTIME_IMAGE", "registry.example/runtime:1.2")
    docker.present = False

    with pytest.raises(SystemExit) as excinfo:
        platform.ensure_runtime_image()

    assert docker.bakes == []
    assert "registry.example/runtime:1.2" in str(excinfo.value)


def test_an_empty_override_falls_back_to_the_default(docker, monkeypatch):
    # compose's `${PROCESS_RUNTIME_IMAGE:-…}` treats empty as unset; so do we.
    monkeypatch.setenv("PROCESS_RUNTIME_IMAGE", "")

    platform.ensure_runtime_image()

    assert len(docker.bakes) == 1


def test_seed_cli_builds_the_image_unless_told_not_to(monkeypatch):
    from pipeline.demo import __main__ as demo_main

    seen: list[bool] = []
    monkeypatch.setattr(demo_main.goes, "seed", lambda args: seen.append(args.build_image) or 0)
    monkeypatch.setattr(demo_main, "seed", lambda args: seen.append(args.build_image) or 0)
    # `set_defaults(func=…)` binds at parser build time, so patch before main().
    assert demo_main.main(["goes-seed"]) == 0
    assert demo_main.main(["goes-seed", "--no-build-image"]) == 0
    assert demo_main.main(["seed"]) == 0
    assert demo_main.main(["seed", "--no-build-image"]) == 0

    assert seen == [True, False, True, False]
