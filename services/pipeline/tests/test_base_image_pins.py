"""Every platform base image and third-party compose/workflow-service image is pinned by
digest (GitHub #60): `image:tag@sha256:<64 hex>`, the tag kept for
readability. A re-pushed upstream tag must not change what we build or run
without a diff. Dependabot (`.github/dependabot.yml`) refreshes the digests.

The files are globbed, not listed, so a NEW Dockerfile or compose service is
covered the day it lands.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Directories never scanned: dependencies, worktrees, build output.
_SKIP_PARTS = frozenset({"node_modules", ".venv", ".claude", ".git", "dist", ".astro"})

#: `FROM ${ARG}` is allowed only where the ARG is fed a bake TARGET, not a
#: registry image, so the pin is inherited from that target's own FROM.
_ARG_FROM_ALLOWED = {
    "services/process-runtime/Dockerfile.stactools": "BASE_IMAGE is bake target:runtime",
}

#: This repo's own build outputs (compose `image:` names for `build:` services).
_LOCAL_IMAGE_PREFIX = "stac-higher-"

_PINNED = re.compile(r"^[^\s@]+:[^\s@:/]+@sha256:[0-9a-f]{64}$")
_FROM = re.compile(
    r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?\s*$", re.IGNORECASE
)
_IMAGE = re.compile(r"^\s*image:\s*[\"']?([^\"'\s#]+)")


PIN_HINT = "pin these by index digest (docs/backend.md 'Base images'):\n"


def is_pinned(ref: str) -> bool:
    return bool(_PINNED.match(ref))


def _walk(predicate) -> list[Path]:
    found = []
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_PARTS]
        for name in filenames:
            if predicate(name):
                found.append(Path(dirpath) / name)
    return sorted(found)


def is_dockerfile_name(name: str) -> bool:
    """`Dockerfile`, `Dockerfile.<x>`, `Containerfile`, `<x>.Dockerfile`.
    Any `Dockerfile.<x>` counts, including `Dockerfile.md`: a false positive
    only scans a non-Dockerfile for FROM lines, which is harmless."""
    return (
        name in ("Dockerfile", "Containerfile")
        or name.startswith("Dockerfile.")
        or name.endswith(".Dockerfile")
    )


def is_compose_name(name: str) -> bool:
    """`docker-compose.y(a)ml`, `compose.y(a)ml`, `compose.<x>.y(a)ml`."""
    if name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        return True
    return name.startswith("compose.") and name.endswith((".yml", ".yaml"))


def dockerfiles() -> list[Path]:
    return _walk(is_dockerfile_name)


def compose_files() -> list[Path]:
    return _walk(is_compose_name)


def workflow_files() -> list[Path]:
    """GitHub Actions workflows, whose `services:` images are pinned too."""
    return sorted((REPO_ROOT / ".github/workflows").glob("*.y*ml"))


def unpinned_images(path: Path) -> list[str]:
    """`file:line: ref` for each `image: <ref>` that is not a digest pin."""
    rel = str(path.relative_to(REPO_ROOT))
    bad = []
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        match = _IMAGE.match(line)
        if not match:
            continue
        ref = match.group(1)
        if ref.startswith(_LOCAL_IMAGE_PREFIX) or ref.startswith("$"):
            continue
        if not is_pinned(ref):
            bad.append(f"{rel}:{lineno}: {ref}")
    return bad


def unpinned_froms(text: str, rel: str) -> list[str]:
    """`file:line: ref` for every FROM that is neither an earlier stage, an
    allow-listed `${ARG}`, nor a digest pin."""
    stages: set[str] = set()
    bad: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        match = _FROM.match(line)
        if not match:
            continue
        ref, alias = match.group(1), match.group(2)
        if ref.lower() in stages:
            pass
        elif ref.startswith("${") or ref.startswith("$"):
            if rel not in _ARG_FROM_ALLOWED:
                bad.append(f"{rel}:{lineno}: {ref}")
        elif not is_pinned(ref):
            bad.append(f"{rel}:{lineno}: {ref}")
        if alias:
            stages.add(alias.lower())
    return bad


def test_globs_find_the_known_files():
    names = {str(p.relative_to(REPO_ROOT)) for p in dockerfiles()}
    assert {
        "app/Dockerfile",
        "services/pipeline/Dockerfile",
        "services/image-scanner/Dockerfile",
        "services/process-runtime/Dockerfile",
        "services/process-runtime/Dockerfile.stactools",
        "infra/titiler/Dockerfile",
        "infra/proxy-policy/Dockerfile",
    } <= names
    composes = {str(p.relative_to(REPO_ROOT)) for p in compose_files()}
    assert {"docker-compose.yml", "infra/compose.test-servers.yml"} <= composes


def test_pin_check_accepts_stage_refs_and_rejects_unlisted_args():
    digest = "sha256:" + "a" * 64
    text = (
        f"FROM node:22-trixie-slim@{digest} AS build\n"
        "FROM build\n"
        "FROM --platform=linux/amd64 python:3.12-slim\n"
        "FROM ${BASE_IMAGE}\n"
    )
    assert unpinned_froms(text, "x/Dockerfile") == [
        "x/Dockerfile:3: python:3.12-slim",
        "x/Dockerfile:4: ${BASE_IMAGE}",
    ]
    stactools = "services/process-runtime/Dockerfile.stactools"
    assert unpinned_froms("FROM ${BASE_IMAGE}\n", stactools) == []


@pytest.mark.parametrize(
    "ref, ok",
    [
        ("python:3.12-slim-trixie@sha256:" + "f" * 64, True),
        ("ghcr.io/a/b:1.0@sha256:" + "0" * 64, True),
        ("localhost:5000/a:1@sha256:" + "0" * 64, True),
        ("python:3.12-slim", False),
        ("python@sha256:" + "f" * 64, False),  # the tag must stay for readability
        ("python:3.12@sha256:abc", False),
    ],
)
def test_is_pinned(ref, ok):
    assert is_pinned(ref) is ok


def test_every_dockerfile_from_is_digest_pinned():
    bad = []
    for path in dockerfiles():
        bad += unpinned_froms(path.read_text(), str(path.relative_to(REPO_ROOT)))
    assert bad == [], PIN_HINT + "\n".join(bad)


def test_every_compose_and_workflow_image_is_digest_pinned():
    bad = []
    for path in [*compose_files(), *workflow_files()]:
        bad += unpinned_images(path)
    assert bad == [], PIN_HINT + "\n".join(bad)


@pytest.mark.parametrize(
    "name, ok",
    [
        ("Dockerfile", True),
        ("Dockerfile.stactools", True),
        ("Containerfile", True),
        ("base.Dockerfile", True),
        ("Dockerfile.md", True),  # accepted false positive, see is_dockerfile_name
        ("dockerfile", False),
        ("Dockerfile-notes", False),
        ("README.md", False),
    ],
)
def test_dockerfile_name_predicate(name, ok):
    assert is_dockerfile_name(name) is ok


@pytest.mark.parametrize(
    "name, ok",
    [
        ("docker-compose.yml", True),
        ("docker-compose.yaml", True),
        ("compose.yml", True),
        ("compose.yaml", True),
        ("compose.test-servers.yml", True),
        ("compose.test-servers.yaml", True),
        ("docker-compose.override.yml", False),
        ("compose.md", False),
        ("mycompose.yml", False),
    ],
)
def test_compose_name_predicate(name, ok):
    assert is_compose_name(name) is ok


def _dependabot_dirs() -> dict[str, set[str]]:
    """ecosystem -> directories, from .github/dependabot.yml's `updates:` list.
    A deliberately narrow parser (no YAML dependency): one entry starts at
    `- package-ecosystem:`; `directory: /p` or a `directories:` block of
    `- /p` lines follows."""
    dirs: dict[str, set[str]] = {}
    ecosystem: str | None = None
    in_dirs = False
    for raw in (REPO_ROOT / ".github/dependabot.yml").read_text().splitlines():
        line = raw.split("#", 1)[0].rstrip()
        stripped = line.strip()
        if stripped.startswith("- package-ecosystem:"):
            ecosystem = stripped.split(":", 1)[1].strip()
            dirs.setdefault(ecosystem, set())
            in_dirs = False
        elif ecosystem and stripped.startswith("directory:"):
            dirs[ecosystem].add(stripped.split(":", 1)[1].strip().strip("\"'"))
            in_dirs = False
        elif ecosystem and stripped == "directories:":
            in_dirs = True
        elif in_dirs and stripped.startswith("- /"):
            dirs[ecosystem].add(stripped[2:].strip().strip("\"'"))
        elif stripped:
            in_dirs = False
    return dirs


def test_dependabot_covers_every_dockerfile_directory():
    dirs = _dependabot_dirs()
    docker_dirs = dirs.get("docker", set())
    for path in dockerfiles():
        rel = "/" + str(path.parent.relative_to(REPO_ROOT))
        assert rel in docker_dirs, f"{rel} has a Dockerfile but no Dependabot docker entry"
    compose_dirs = dirs.get("docker-compose", set())
    for path in compose_files():
        parent = path.parent.relative_to(REPO_ROOT)
        rel = "/" if str(parent) == "." else "/" + str(parent)
        assert rel in compose_dirs, (
            f"{rel} has a compose file but no Dependabot docker-compose entry"
        )
