# G-4 · Tile Server Href Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make titiler-pgstac able to open the platform's canonical assets by mapping `/api/assets/{collection}/{item}/{filename}` hrefs to the matching `s3://` object, without changing what the catalog stores.

**Architecture:** A derived image `infra/titiler/` on the pinned upstream `ghcr.io/stac-utils/titiler-pgstac:1.7.2`. It carries one small Python package whose `main` module wraps `_get_asset_info` on the two upstream readers (`PgSTACReader` for item endpoints, `SimpleSTACReader` for collection/search mosaics), rewriting the returned URL, and then imports the upstream `app` unchanged. The mapping itself is a pure function in its own module, tested without titiler installed. Compose builds and runs the derived image; CI build-verifies it.

**Tech Stack:** Python 3.12, titiler-pgstac 1.7.2 (rio-tiler 7.x), Docker compose, GitHub Actions matrix build, pytest.

**Spec:** `docs/superpowers/specs/2026-09-01-goes-geocolor-loop-design.md` §7.1.

## Global Constraints

- Worktree off `ai/main`: `git worktree add .claude/worktrees/goes-g4 -b ai/goes-g4 ai/main`.
- Base image tag stays `1.7.2` — the pin compose uses today; bump it in the Dockerfile AND `docs/serving.md` together, never one alone.
- Catalog items are NOT changed: no `alternate` hrefs, no absolute hrefs. The mapping lives only in the tile server.
- `PLATFORM_ASSET_BUCKET` (the bucket) and `ASSET_HREF_BASE` (default `/api/assets`) are the only knobs; they mirror the pipeline's `STAGING_BUCKET` / `ASSET_HREF_BASE`.
- The mapping is a string substitution over the deterministic key layout `assets/{collection}/{item}/{filename}` (ADR 0005 §5.3, `services/pipeline/src/pipeline/storage/keys.py`). Any href that does not match exactly three non-empty, non-traversal segments passes through untouched.
- The linux/amd64-only constraint of the upstream image is preserved (`platform: linux/amd64` in compose; single-platform in CI).
- Docker is a singleton resource: only the lead runs `docker compose` locally; the implementer verifies the image builds (`docker compose build titiler`) and runs the pure tests.

---

### Task 1: The pure mapping module and its tests

**Files:**
- Create: `infra/titiler/pyproject.toml`
- Create: `infra/titiler/stac_higher_titiler/__init__.py`
- Create: `infra/titiler/stac_higher_titiler/hrefmap.py`
- Create: `infra/titiler/tests/test_hrefmap.py`

**Interfaces:**
- Produces: `map_canonical_href(href: object, *, bucket: str, base: str = "/api/assets") -> object` — returns the `s3://…` URL for a canonical href, otherwise returns `href` unchanged (including non-strings).

- [ ] **Step 1: Create the package skeleton**

`infra/titiler/pyproject.toml`:

```toml
[project]
name = "stac-higher-titiler"
version = "0.1.0"
description = "Derived titiler-pgstac entrypoint mapping platform /api/assets hrefs to s3:// (GOES spec §7.1, I-68)."
requires-python = ">=3.12"
license = { text = "MIT" }
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8", "ruff>=0.8"]
# Only needed for tests/test_readers.py, which is skipped when absent.
titiler = ["titiler.pgstac==1.7.2", "psycopg[binary]>=3.2"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["stac_higher_titiler"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
target-version = "py312"
line-length = 100
```

`infra/titiler/stac_higher_titiler/__init__.py`:

```python
"""stac-higher's derived titiler-pgstac entrypoint (see hrefmap.py, main.py)."""
```

- [ ] **Step 2: Write the failing tests**

`infra/titiler/tests/test_hrefmap.py`:

```python
import pytest

from stac_higher_titiler.hrefmap import map_canonical_href

B = "stac-higher"


def test_maps_a_canonical_href_to_the_platform_object():
    assert (
        map_canonical_href("/api/assets/goes/item-1/visual.tif", bucket=B)
        == "s3://stac-higher/assets/goes/item-1/visual.tif"
    )


def test_url_decodes_each_segment():
    assert (
        map_canonical_href("/api/assets/my%20coll/OR_ABI%2Bx/a%20b.tif", bucket=B)
        == "s3://stac-higher/assets/my coll/OR_ABI+x/a b.tif"
    )


def test_honours_a_custom_base():
    assert (
        map_canonical_href("/assets/c/i/f.tif", bucket=B, base="/assets")
        == "s3://stac-higher/assets/c/i/f.tif"
    )


def test_strips_a_file_scheme_prefix_pystac_may_add():
    # pystac's get_absolute_href() can turn a rooted path into file:///…
    assert (
        map_canonical_href("file:///api/assets/c/i/f.tif", bucket=B)
        == "s3://stac-higher/assets/c/i/f.tif"
    )


@pytest.mark.parametrize(
    "href",
    [
        "https://noaa-goes19.s3.amazonaws.com/ABI-L2-MCMIPC/x.nc",
        "s3://other/assets/c/i/f.tif",
        "/api/assets/c/i",  # too few segments
        "/api/assets/c/i/f/g.tif",  # too many
        "/api/assets/c//f.tif",  # empty segment
        "/api/assets/c/../f.tif",  # traversal
        "/api/assetsX/c/i/f.tif",  # base must end at a separator
        "vrt:///api/assets/c/i/f.tif?bands=1",  # vrt wrapper is left alone
        "",
        None,
        42,
    ],
)
def test_everything_else_passes_through_unchanged(href):
    assert map_canonical_href(href, bucket=B) is href


def test_query_string_is_dropped_only_when_mapping():
    assert (
        map_canonical_href("/api/assets/c/i/f.tif?x=1", bucket=B)
        == "s3://stac-higher/assets/c/i/f.tif"
    )
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd infra/titiler && uv run --extra dev pytest -v`
Expected: FAIL with `ModuleNotFoundError: stac_higher_titiler.hrefmap`.

- [ ] **Step 4: Implement**

`infra/titiler/stac_higher_titiler/hrefmap.py`:

```python
"""Map platform-canonical asset hrefs to the objects behind them.

Items carry app-relative ``/api/assets/{collection}/{item}/{filename}`` hrefs
(ADR 0005: bytes are reachable only through the app). GDAL cannot open those,
so the tile server — which already holds credentials for the platform bucket —
rewrites them to ``s3://{bucket}/assets/{collection}/{item}/{filename}``.

The rewrite is a pure string substitution over the deterministic key layout
(``pipeline/storage/keys.py`` / ``app/src/lib/storage/keys.ts``). Anything that
is not exactly a canonical href passes through untouched: absolute URLs,
``s3://`` hrefs, ``vrt://`` wrappers, malformed paths. Never raise here — a
reader failure downstream is a clearer error than a mapper exception.
"""

from __future__ import annotations

from urllib.parse import unquote

_FILE_SCHEME = "file://"
_CANONICAL_PREFIX = "assets"


def map_canonical_href(href: object, *, bucket: str, base: str = "/api/assets") -> object:
    if not isinstance(href, str) or not href:
        return href
    candidate = href
    if candidate.startswith(_FILE_SCHEME):
        candidate = candidate[len(_FILE_SCHEME):]
    prefix = base.rstrip("/") + "/"
    if not candidate.startswith(prefix):
        return href
    rest = candidate[len(prefix):].split("?", 1)[0]
    parts = rest.split("/")
    if len(parts) != 3:
        return href
    segments = [unquote(p) for p in parts]
    for seg in segments:
        if not seg or seg in (".", "..") or "/" in seg or "\\" in seg:
            return href
    collection, item_id, filename = segments
    return f"s3://{bucket}/{_CANONICAL_PREFIX}/{collection}/{item_id}/{filename}"
```

- [ ] **Step 5: Run to verify they pass**

