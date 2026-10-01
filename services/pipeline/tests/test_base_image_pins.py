"""Every platform base image and third-party compose image is pinned by
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


def dockerfiles() -> list[Path]:
    return _walk(lambda name: name == "Dockerfile" or name.startswith("Dockerfile."))


def compose_files() -> list[Path]:
    return _walk(
        lambda name: name in ("docker-compose.yml", "compose.yml")
        or (name.startswith("compose.") and name.endswith(".yml"))
    )


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


def test_every_compose_image_is_digest_pinned():
    bad = []
    for path in compose_files():
        rel = str(path.relative_to(REPO_ROOT))
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            match = _IMAGE.match(line)
            if not match:
                continue
            ref = match.group(1)
            if ref.startswith(_LOCAL_IMAGE_PREFIX) or ref.startswith("$"):
                continue
            if not is_pinned(ref):
                bad.append(f"{rel}:{lineno}: {ref}")
    assert bad == [], PIN_HINT + "\n".join(bad)
