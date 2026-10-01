# C follow-up: pin every base image by digest, move the scanned runtime to trixie — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Tracking: GitHub #60 (queue C, epic #56).

**Goal:** Every platform `FROM` and every third-party compose `image:` is pinned `image:tag@sha256:…`, Dependabot bumps the digests, and the platform process-runtime image is rebuilt on Debian trixie so it passes `infra/image-policy/default.json` with no exception.

**Architecture:** A pytest pin check (`services/pipeline/tests/test_base_image_pins.py`) walks the repo's Dockerfiles and compose files and fails on any unpinned external image, so the rule survives future Dockerfiles. The Python-based images move from `bookworm` to `trixie` (same digest as today's `python:3.12-slim`); the Node app moves to `node:22-trixie-slim`. Dependabot's `docker` and `docker-compose` ecosystems keep the digests current, with version-bump ignores so it only refreshes digests. `images-seed` picks the newest non-revoked registry row explicitly, so a re-added runtime digest wins over the old excepted row.

**Tech Stack:** Dockerfiles, docker compose, buildx bake, Dependabot, pytest (pipeline suite), Grype (via the platform scanner image).

**Spec:** `docs/superpowers/specs/2026-09-13-container-images-scanning-design.md` §6.1 and ADR 0021; the issue text of #60; the C-5 finding (2026-09-30) that the runtime image FAILS the default policy and runs only on an admin exception expiring 2026-10-14.

## Evidence gathered before this plan (lead, 2026-09-30)