Run: `cd infra/titiler && uv run --extra dev pytest -v && uv run --extra dev ruff check .`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add infra/titiler/pyproject.toml infra/titiler/stac_higher_titiler/__init__.py infra/titiler/stac_higher_titiler/hrefmap.py infra/titiler/tests/test_hrefmap.py
git commit -m "feat(titiler): pure /api/assets -> s3:// href mapper (G-4)"
```

---

### Task 2: The entrypoint that wraps both upstream readers

**Files:**
- Create: `infra/titiler/stac_higher_titiler/main.py`
- Create: `infra/titiler/tests/test_readers.py`

**Interfaces:**
- Consumes: `map_canonical_href` (Task 1); upstream `titiler.pgstac.reader.PgSTACReader` / `SimpleSTACReader` (rio-tiler 7 `_get_asset_info(self, asset: str) -> AssetInfo`, where `AssetInfo` is a TypedDict with `url`).
- Produces: `stac_higher_titiler.main:app` — the upstream FastAPI app with both readers patched at import time; env `PLATFORM_ASSET_BUCKET` (required), `ASSET_HREF_BASE` (default `/api/assets`).

- [ ] **Step 1: Write the failing tests (skipped when titiler is not installed)**

`infra/titiler/tests/test_readers.py`:

```python
"""Both upstream readers rewrite canonical hrefs after `main` is imported.

Needs titiler.pgstac (heavy); skipped when it is absent so the pure tests
still run anywhere. Locally: `uv run --extra dev --extra titiler pytest`.
"""

import importlib
import os

import pytest

pytest.importorskip("titiler.pgstac")


@pytest.fixture(scope="module")
def patched(monkeypatch_module):
    monkeypatch_module.setenv("PLATFORM_ASSET_BUCKET", "stac-higher")
    monkeypatch_module.setenv("ASSET_HREF_BASE", "/api/assets")
    # Importing main patches the classes and imports the upstream app, which
    # needs no database at import time.
    return importlib.import_module("stac_higher_titiler.main")


@pytest.fixture(scope="module")
def monkeypatch_module():
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    yield mp
    mp.undo()


def test_simple_reader_maps_canonical_href(patched):
    from titiler.pgstac.reader import SimpleSTACReader

    item = {
        "id": "i1",
        "collection": "c",
        "bbox": [-10, -10, 10, 10],
        "assets": {"visual": {"href": "/api/assets/c/i1/visual.tif", "type": "image/tiff"}},
    }
    with SimpleSTACReader(item) as src:
        assert src._get_asset_info("visual")["url"] == "s3://stac-higher/assets/c/i1/visual.tif"


def test_simple_reader_leaves_absolute_href_alone(patched):
    from titiler.pgstac.reader import SimpleSTACReader

    href = "https://noaa-goes19.s3.amazonaws.com/x.nc"
    item = {"id": "i1", "collection": "c", "bbox": [0, 0, 1, 1], "assets": {"d": {"href": href}}}
    with SimpleSTACReader(item) as src:
        assert src._get_asset_info("d")["url"] == href


def test_pgstac_item_reader_maps_canonical_href(patched):
    import pystac
    from titiler.pgstac.reader import PgSTACReader

    item = pystac.Item.from_dict(
        {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": "i1",
            "collection": "c",
            "geometry": {"type": "Point", "coordinates": [0, 0]},
            "bbox": [0, 0, 1, 1],
            "properties": {"datetime": "2026-01-01T00:00:00Z"},
            "links": [],
            "assets": {"visual": {"href": "/api/assets/c/i1/visual.tif", "type": "image/tiff"}},
        }
    )
    with PgSTACReader(item) as src:
        assert src._get_asset_info("visual")["url"] == "s3://stac-higher/assets/c/i1/visual.tif"


def test_missing_bucket_env_fails_at_import(monkeypatch):
    monkeypatch.delenv("PLATFORM_ASSET_BUCKET", raising=False)
    import sys

    sys.modules.pop("stac_higher_titiler.main", None)
    with pytest.raises(RuntimeError, match="PLATFORM_ASSET_BUCKET"):
        importlib.import_module("stac_higher_titiler.main")