Grype (the platform scanner image's baked DB, `GRYPE_DB_AUTO_UPDATE=false`), linux/amd64:

| image | distro | CRITICAL | blocked by default policy? |
|---|---|---|---|
| `ghcr.io/techtraverse/stac-higher-process-runtime:latest` (bookworm, today) | Debian 12.15 | 2 fixed (CVE-2026-75803 `libssl3`/`openssl`) + 8 unfixed (`perl-base` ×5, `libc6`/`libc-bin` CVE-2026-5450, `libsqlite3-0` CVE-2025-7458) | yes: `critical_fixed`, and the unfixed ones are >30 days old, so even an `apt-get upgrade` rebuild on bookworm stays blocked |
| the same Dockerfile on `python:3.12-slim-trixie@sha256:f77ac9e4…` | Debian 13.7 | 0 | no: one fixed HIGH (CVE-2026-82049, CPython 3.12.14, EPSS 0.0019 < 0.1), no KEV, unfixed HIGHs are not blocked (`high_unfixed: false`) |

On the trixie probe: `import rasterio, numpy, boto3, pystac, rio_stac` plus an in-memory GTiff write all succeed (rasterio 1.5.2, GDAL 3.12.2, numpy 2.5.3). The stactools variant builds on it, and its in-build smoke (`python -m stac_higher_stactools.smoke`, which imports all eleven registry packages and checks their pins) passes.

**Decision:** trixie for every Python image; bookworm cannot pass the default policy.

## Global Constraints

- Branch `feat/c-pin-base-images`, worktree `.claude/worktrees/c-pin-base-images`. PR title `C: pin base images by digest, runtime on trixie`, body starts `Closes #60`.
- Pin format: `<image>:<tag>@sha256:<64 hex>`. Keep the tag for readability. The digest is the multi-arch **index** digest (`docker buildx imagetools inspect <image:tag> --format '{{json .Manifest}}'` → `.digest`), never a per-platform manifest digest: CI pushes linux/amd64 + linux/arm64.
- Digests to use, resolved 2026-09-30 (copy verbatim):
  - `python:3.12-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f`
  - `ghcr.io/astral-sh/uv:python3.12-trixie-slim@sha256:5ae92e4d35b8d586d50ddf4aba6ecdd9f744237284d31c86bf45077e4e17e4bd`
  - `node:22-trixie-slim@sha256:b26b04c123d9ff8ab646ceb18b9d75a1173acf64b9a401094b906d27b29338d4`
  - `ghcr.io/developmentseed/stac-auth-proxy:v1.2.0@sha256:3f0969d16badabd0212c0e7c462209490841ad4a342b9da1c2953ae56da21c11`
  - `ghcr.io/stac-utils/titiler-pgstac:1.7.2@sha256:29920c615edf1347ace6fd8a04895dd7bbd65d32dc1f25fe7f9d3f8679d75f0e`
  - `ghcr.io/stac-utils/pgstac:v0.9.11@sha256:26d5c20c650736a65793ab61cb2e7113ecef0c036ec8c2d6c6838c7aa14e131d`
  - `ghcr.io/stac-utils/stac-fastapi-pgstac:6.3.1@sha256:db117b91302f7983ce4e07658768a6e8d2f6716613252a15f5dcdbda8dae9883`
  - `quay.io/keycloak/keycloak:26.3@sha256:357829ec7c4693397533035092ad13b0644bcc95ded311f33a3738c4d9e9bdba`
  - `pgsty/silo:RELEASE.2026-09-16T00-00-00Z@sha256:635197cb9f36d01bee221d34d1c7d7960f6a95c48b0b6c01d99cd13bdae51a46`
  - `tecnativa/docker-socket-proxy:0.3.0@sha256:9e4b9e7517a6b660f2cc903a19b257b1852d5b3344794e3ea334ff00ae677ac2`
  - `ghcr.io/developmentseed/tipg:1.0.1@sha256:70ae557c4fab6983a5f302b91aa772548518ab0c9b9b4a0ebcf1c85ec7daaf54`
  - `atmoz/sftp:alpine@sha256:6d41b9200f8115ce925bbd295376cb3c6b72634a267f41946e5aee4efe482186`
  - `delfer/alpine-ftp-server:latest@sha256:60bb774d8408d9d4d5c74d05d1c086a34ce192c6c1a142ffac268cac0dbc6fac`
  - `fauria/vsftpd:latest@sha256:6d71d7c7f1b0ab2844ec7dc7999a30aef6d758b6d8179cf5967513f87c79c177`
- Locally built image names (`stac-higher-*`) are not pinned: they are this repo's own build outputs.
- `services/process-runtime/Dockerfile.stactools` keeps `FROM ${BASE_IMAGE}`: its base is the bake file's `target:runtime` (the `base` named context), so it inherits the runtime's pin transitively. Do not add a registry default to it.
- Teammates: run `npm run verify` and, in `services/pipeline` (after `uv sync --frozen --extra dev --extra stactools`), `uv run pytest` and `uv run ruff check . ../process-runtime/stac_higher_stactools ../image-scanner/stac_higher_scanner`. **Only these.** No Docker builds, no e2e, no dev server, no push. The lead builds every image.
- No new dependencies.

## Review Focus

1. **A future Dockerfile or compose service lands unpinned.** Expect the pin check to fail CI and name the file and line. Pinned by Task 1's `test_every_dockerfile_from_is_digest_pinned` and `test_every_compose_image_is_digest_pinned`, which glob the files rather than listing them.
2. **A multi-stage `FROM <earlier-stage>` or `FROM ${ARG}` is misread as an unpinned external image.** Expect stage references to be accepted, and `${…}` accepted only in the explicitly allow-listed `Dockerfile.stactools`. Pinned by `test_pin_check_accepts_stage_refs_and_rejects_unlisted_args`.
3. **A per-platform digest pasted instead of the index digest.** arm64 dev Macs and the arm64 CI push would then fail or run emulated. The check can't tell the two apart offline, so this is a lead check in Task 4 (`docker buildx imagetools inspect` of each pin shows a manifest list).
4. **Dependabot proposes `python:3.14-slim-trixie` or `node:24`.** Expect digest refreshes only. Pinned by Task 2's ignore rules, checked by `test_dependabot_covers_every_dockerfile_directory`.
5. **`images-seed` re-run after the new runtime digest is added on `/images` deploys on the OLD excepted row.** Expect the newest non-revoked row for that `(reference, tag)` to win, regardless of API order. Pinned by Task 3's `test_pick_image_prefers_the_newest_live_row`.

---

### Task 1: Pin check + pin every Dockerfile and compose image (Python images → trixie, app → node trixie)

**Files:**
- Create: `services/pipeline/tests/test_base_image_pins.py`
- Modify: `services/process-runtime/Dockerfile` (FROM line 12 + a comment)
- Modify: `services/process-runtime/Dockerfile.stactools` (comment above `ARG BASE_IMAGE` only)
- Modify: `services/image-scanner/Dockerfile` (FROM line 15)
- Modify: `services/pipeline/Dockerfile` (FROM lines 11 and 23)
- Modify: `app/Dockerfile` (FROM lines 16 and 31)
- Modify: `infra/titiler/Dockerfile` (FROM line 11)
- Modify: `infra/proxy-policy/Dockerfile` (FROM line 14)
- Modify: `docker-compose.yml` (every third-party `image:`)
- Modify: `infra/compose.test-servers.yml` (every `image:`)

**Interfaces:**
- Produces: `REPO_ROOT` (a `Path`), `dockerfiles()` and `compose_files()` (lists of `Path`), and `is_pinned(ref: str) -> bool` in `test_base_image_pins.py`. Task 2 adds a test to this file that uses `REPO_ROOT` and `dockerfiles()`.

- [ ] **Step 1: Write the failing pin check**

Create `services/pipeline/tests/test_base_image_pins.py`. Look at how `services/pipeline/tests/test_builtin_extractors.py` locates the repo root and `Dockerfile.stactools`, and use the same approach for `REPO_ROOT`. The pipeline tests run from `services/pipeline`, so `Path(__file__).resolve().parents[3]` is the repo root.

```python
"""Every platform base image and third-party compose image is pinned by
digest (GitHub #60): `image:tag@sha256:<64 hex>`, the tag kept for
readability. A re-pushed upstream tag must not change what we build or run
without a diff. Dependabot (`.github/dependabot.yml`) refreshes the digests.

The files are globbed, not listed, so a NEW Dockerfile or compose service is
covered the day it lands.
"""

from __future__ import annotations

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
_FROM = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?\s*$", re.IGNORECASE)
_IMAGE = re.compile(r"^\s*image:\s*[\"']?([^\"'\s#]+)")


def is_pinned(ref: str) -> bool:
    return bool(_PINNED.match(ref))


def _walk(predicate) -> list[Path]:
    found = []
    for path in REPO_ROOT.rglob("*"):
        rel = path.relative_to(REPO_ROOT)
        if _SKIP_PARTS.intersection(rel.parts) or not path.is_file():
            continue
        if predicate(path.name):
            found.append(path)
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
    assert unpinned_froms("FROM ${BASE_IMAGE}\n", "services/process-runtime/Dockerfile.stactools") == []


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
    assert bad == [], "pin these by index digest (docs/backend.md 'Base images'):\n" + "\n".join(bad)


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
    assert bad == [], "pin these by index digest (docs/backend.md 'Base images'):\n" + "\n".join(bad)
```

Note on `localhost:5000/a:1@…`: the `_PINNED` regex requires the tag segment to have no `/` or `:`, and greedy `[^\s@]+` on the name backtracks to the last `:`, so a registry port is accepted. If a parametrized case disagrees with this reasoning, fix the regex, not the case.

- [ ] **Step 2: Run it and watch it fail on today's files**

Run (from `services/pipeline`): `uv run pytest tests/test_base_image_pins.py -v`
Expected: `test_globs_find_the_known_files`, `test_pin_check_accepts_stage_refs_and_rejects_unlisted_args` and `test_is_pinned` PASS. `test_every_dockerfile_from_is_digest_pinned` FAILS listing 9 FROMs. `test_every_compose_image_is_digest_pinned` FAILS listing the 11 third-party compose images (8 in `docker-compose.yml`, `silo` counted twice; 3 in `infra/compose.test-servers.yml`).

- [ ] **Step 3: Pin the scanned runtime on trixie**

`services/process-runtime/Dockerfile`: replace `FROM python:3.12-slim-bookworm` with the block below. Leave every other line alone; `libexpat1` still exists in trixie and is still needed by rasterio's wheel.

```dockerfile
# Debian trixie, pinned by index digest (GitHub #60). This image is scanned
# under infra/image-policy/default.json like any user image (C-5). On bookworm
# it FAILED that policy (fixed CRITICAL CVE-2026-75803 in libssl3/openssl,
# plus unfixed CRITICALs in perl-base/libc/sqlite older than 30 days that no
# bookworm rebuild clears); on trixie it scans 0 CRITICAL. Dependabot bumps
# the digest; `rasterio`'s wheel and the stactools variant's smoke were
# re-proved on this base (plan 2026-09-30-c-pin-base-images).
FROM python:3.12-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
```

`services/process-runtime/Dockerfile.stactools`: directly above `ARG BASE_IMAGE=stac-higher-process-runtime:local`, add:

```dockerfile
# Not a registry image, so not digest-pinned here: the bake file feeds
# BASE_IMAGE the runtime TARGET (`base = "target:runtime"`), whose own FROM is
# pinned — the variant inherits that pin (GitHub #60).
```

- [ ] **Step 4: Pin the other Python images on trixie**

`services/image-scanner/Dockerfile`: `FROM python:3.12-slim-bookworm` → `FROM python:3.12-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f`. If a comment near the FROM says "bookworm", update it to "trixie".

`services/pipeline/Dockerfile`: the builder and the runtime MUST move together, because the builder's `.venv` is copied into the runtime and links against its Python and glibc.
- `FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder` → `FROM ghcr.io/astral-sh/uv:python3.12-trixie-slim@sha256:5ae92e4d35b8d586d50ddf4aba6ecdd9f744237284d31c86bf45077e4e17e4bd AS builder`
- `FROM python:3.12-slim-bookworm` → `FROM python:3.12-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f`
- Above the builder FROM, add one comment line: `# Both stages trixie and digest-pinned (GitHub #60): the builder's venv is copied into the runtime, so they move together.`

- [ ] **Step 5: Pin the app and the derived upstream images**

`app/Dockerfile`, both FROMs (the build stage keeps `AS build`):
- `FROM node:22-bookworm-slim AS build` → `FROM node:22-trixie-slim@sha256:b26b04c123d9ff8ab646ceb18b9d75a1173acf64b9a401094b906d27b29338d4 AS build`
- `FROM node:22-bookworm-slim` → `FROM node:22-trixie-slim@sha256:b26b04c123d9ff8ab646ceb18b9d75a1173acf64b9a401094b906d27b29338d4`

`infra/titiler/Dockerfile`: `FROM ghcr.io/stac-utils/titiler-pgstac:1.7.2` → `FROM ghcr.io/stac-utils/titiler-pgstac:1.7.2@sha256:29920c615edf1347ace6fd8a04895dd7bbd65d32dc1f25fe7f9d3f8679d75f0e`

`infra/proxy-policy/Dockerfile`: `FROM ghcr.io/developmentseed/stac-auth-proxy:v1.2.0` → `FROM ghcr.io/developmentseed/stac-auth-proxy:v1.2.0@sha256:3f0969d16badabd0212c0e7c462209490841ad4a342b9da1c2953ae56da21c11`

- [ ] **Step 6: Pin the compose images**

In `docker-compose.yml` and `infra/compose.test-servers.yml`, append the Global Constraints digest to every `image:` that does not start with `stac-higher-`. Both `pgsty/silo` lines get the same digest. The `stac-auth-proxy` service in `docker-compose.yml` gets the same digest as `infra/proxy-policy/Dockerfile`. Change nothing else on those lines (quotes, comments).

- [ ] **Step 7: Run the pin check and the full gates**

Run (from `services/pipeline`): `uv run pytest tests/test_base_image_pins.py -v`. Expected: all PASS.
Then: `uv run pytest -q`, `uv run ruff check . ../process-runtime/stac_higher_stactools ../image-scanner/stac_higher_scanner`, and from the repo root `npm run verify`. Expected: all green. `test_builtin_extractors.py` reads `Dockerfile.stactools`; it must still pass, since only a comment changed there.

- [ ] **Step 8: Commit**

```bash
git add services/pipeline/tests/test_base_image_pins.py services/process-runtime/Dockerfile services/process-runtime/Dockerfile.stactools services/image-scanner/Dockerfile services/pipeline/Dockerfile app/Dockerfile infra/titiler/Dockerfile infra/proxy-policy/Dockerfile docker-compose.yml infra/compose.test-servers.yml
git commit -m "C: pin every base and compose image by digest; Python images and the app on trixie (#60)"
```

---

### Task 2: Dependabot bumps the digests, plus the manual step documented

**Files:**
- Modify: `.github/dependabot.yml`
- Modify: `services/pipeline/tests/test_base_image_pins.py` (one test appended)
- Modify: `docs/backend.md` (a short "Base images" subsection; put it next to the docker-socket-proxy / process-runtime material, or at the end of the services section)

**Interfaces:**
- Consumes: `REPO_ROOT`, `dockerfiles()`, `compose_files()` from Task 1.

- [ ] **Step 1: Write the failing coverage test**

Append to `services/pipeline/tests/test_base_image_pins.py`. PyYAML is already a pipeline dependency; confirm with `uv run python -c "import yaml"`. If it is NOT importable, parse the file with a regex over `- /path` lines instead, and add no dependency.

```python
def test_dependabot_covers_every_dockerfile_directory():
    import yaml

    config = yaml.safe_load((REPO_ROOT / ".github/dependabot.yml").read_text())

    def dirs(ecosystem: str) -> set[str]:
        out: set[str] = set()
        for update in config["updates"]:
            if update["package-ecosystem"] == ecosystem:
                out.update(update.get("directories", []))
                if "directory" in update:
                    out.add(update["directory"])
        return out

    docker_dirs = dirs("docker")
    for path in dockerfiles():
        rel = "/" + str(path.parent.relative_to(REPO_ROOT))
        assert rel in docker_dirs, f"{rel} has a Dockerfile but no Dependabot docker entry"
    compose_dirs = dirs("docker-compose")
    for path in compose_files():
        parent = path.parent.relative_to(REPO_ROOT)
        rel = "/" if str(parent) == "." else "/" + str(parent)
        assert rel in compose_dirs, f"{rel} has a compose file but no Dependabot docker-compose entry"
```

Run: `uv run pytest tests/test_base_image_pins.py::test_dependabot_covers_every_dockerfile_directory -v`
Expected: FAIL naming `/infra/proxy-policy` or another missing directory.

- [ ] **Step 2: Rewrite the docker entries in `.github/dependabot.yml`**

Replace the two existing `package-ecosystem: docker` entries (`/app` and `/services/pipeline`) with the block below. Leave the `github-actions`, `npm` and `uv` entries untouched.

```yaml
  # Base images are pinned tag@digest (GitHub #60). These entries refresh the
  # digests weekly as one grouped PR per ecosystem. Version jumps (python
  # 3.12 → 3.13, node 22 → 24) are ignored: they are deliberate upgrades with
  # their own issue, not a digest refresh.
  - package-ecosystem: docker
    directories:
      - /app
      - /services/pipeline
      - /services/image-scanner
      - /services/process-runtime
      - /infra/titiler
      - /infra/proxy-policy
    schedule:
      interval: weekly
    groups:
      base-image-digests:
        patterns: ["*"]
    ignore:
      - dependency-name: python
        update-types: [version-update:semver-major, version-update:semver-minor]
      - dependency-name: node
        update-types: [version-update:semver-major]
      - dependency-name: ghcr.io/astral-sh/uv
        update-types: [version-update:semver-major, version-update:semver-minor]
  - package-ecosystem: docker-compose
    directories:
      - /
      - /infra
    schedule:
      interval: weekly
    groups:
      compose-image-digests:
        patterns: ["*"]
```

Run the test again. Expected: PASS.

- [ ] **Step 3: Document the manual bump in `docs/backend.md`**

Add this subsection (adjust the heading level to match its neighbors):

```markdown
### Base images (pinned by digest)

Every `FROM` and every third-party compose `image:` is `tag@sha256:<index digest>`
(GitHub #60); `services/pipeline/tests/test_base_image_pins.py` fails CI on an
unpinned one. Dependabot's `docker` and `docker-compose` entries refresh the
digests weekly. To bump by hand (an urgent CVE):

    docker buildx imagetools inspect python:3.12-slim-trixie --format '{{json .Manifest}}'

Take `.digest`, the multi-arch index, never a per-platform manifest. Replace it
everywhere the tag appears (`git grep -n 'python:3.12-slim-trixie@'`), rebuild,
and for the process-runtime images re-add the new `:latest` digest on
`/images`: the runtime is scanned under the default policy like any user image.
```

- [ ] **Step 4: Gates and commit**

Run the full gate set from Global Constraints. Expected: green.

```bash
git add .github/dependabot.yml services/pipeline/tests/test_base_image_pins.py docs/backend.md
git commit -m "C: Dependabot refreshes base-image digests; manual bump documented (#60)"
```

---

### Task 3: `images-seed` picks the newest live row for a reference+tag

**Why:** after this PR merges, CI pushes a new `:latest` digest for the runtime, and the operator re-adds it on `/images`. That makes a second row for `(ghcr.io/techtraverse/stac-higher-process-runtime, latest)`, while the old bookworm row still carries its admin exception. `AppClient.find_image` returns the first non-revoked row in the API's order. Today that order is `created_at DESC` (`app/src/lib/images/storage.ts`), but nothing in the seed pins it, and `FakeClient` duplicates the loop rather than testing it. Make the choice explicit and tested: **the newest non-revoked row wins.** Deliberately NOT "newest approved": while the new row is still scanning, that would fall back to the old excepted digest and deploy on it silently. With "newest live", the seed waits for the new scan, and a rejected new row fails loudly in `require_deployable`.

**Files:**
- Modify: `services/pipeline/src/pipeline/demo/images/seed.py` (`AppClient.find_image`, a new `pick_image`, `EXCEPTION_REASON`)
- Modify: `services/pipeline/tests/test_demo_images.py` (`_image` gains `created_at`; `FakeClient.find_image` delegates to `pick_image`; new tests)
- Modify: `services/pipeline/src/pipeline/demo/README.md` (images loop section)

**Interfaces:**
- Produces: `pick_image(images: list[dict], reference: str, tag: str | None) -> dict | None` in `pipeline.demo.images.seed`.

- [ ] **Step 1: Write the failing tests**

In `services/pipeline/tests/test_demo_images.py`: add `pick_image` to the import list from `pipeline.demo.images.seed`, and give `_image` a keyword `created_at: str = "2026-09-30T00:00:00Z"` that it puts into the returned dict as `"created_at": created_at`. Then add:

```python
def test_pick_image_prefers_the_newest_live_row():
    ref, tag = "ghcr.io/techtraverse/stac-higher-process-runtime", "latest"
    old = _image(
        "old", ref, tag, "approved", digest="sha256:old",
        exception={"reason": "x"}, created_at="2026-09-30T10:00:00Z",
    )
    new = _image("new", ref, tag, "scanning", digest="sha256:new", created_at="2026-10-01T10:00:00Z")
    # Whatever order the API answers in, the newest live row wins, even
    # while it is still scanning, so the seed waits for it instead of
    # deploying on the old excepted digest.
    assert pick_image([old, new], ref, tag)["id"] == "new"
    assert pick_image([new, old], ref, tag)["id"] == "new"


def test_pick_image_skips_revoked_and_other_tags():
    ref = "ghcr.io/techtraverse/stac-higher-process-runtime"
    revoked = _image("rev", ref, "latest", "revoked", created_at="2026-10-02T00:00:00Z")
    other_tag = _image("v1", ref, "v1", "approved", created_at="2026-10-03T00:00:00Z")
    live = _image("live", ref, "latest", "approved", created_at="2026-09-01T00:00:00Z")
    assert pick_image([revoked, other_tag, live], ref, "latest")["id"] == "live"
    assert pick_image([revoked], ref, "latest") is None
    # tag=None matches any tag: the newest live row of the reference.
    assert pick_image([revoked, other_tag, live], ref, None)["id"] == "v1"
```

Run (from `services/pipeline`): `uv run pytest tests/test_demo_images.py -k pick_image -v`
Expected: FAIL with `ImportError: cannot import name 'pick_image'`.

- [ ] **Step 2: Implement `pick_image` and use it**

In `seed.py`, above `class AppClient`, add:

```python
def pick_image(images: list[dict], reference: str, tag: str | None) -> dict | None:
    """The NEWEST non-revoked row for `(reference, tag)` (tag None: any tag).

    A re-pushed tag (the platform runtime's `:latest` after a rebuild) gets a
    second row when it is re-added; the old row may still be approved through
    an exception. Newest wins, even mid-scan: the seed then waits for that
    scan rather than silently deploying on the older digest. A revoked row is
    terminal and never picked."""
    live = [
        image
        for image in images
        if image.get("reference") == reference
        and (tag is None or image.get("tag_at_add") == tag)
        and image.get("status") != "revoked"
    ]
    return max(live, key=lambda image: image.get("created_at") or "", default=None)
```

Replace the body of `AppClient.find_image` with:

```python
        query = reference if tag is None else f"{reference}:{tag}"
        result = self._call(f"/api/images?q={urllib.parse.quote(query)}")
        return pick_image(result.get("images", []), reference, tag)
```

The app returns `created_at` as an ISO-8601 UTC string (`toISOString()`), so string comparison orders correctly.

In `test_demo_images.py`, replace the body of `FakeClient.find_image` with:

```python
        self.calls.append("find_image")
        return pick_image(list(self.images.values()), reference, tag)
```

Change `EXCEPTION_REASON` in `seed.py` so it no longer claims #60 is pending. Search the tests for `EXCEPTION_REASON` or its text first, and update any assertion that quotes it:

```python
EXCEPTION_REASON = "images-seed: platform runtime image for the kind-2 GOES demo (C-5)"
```

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_demo_images.py -v`. Expected: all PASS, old and new. If an existing test relied on dict insertion order to pick between two live rows of the same reference+tag, give those rows distinct `created_at` values that keep its intent, and note it in your report.

- [ ] **Step 4: Update the images-loop README**

In `services/pipeline/src/pipeline/demo/README.md`, "Images loop (`images-seed`)":
- Where it says the runtime image needs `--exception-days` or a manual Grant exception because it fails the policy, say that since GitHub #60 the runtime is built on Debian trixie and passes the default policy, so no exception is needed. `--exception-days` stays for a future failing digest.
- Add one paragraph: after a rebuild pushes a new `:latest` digest, add the image again on `/images` (a new digest is a new row), then re-run `images-seed`; it picks the newest non-revoked row for the tag. Revoke the old row on `/images` once nothing uses it.

- [ ] **Step 5: Gates and commit**

Run the full gate set from Global Constraints. Expected: green.

```bash
git add services/pipeline/src/pipeline/demo/images/seed.py services/pipeline/tests/test_demo_images.py services/pipeline/src/pipeline/demo/README.md
git commit -m "C: images-seed picks the newest live row for a tag, so a re-added runtime digest wins (#60)"
```

---

### Task 4 (LEAD ONLY): build, preview-scan, PR, merge, re-add, demo

Docker policy `smoke`: rebuild + restart + canary, no probes.

**Scan choice:** the binding policy scan happens **after merge** on the CI-pushed `ghcr.io/techtraverse/stac-higher-process-runtime:latest` digest, added on `/images` as an admin. Before merge, the lead runs a **local Grype preview** of the locally built runtime with the platform scanner image, as in the evidence table. No throwaway registry push: that publishes an image outside the project's GHCR flow for no gain, since the preview already shows the CVE set and the policy inputs (severity, fix state, EPSS, KEV). If the post-merge verdict is not `approved`, that is a `fix/` PR, and the 2026-10-14 exception remains the stopgap until it lands.

- [ ] Rebase onto fresh `origin/main` and re-run every gate.
- [ ] `docker buildx bake -f services/process-runtime/docker-bake.hcl runtime`, `… stactools`, `… image-scanner`; `docker compose build pipeline`; `docker build -f app/Dockerfile --build-context fixtures=tests/contract-fixtures --build-context hardware=infra/hardware-profiles --build-context imagepolicy=infra/image-policy .`; and the titiler and proxy-policy builds. Save each log and `grep -n ERROR` it: BuildKit can exit 0 on a failed pull.
- [ ] `docker buildx imagetools inspect` on each pinned ref: each must show a manifest list (Review Focus #3).
- [ ] Grype preview of the built runtime and stactools images: 0 CRITICAL, no KEV, no fixed HIGH with EPSS ≥ 0.1.
- [ ] `docker compose up -d pipeline`. Canary: the standing GOES demo (`goes-geocolor`, kind 1) completes a run on the rebuilt local `stac-higher-process-runtime:local`.
- [ ] PR (`Closes #60`; the body lists every pin old → new, the base change and why, the bump mechanism, the before/after verdict, and the `images-seed` change). CI green, squash-merge, remove the worktree.
- [ ] After CI on `main` pushes `:latest`: admin dev server on :4399 (`DEV_AUTH_IDENTITY='{"roles":["admin"]}' ASTRO_DEV_BACKGROUND=0 npm run dev -- --host 127.0.0.1 --port 4399` from `app/`, main checkout `.env` sourced by absolute path). Add the runtime on `/images` and confirm the new digest is `approved` with no exception. Run `images-seed --app-url http://127.0.0.1:4399` and confirm it reports the NEW digest, and that one `goes-geocolor-img` run completes on it. Revoke the old excepted row once nothing uses it, and record that.