```

Note for the implementer: the module-import-once fixture ordering matters — run the missing-env test in its own process if it interferes (`pytest -p no:cacheprovider tests/test_readers.py::test_missing_bucket_env_fails_at_import`), or move it to `tests/test_main_env.py`. Keep whichever arrangement makes all four pass deterministically.

- [ ] **Step 2: Run to verify they fail**

Run: `cd infra/titiler && uv run --extra dev --extra titiler pytest tests/test_readers.py -v`
Expected: FAIL with `ModuleNotFoundError: stac_higher_titiler.main` (or all SKIPPED if titiler could not install — then rely on the Docker build in Task 3 and note it in the commit).

- [ ] **Step 3: Implement**

`infra/titiler/stac_higher_titiler/main.py`:

```python
"""titiler-pgstac with platform href mapping (GOES spec §7.1; ISSUES I-68).

Why a wrapper and not a copy of ``titiler.pgstac.main``: the upstream module
builds its factories at import time with the reader CLASSES baked in as
attribute defaults (``PGSTACBackend.reader``, ``MosaicTilerFactory.
dataset_reader``, ``MultiBaseTilerFactory(reader=PgSTACReader)``). Patching
the classes' ``_get_asset_info`` BEFORE importing the upstream app changes
every path at once without vendoring ~280 lines we would then have to keep in
step with upstream. The one place to touch on a titiler-pgstac bump is here
(rio-tiler 9 / titiler-pgstac 3.x changed ``_get_asset_info``'s return shape,
not its name).

Configuration:
- ``PLATFORM_ASSET_BUCKET`` (required): the platform bucket — the pipeline's
  ``STAGING_BUCKET``.
- ``ASSET_HREF_BASE`` (default ``/api/assets``): the pipeline's
  ``ASSET_HREF_BASE``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from titiler.pgstac.reader import PgSTACReader, SimpleSTACReader

from stac_higher_titiler.hrefmap import map_canonical_href

_bucket = os.environ.get("PLATFORM_ASSET_BUCKET", "").strip()
if not _bucket:
    raise RuntimeError(
        "PLATFORM_ASSET_BUCKET is required: the tile server maps /api/assets hrefs "
        "to s3://<bucket>/assets/... and cannot guess the bucket"
    )
_base = os.environ.get("ASSET_HREF_BASE", "/api/assets")


def _wrap(method: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    def _get_asset_info(self, asset):  # type: ignore[no-untyped-def]
        info = method(self, asset)
        info["url"] = map_canonical_href(info.get("url"), bucket=_bucket, base=_base)
        return info

    _get_asset_info.__wrapped__ = method  # type: ignore[attr-defined]
    _get_asset_info.__doc__ = method.__doc__
    return _get_asset_info


# Idempotent: re-importing in tests must not double-wrap.
for _cls in (PgSTACReader, SimpleSTACReader):
    _current = _cls.__dict__.get("_get_asset_info", getattr(_cls, "_get_asset_info"))
    if not hasattr(_current, "__wrapped__"):
        _cls._get_asset_info = _wrap(_current)  # type: ignore[method-assign]

from titiler.pgstac.main import app  # noqa: E402  (patch first, then import)

__all__ = ["app"]
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd infra/titiler && uv run --extra dev --extra titiler pytest -v && uv run --extra dev ruff check .`
Expected: PASS (or the reader tests SKIPPED with the pure tests passing, if titiler could not be installed locally — then Task 3's build is the proof).

- [ ] **Step 5: Commit**

```bash
git add infra/titiler/stac_higher_titiler/main.py infra/titiler/tests/test_readers.py
git commit -m "feat(titiler): entrypoint wrapping PgSTACReader and SimpleSTACReader with the href mapper (G-4)"
```

---

### Task 3: Dockerfile, compose, CI

**Files:**
- Create: `infra/titiler/Dockerfile`
- Create: `infra/titiler/README.md`
- Modify: `docker-compose.yml` (the `titiler` service, ~lines 320–349)
- Modify: `.github/workflows/containers.yml` (matrix ~lines 24–31, platforms ~line 62)

**Interfaces:**
- Produces: image `stac-higher-titiler:local` built from the repo root; compose runs `uvicorn stac_higher_titiler.main:app`.

- [ ] **Step 1: Dockerfile**

`infra/titiler/Dockerfile`:

```dockerfile
# Derived titiler-pgstac image carrying the stac-higher href mapper
# (GOES spec §7.1, ISSUES I-68).
#
# The base tag is PINNED and must move in lockstep with docs/serving.md. The
# wrapper patches rio-tiler 7's `_get_asset_info`; titiler-pgstac 3.x
# (rio-tiler 9) changed that method's return shape — re-run
# infra/titiler/tests before bumping.
#
# Build context is the REPO ROOT (set in docker-compose.yml):
#   docker build -f infra/titiler/Dockerfile .
FROM ghcr.io/stac-utils/titiler-pgstac:1.7.2

COPY infra/titiler/stac_higher_titiler /opt/stac-higher/titiler/stac_higher_titiler
ENV PYTHONPATH=/opt/stac-higher/titiler \
    MODULE_NAME=stac_higher_titiler.main \
    VARIABLE_NAME=app
```

`infra/titiler/README.md` (short): what it is, the two env vars, how to run the tests (`uv run --extra dev pytest`; `--extra titiler` for the reader tests), and the bump procedure.

- [ ] **Step 2: Compose**

Replace the `titiler` service's `image:` + `command:` lines and add the two env vars:

```yaml
  titiler:
    # rasterio/psycopg wheels are not published for arm64 (eoAPI does the same)
    platform: linux/amd64
    # Derived image: upstream 1.7.2 + the /api/assets -> s3:// href mapper
    # (infra/titiler, GOES spec §7.1). Context is the repo root.
    build:
      context: .
      dockerfile: infra/titiler/Dockerfile
    image: stac-higher-titiler:local
    command: uvicorn stac_higher_titiler.main:app --host 0.0.0.0 --port 8084
    environment:
      # …every existing entry unchanged…
      # Platform href mapping: mirror the pipeline's STAGING_BUCKET /
      # ASSET_HREF_BASE so the tiler resolves the same objects the app serves.
      - PLATFORM_ASSET_BUCKET=${STAGING_BUCKET:-stac-higher}
      - ASSET_HREF_BASE=${ASSET_HREF_BASE:-/api/assets}
```

- [ ] **Step 3: CI matrix**

In `.github/workflows/containers.yml` add a matrix entry and make platforms per-image overridable:

```yaml
          - name: titiler
            dockerfile: infra/titiler/Dockerfile
            context: .
            # upstream base is linux/amd64 only
            platforms: linux/amd64
```

and change the build step's `platforms:` to

```yaml
          platforms: ${{ matrix.image.platforms || (env.PUSH == 'true' && 'linux/amd64,linux/arm64' || 'linux/amd64') }}
```

- [ ] **Step 4: Build-verify the image**

Run from the worktree root: `docker compose build titiler`
Expected: builds. Then, without starting the stack, confirm the module imports inside the image:

```bash
docker run --rm -e PLATFORM_ASSET_BUCKET=stac-higher stac-higher-titiler:local \
  python -c "import stac_higher_titiler.main as m; print(m.app.title)"
```

Expected: prints the upstream app title. (Starting the service against the database is the lead's step in Task 4.)

- [ ] **Step 5: Commit**

```bash
git add infra/titiler/Dockerfile infra/titiler/README.md docker-compose.yml .github/workflows/containers.yml
git commit -m "build(titiler): derived image with href mapping; compose + CI build it (G-4)"
```

---

### Task 4: Docs, ISSUES, FEATURES; live check; merge

**Files:**
- Modify: `docs/serving.md` (image table row; "Asset-href caveat" bullet; Env table; "Trying it")
- Modify: `docs/ISSUES.md` (I-68)
- Modify: `docs/FEATURES.md` (the OGC serving row)
- Modify: `AGENTS.md` (the OGC serving bullet's "not resolvable by the tiler (I-68)" clause)
- Modify: `TODO.md` (tick G-4)

- [ ] **Step 1: Documentation edits**

`docs/serving.md`:
- Table row: `titiler` → image `stac-higher-titiler:local` (derived from `ghcr.io/stac-utils/titiler-pgstac:1.7.2`, `infra/titiler/`).
- Replace the "Asset-href caveat (raster)" bullet with: "**Canonical hrefs are mapped in the tile server.** Items keep their app-relative `/api/assets/{collection}/{item}/{filename}` hrefs; the derived image rewrites them to `s3://{PLATFORM_ASSET_BUCKET}/assets/{collection}/{item}/{filename}` inside both readers (`infra/titiler/stac_higher_titiler/hrefmap.py`), so platform assets tile with no change to the catalog. Reference-mode absolute URLs and explicit `s3://` hrefs pass through as before. This is the *local* answer to I-68; the cloud deployment chooses between the same mapping (with the deployment's bucket) and presign integration."
- Env table: add `PLATFORM_ASSET_BUCKET` (default `${STAGING_BUCKET:-stac-higher}`) and `ASSET_HREF_BASE` (default `/api/assets`).
- "Trying it": add `docker compose build titiler` before `up`, and a tile fetch example: `curl -o tile.png "http://localhost:8084/collections/<coll>/items/<item>/tiles/WebMercatorQuad/0/0/0.png?assets=visual"`.
- Add a "Bumping titiler-pgstac" note: rio-tiler 9 changed `_get_asset_info`'s return shape; re-run `infra/titiler/tests` with `--extra titiler` before moving the pin.

`docs/ISSUES.md` I-68: change the status glyph to 🟡 (accepted/mitigated) and append: "**2026-09 (G-4):** the local half is closed — the compose tile server is a derived image that maps canonical hrefs to the platform bucket (`infra/titiler/`). Remaining: the cloud deployment's choice (same mapping vs presign integration) and the fact that the tiler's bucket credentials see everything (I-1)."

`docs/FEATURES.md`: append to the serving row: "G-4 (2026-09): derived titiler image maps `/api/assets` hrefs → `s3://` so canonical assets tile locally."

`AGENTS.md`: change "Platform `/api/assets/...` hrefs are not resolvable by the tiler (I-68)." to "The compose tiler is a derived image that maps platform `/api/assets/...` hrefs to the bucket (G-4; I-68 keeps the cloud half)."

- [ ] **Step 2: Lead-only live check**

With the stack up (`docker compose up -d --build titiler`), pick any copy-mode item with a GeoTIFF asset (or upload one through the UI), then:

```bash
curl -s http://localhost:8084/collections/<coll>/items/<item>/info | head -c 400
curl -s -o /tmp/tile.png -w '%{http_code} %{content_type}\n' \
  "http://localhost:8084/collections/<coll>/items/<item>/tiles/WebMercatorQuad/0/0/0.png?assets=<asset>"
```

Expected: `info` lists the asset; the tile request returns `200 image/png`. If it returns a GDAL open error mentioning `/api/assets`, the wrapper did not apply — check `docker compose logs titiler` for the import path and that `PLATFORM_ASSET_BUCKET` is set.

- [ ] **Step 3: Verify, commit, merge**

Run `npm run verify` from the worktree root (docs-only app changes, but the gate is the gate), and `cd infra/titiler && uv run --extra dev pytest`.

```bash
git add docs/serving.md docs/ISSUES.md docs/FEATURES.md AGENTS.md
git commit -m "docs(serving): tile server maps canonical hrefs; I-68 local half closed (G-4)"
# tick G-4 in TODO.md, commit "chore(todo): G-4 done"
git checkout ai/main && git merge ai/goes-g4 --no-ff
git worktree remove .claude/worktrees/goes-g4 && git branch -d ai/goes-g4
```

Do NOT push `ai/main`.
