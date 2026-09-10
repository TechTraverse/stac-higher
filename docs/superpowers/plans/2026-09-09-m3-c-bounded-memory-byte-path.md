# M3-C · Bounded-memory byte path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Worker memory stops scaling with asset size: EXTRACT opens rasters through a GDAL `/vsis3` URI instead of a whole-object buffer, FETCH server-side-copies when the gate allows and streams a bounded multipart upload otherwise, and the envelope (`GDAL_CACHEMAX`, transfer chunk size, transfer concurrency) is configuration — so M3-D's concurrency raise cannot become an OOM (closes I-19 and I-26; I-83 stays as the documented SFTP/FTP limit).

**Architecture:** A new value type `RasterLocation` (URI + the GDAL session/options it must be opened under) travels from whoever knows where the bytes are (the platform client for copy mode, the `S3Adapter` for reference mode) to the two rasterio call sites, which accept `bytes | RasterLocation` and open either. `MemberByteSource` keeps `read()` for small sidecars and gains `locate()`; `build_item` prefers `locate()` and falls back to `read()` for adapters that cannot locate (SFTP/FTP — I-83). FETCH gets a `TransferPolicy` (copy allowed? chunk bytes, concurrency) computed once per job from the adapter and the platform endpoint by reusing `delivery.transfer.can_server_side_copy`; the stage tries `CopyObject` (boto3's managed `copy`, which handles multipart copies) and falls back to a streamed `upload_fileobj` whose buffers are bounded by `chunk × concurrency`, hashing sha256 on the way through. Three settings make the envelope explicit; the pipeline README writes the formula down.

**Tech Stack:** Python 3.12, boto3 ≥ 1.43 (`TransferConfig`, `upload_fileobj`, managed `copy`), rasterio ≥ 1.5 (`rasterio.session.AWSSession`, `rasterio.Env`), rio-stac 0.12.0, pytest, ruff, uv.

**Spec:** `docs/superpowers/specs/2026-09-01-m3-noaa-scale-design.md` §3 (M3-C row, dependency spine `M3-B → M3-C → M3-D`), §5 ("M3-C is second"), and `docs/superpowers/specs/2026-08-31-m3-scoping-notes.md` M3-S-E (the measurements, the envelope formula, the four caveats). Queue text: `TODO.md` M3-C slice.

## Global Constraints

- **Worktree:** `git worktree add .claude/worktrees/m3-c-byte-path -b ai/m3-c-byte-path ai/main` (after M3-B has merged — Task 0 checks). Pipeline-only: no `npm install` needed.
- **Gates before merge:** from `services/pipeline/`: `uv run pytest -q` and `uv run ruff check .`; from the worktree root `npm run verify` (the lead runs it after merge; teammates run pytest + ruff only). Teammates never run e2e, the dev server, or Docker.
- **ADR 0001:** the pipeline never runs DDL. Nothing here touches the schema; the ledger's existing `checksum` column is written with `None` on the copy path (no bytes were read — an honest absence; nothing reads the column today).
- **No new dependency.** `boto3`, `rasterio` and `rio-stac` are already pinned; `rasterio.session.AWSSession` and `boto3.s3.transfer.TransferConfig` ship with them.
- **Egress (AGENTS.md):** every endpoint a GDAL session or a boto3 client dials is the PINNED one — `S3Adapter._pinned_endpoint()` for a source, `platform.pinned_endpoint_url(...)` for the platform bucket. No new code resolves a host itself.
- **rasterio refuses raw `AWS_*` GDAL options** (`rasterio.Env(AWS_S3_ENDPOINT=…)` raises `EnvError`). The working form is `rasterio.Env(session=AWSSession(..., endpoint_url="host:port"), AWS_HTTPS="NO", AWS_VIRTUAL_HOSTING="FALSE")` — `AWS_HTTPS` and `AWS_VIRTUAL_HOSTING` are the two the library allows. `endpoint_url` is `host[:port]` WITHOUT a scheme.
- **Credentials in GDAL (spec §5 review point):** a reference-mode `/vsis3` read puts a connection's decrypted credentials into an `AWSSession`. They are built INSIDE `S3Adapter` (which already holds them) and never returned as a dict — the adapter hands out a `RasterLocation` whose `session` is the opaque `AWSSession`. Nothing logs a location's session; `RasterLocation.__repr__` shows the URI only.
- **SFTP/FTP keep the buffered `get()` (I-83).** `StorageAdapter.gdal_location()` raises `NotImplementedError` by default and `open()` defaults to `BytesIO(await self.get(path))`; only `S3Adapter` overrides both.
- **Memory envelope (write it down, S-E):** `per-worker peak RSS ≈ 255 MiB + (GDAL_CACHEMAX + FETCH_CHUNK_BYTES × FETCH_TRANSFER_CONCURRENCY) × WORKER_CONCURRENCY`. Defaults: `GDAL_CACHEMAX=64` (MB), `FETCH_CHUNK_BYTES=8388608` (8 MiB), `FETCH_TRANSFER_CONCURRENCY=4` → 96 MB per job, ~1.4 GB at M3-D's concurrency 12 (S-E's "~1.8 GB at 16").
- **Structured logging:** data in `extra={...}`; a fetch log line carries `transfer: "copy" | "stream"`.
- **Do not touch:** the delivery worker's copy/stream code (`delivery/worker.py`) — FETCH reuses the gate function and the pattern, not the code; `finalize/` (its same-bucket `platform.copy_object` stays); the pgstac writer.
- Commit messages end with:
  ```
  Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
  Claude-Session: <the executing session's URL>
  ```

---

### Task 0: Precondition — M3-B is on `ai/main`

**Files:** none.

- [ ] Verify from the worktree: `test -f services/pipeline/src/pipeline/db/pool.py && grep -n 'def get_async_pool' services/pipeline/src/pipeline/db/pool.py` prints a line, and `git log --oneline ai/main -20 | grep -i 'm3-b'` shows the M3-B merge. If not, STOP and report — M3-C builds on the pooled repos.

---

### Task 1: The three envelope settings

**Files:**
- Modify: `services/pipeline/src/pipeline/config.py` — module docstring env contract (append three bullets after the `DB_POOL_MAX` bullet); a constants block after `DEFAULT_DB_POOL_MAX = 16` (anchor: the M3-B pool constants — the last constants in the file); three dataclass fields after `db_pool_max: int = DEFAULT_DB_POOL_MAX`; three `from_env` kwargs after `db_pool_max=...`
- Modify: `services/pipeline/README.md` — the "Environment contract" table: three rows after the `DB_POOL_MAX` row
- Modify: `docker-compose.yml` — the pipeline service `environment:` list, after the `DB_POOL_MAX` line M3-B added
- Test: `services/pipeline/tests/test_config.py` (append)

**Interfaces:**
- Produces: `Settings.gdal_cachemax_mb: int` (default 64), `Settings.fetch_chunk_bytes: int` (default 8 MiB), `Settings.fetch_transfer_concurrency: int` (default 4); constants `DEFAULT_GDAL_CACHEMAX_MB`, `DEFAULT_FETCH_CHUNK_BYTES`, `DEFAULT_FETCH_TRANSFER_CONCURRENCY`. Env names: `GDAL_CACHEMAX`, `FETCH_CHUNK_BYTES`, `FETCH_TRANSFER_CONCURRENCY`.

- [ ] **Step 1: Failing test** — append to `tests/test_config.py`:

```python
def test_memory_envelope_settings_default_to_the_s_e_envelope():
    from pipeline.config import (
        DEFAULT_FETCH_CHUNK_BYTES,
        DEFAULT_FETCH_TRANSFER_CONCURRENCY,
        DEFAULT_GDAL_CACHEMAX_MB,
    )

    settings = Settings.from_env(env={})
    assert settings.gdal_cachemax_mb == DEFAULT_GDAL_CACHEMAX_MB == 64
    assert settings.fetch_chunk_bytes == DEFAULT_FETCH_CHUNK_BYTES == 8 * 1024 * 1024
    assert settings.fetch_transfer_concurrency == DEFAULT_FETCH_TRANSFER_CONCURRENCY == 4


def test_memory_envelope_settings_read_their_env_names():
    settings = Settings.from_env(
        env={"GDAL_CACHEMAX": "128", "FETCH_CHUNK_BYTES": "16777216", "FETCH_TRANSFER_CONCURRENCY": "2"}
    )
    assert settings.gdal_cachemax_mb == 128
    assert settings.fetch_chunk_bytes == 16 * 1024 * 1024
    assert settings.fetch_transfer_concurrency == 2
```
(Match how the file's other tests obtain `Settings` — copy their import line.)

- [ ] **Step 2:** `uv run pytest tests/test_config.py -q -k envelope` → FAIL (ImportError).

- [ ] **Step 3: Implement.** In `config.py`:

Docstring bullets (after the `DB_POOL_MAX` bullet, same style as its neighbours):
```
- ``GDAL_CACHEMAX``: GDAL block-cache ceiling in MB for EXTRACT's raster reads (M3-C, default 64).
  GDAL reads this variable natively; the pipeline also passes it into every ``rasterio.Env``.
- ``FETCH_CHUNK_BYTES``: multipart part size for streamed FETCH uploads (default 8 MiB).
- ``FETCH_TRANSFER_CONCURRENCY``: parts in flight per streamed FETCH upload (default 4).
```
Constants after `DEFAULT_DB_POOL_MAX = 16`:
```python
# --- Memory envelope (M3-C, spec §3 / S-E) ---------------------------------
#: GDAL block cache ceiling (MB). GDAL honours the GDAL_CACHEMAX env var on
#: its own; it is also passed explicitly into every rasterio.Env so the bound
#: holds even when the process env is not what the compose file set.
DEFAULT_GDAL_CACHEMAX_MB = 64
#: Streamed FETCH: multipart part size and parts in flight. Bounded memory per
#: streamed upload = FETCH_CHUNK_BYTES × FETCH_TRANSFER_CONCURRENCY (32 MiB).
DEFAULT_FETCH_CHUNK_BYTES = 8 * 1024 * 1024
DEFAULT_FETCH_TRANSFER_CONCURRENCY = 4
#: Per-worker peak RSS ≈ 255 MiB + (GDAL_CACHEMAX + FETCH_CHUNK_BYTES ×
#: FETCH_TRANSFER_CONCURRENCY) × WORKER_CONCURRENCY — independent of asset size.
```
Fields after `db_pool_max`:
```python
    gdal_cachemax_mb: int = DEFAULT_GDAL_CACHEMAX_MB
    fetch_chunk_bytes: int = DEFAULT_FETCH_CHUNK_BYTES
    fetch_transfer_concurrency: int = DEFAULT_FETCH_TRANSFER_CONCURRENCY
```
`from_env` kwargs after `db_pool_max=...`:
```python
            gdal_cachemax_mb=int(env.get("GDAL_CACHEMAX", str(DEFAULT_GDAL_CACHEMAX_MB))),
            fetch_chunk_bytes=int(env.get("FETCH_CHUNK_BYTES", str(DEFAULT_FETCH_CHUNK_BYTES))),
            fetch_transfer_concurrency=int(
                env.get("FETCH_TRANSFER_CONCURRENCY", str(DEFAULT_FETCH_TRANSFER_CONCURRENCY))
            ),
```
README rows (after `DB_POOL_MAX`):
```markdown
| `GDAL_CACHEMAX` | `64` | GDAL block-cache ceiling in MB for EXTRACT's `/vsis3` raster reads (M3-C). GDAL's own variable; also passed into every `rasterio.Env`. Part of the memory envelope below. |
| `FETCH_CHUNK_BYTES` | `8388608` | Multipart part size for streamed copy-mode FETCH uploads (M3-C). |
| `FETCH_TRANSFER_CONCURRENCY` | `4` | Parts in flight per streamed FETCH upload (M3-C). Per-upload buffer = `FETCH_CHUNK_BYTES × FETCH_TRANSFER_CONCURRENCY`. |
```
Compose (after the `DB_POOL_MAX` line):
```yaml
      - GDAL_CACHEMAX=${GDAL_CACHEMAX:-64}
      - FETCH_CHUNK_BYTES=${FETCH_CHUNK_BYTES:-8388608}
      - FETCH_TRANSFER_CONCURRENCY=${FETCH_TRANSFER_CONCURRENCY:-4}
```

- [ ] **Step 4:** `uv run pytest tests/test_config.py -q && uv run ruff check .` → green. Commit: `feat(pipeline): GDAL_CACHEMAX / FETCH_CHUNK_BYTES / FETCH_TRANSFER_CONCURRENCY — the M3-C memory envelope as settings`.

---

### Task 2: `RasterLocation` and the two rasterio call sites

**Files:**
- Create: `services/pipeline/src/pipeline/ingest/raster_io.py`
- Modify: `services/pipeline/src/pipeline/ingest/extract.py` — `geometry_from_raster` (anchor: `def geometry_from_raster(data: bytes)`), `build_raster_auto` (anchor: `raster_bytes: bytes,` parameter and the `with rasterio.io.MemoryFile(raster_bytes)` line), module docstring line 13 ("raster reads go through an in-memory `rasterio.MemoryFile`")
- Test: `services/pipeline/tests/test_raster_io.py` (new), `services/pipeline/tests/test_ingest_extract.py` (append)

**Interfaces:**
- Produces:
  ```python
  # pipeline/ingest/raster_io.py
  @dataclass(frozen=True)
  class RasterLocation:
      uri: str                       # "/vsis3/<bucket>/<key>" or a local path
      session: Any | None = None     # rasterio.session.Session (AWSSession) or None
      options: Mapping[str, str] = field(default_factory=dict)  # rasterio.Env kwargs: AWS_HTTPS, AWS_VIRTUAL_HOSTING, GDAL_CACHEMAX
      def __repr__(self) -> str: ...  # f"RasterLocation({self.uri!r})" — never the session
  RasterSource = bytes | RasterLocation
  @contextmanager
  def open_raster(source: RasterSource):  # yields an open rasterio dataset
  ```
  `geometry_from_raster(source: RasterSource)`, `build_raster_auto(..., raster: RasterSource, ...)` (parameter renamed from `raster_bytes`; positional order unchanged).

- [ ] **Step 1: Failing tests.** Create `tests/test_raster_io.py`:

```python
"""RasterLocation + open_raster (M3-C): a raster is opened where it lives."""

from __future__ import annotations

import pytest

from pipeline.ingest.raster_io import RasterLocation, open_raster
from test_ingest_extract import _geotiff_bytes  # the existing in-memory GeoTIFF fixture


def test_repr_never_shows_the_session():
    loc = RasterLocation(uri="/vsis3/b/k", session=object(), options={"AWS_HTTPS": "NO"})
    assert repr(loc) == "RasterLocation('/vsis3/b/k')"


def test_open_raster_opens_bytes_and_a_location_to_the_same_dataset(tmp_path):
    data = _geotiff_bytes()
    path = tmp_path / "scene.tif"
    path.write_bytes(data)
    with open_raster(data) as from_bytes, open_raster(RasterLocation(uri=str(path))) as from_path:
        assert from_bytes.bounds == from_path.bounds
        assert from_bytes.crs == from_path.crs
        assert from_bytes.count == from_path.count


def test_open_raster_applies_gdal_options_inside_the_env(tmp_path):
    import rasterio

    path = tmp_path / "scene.tif"
    path.write_bytes(_geotiff_bytes())
    loc = RasterLocation(uri=str(path), options={"GDAL_CACHEMAX": "7"})
    with open_raster(loc):
        assert rasterio.env.getenv().get("GDAL_CACHEMAX") == "7"


def test_open_raster_rejects_unknown_sources():
    with pytest.raises(TypeError):
        with open_raster(123):  # type: ignore[arg-type]
            pass
```
(If `_geotiff_bytes` is not importable from the test module because of pytest's rootdir/import mode, copy its body into a `conftest.py`-free local helper — check `tests/test_ingest_extract.py` for how it builds the GeoTIFF and replicate the same 8 lines.)

Append to `tests/test_ingest_extract.py`:

```python
def test_build_raster_auto_from_a_location_is_identical_to_bytes(tmp_path):
    from pipeline.ingest.raster_io import RasterLocation

    members = [_member("scene.tif")]
    cfg = parse_metadata({"strategy": "raster_auto"})
    data = _geotiff_bytes()
    path = tmp_path / "scene.tif"
    path.write_bytes(data)
    from_bytes = build_raster_auto("col", "scene", members, cfg, data, "/api/assets")
    from_loc = build_raster_auto(
        "col", "scene", members, cfg, RasterLocation(uri=str(path)), "/api/assets"
    )
    # S-E's claim, pinned: the item is the same whichever way the raster is opened
    # (rio-stac's `raster:bands` statistics included).
    assert from_loc == from_bytes


def test_geometry_from_raster_accepts_a_location(tmp_path):
    from pipeline.ingest.raster_io import RasterLocation

    path = tmp_path / "scene.tif"
    path.write_bytes(_geotiff_bytes())
    assert geometry_from_raster(RasterLocation(uri=str(path))) == geometry_from_raster(
        _geotiff_bytes()
    )
```
(Import `geometry_from_raster` at the top of the test file if it is not already imported.)

- [ ] **Step 2:** `uv run pytest tests/test_raster_io.py tests/test_ingest_extract.py -q -k "location or raster_io"` → FAIL (ModuleNotFoundError).

- [ ] **Step 3: Create `raster_io.py`:**

```python
"""Where a raster lives, and how to open it there (M3-C, spec §3 / S-E).

EXTRACT used to read a whole object into memory and hand rasterio a
`MemoryFile`: +145 MB of RSS for a 64 MB GeoTIFF, scaling with the asset.
A `RasterLocation` instead names a URI GDAL can range-read (`/vsis3/...` for
an object store, a plain path for a local file) together with the session and
GDAL config options it must be opened under; GDAL's block cache — capped by
`GDAL_CACHEMAX` — does the buffering, so memory is a setting, not a multiple
of the asset (S-E measured +55 MB for the same file, byte-identical item).

The session is opaque on purpose: for a reference-mode source it carries the
connection's decrypted credentials (`rasterio.session.AWSSession`), which is a
new place they live (spec §5). `__repr__` shows the URI only.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RasterLocation:
    uri: str
    #: A `rasterio.session.Session` (AWSSession for /vsis3) or None for a local
    #: path. Never logged, never serialised.
    session: Any | None = None
    #: GDAL config options for `rasterio.Env` — only the ones rasterio allows
    #: as kwargs (`AWS_HTTPS`, `AWS_VIRTUAL_HOSTING`, `GDAL_CACHEMAX`, ...).
    options: Mapping[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"RasterLocation({self.uri!r})"


RasterSource = bytes | RasterLocation


@contextmanager
def open_raster(source: RasterSource) -> Iterator[Any]:
    """Open ``source`` as a rasterio dataset: bytes through `/vsimem`, a
    `RasterLocation` in place under its own `rasterio.Env`. rasterio is
    imported lazily so non-raster code paths stay GDAL-free at import time."""
    import rasterio

    if isinstance(source, RasterLocation):
        env_kwargs: dict[str, Any] = dict(source.options)
        if source.session is not None:
            env_kwargs["session"] = source.session
        with rasterio.Env(**env_kwargs), rasterio.open(source.uri) as ds:
            yield ds
    elif isinstance(source, bytes | bytearray | memoryview):
        with rasterio.io.MemoryFile(bytes(source)) as mem, mem.open() as ds:
            yield ds
    else:
        raise TypeError(f"open_raster: unsupported source {type(source).__name__}")
```

- [ ] **Step 4: Edit `extract.py`.**

`geometry_from_raster`: change the signature to `def geometry_from_raster(data: RasterSource) -> ...` (import `RasterLocation, RasterSource, open_raster` from `pipeline.ingest.raster_io` at module top — it imports nothing heavy). Replace
```python
    try:
        with rasterio.io.MemoryFile(data) as mem, mem.open() as ds:
            crs, bounds = _raster_crs_and_bounds(ds)
    except Exception:
        crs = bounds = None

    if crs is None:
```
with
```python
    try:
        with open_raster(data) as ds:
            crs, bounds = _raster_crs_and_bounds(ds)
    except Exception:
        crs = bounds = None

    if crs is None and isinstance(data, bytes):
```
(the temp-file retry needs bytes; a location that GDAL could not open in place has nothing to spill — the caller falls back to bytes, Task 4).

`build_raster_auto`: rename the parameter `raster_bytes: bytes` → `raster: RasterSource`; replace `with rasterio.io.MemoryFile(raster_bytes) as mem, mem.open() as src:` with `with open_raster(raster) as src:`; the `import rasterio` inside the function is no longer needed if nothing else uses it — check and remove. Update the docstring's "(read from an in-memory file)" to "(opened in place from a `RasterLocation`, or from bytes)". Module docstring line 13: replace "raster reads go through an in-memory `rasterio.MemoryFile`" with "raster reads open a `RasterLocation` in place (`/vsis3`) or, for adapters that cannot locate, an in-memory `rasterio.MemoryFile` (M3-C, I-83)".

- [ ] **Step 5:** `uv run pytest tests/test_raster_io.py tests/test_ingest_extract.py -q && uv run ruff check .` → green. Commit: `feat(ingest): RasterLocation + open_raster — rasterio call sites open a raster where it lives (M3-C)`.

---

### Task 3: Locations from the S3 adapter and the platform client

**Files:**
- Modify: `services/pipeline/src/pipeline/connections/adapters/base.py` — three new methods on `StorageAdapter` after `copy_object_from` (anchor: `async def copy_object_from(`)
- Modify: `services/pipeline/src/pipeline/connections/adapters/s3.py` — imports; new `endpoint` property, `copy_source`, `gdal_location`, `open` on `S3Adapter` after `copy_object_from`
- Modify: `services/pipeline/src/pipeline/storage/platform.py` — `S3Like` gains `upload_fileobj` and `copy`; new `PlatformS3Access`, `platform_s3_access(settings)`, `raster_location(access, bucket, key, *, gdal_cachemax_mb)` after `build_platform_client`
- Test: `services/pipeline/tests/test_adapter_locations.py` (new), `services/pipeline/tests/test_platform_get.py` (append)

**Interfaces:**
- Produces:
  ```python
  # base.py
  class StorageAdapter:
      @property
      def endpoint(self) -> str | None: return None          # custom endpoint URL, when the protocol has one
      def copy_source(self, path: str) -> tuple[str, str] | None: return None   # (bucket, key) a same-endpoint CopyObject can read
      def gdal_location(self, path: str, *, options: Mapping[str, str] = {}) -> RasterLocation: raise NotImplementedError
      async def open(self, path: str) -> BinaryIO: return io.BytesIO(await self.get(path))  # I-83 default
  # platform.py
  @dataclass(frozen=True)
  class PlatformS3Access: endpoint_url: str | None; region: str; access_key: str; secret_key: str; force_path_style: bool
  def platform_s3_access(settings: Settings) -> PlatformS3Access   # endpoint via pinned_endpoint_url
  def raster_location(access: PlatformS3Access, bucket: str, key: str, *, gdal_cachemax_mb: int) -> RasterLocation
  def gdal_session_options(endpoint_url: str | None, force_path_style: bool, gdal_cachemax_mb: int | None) -> dict[str, str]  # shared by both builders
  def gdal_endpoint(endpoint_url: str | None) -> str | None   # "http://host:9000" -> "host:9000"
  ```

- [ ] **Step 1: Failing tests.** Create `tests/test_adapter_locations.py`:

```python
"""S3Adapter hands EXTRACT a /vsis3 location and FETCH a copy source (M3-C)."""

from __future__ import annotations

import io

import pytest

from pipeline.connections.adapters.base import StorageAdapter
from pipeline.connections.adapters.s3 import S3Adapter
from pipeline.ingest.raster_io import RasterLocation

ALLOW = frozenset({"minio"})
CREDS = {"access_key_id": "AK", "secret_access_key": "SK"}


def _adapter(**cfg) -> S3Adapter:
    return S3Adapter({"bucket": "src", **cfg}, CREDS, allow_hosts=ALLOW)


def test_gdal_location_for_a_custom_http_endpoint_is_path_style_plaintext(monkeypatch):
    adapter = _adapter(endpoint="http://minio:9000", force_path_style=True)
    monkeypatch.setattr(adapter, "_pinned_endpoint", lambda: "http://10.0.0.5:9000")
    loc = adapter.gdal_location("scenes/a.tif", options={"GDAL_CACHEMAX": "64"})
    assert loc.uri == "/vsis3/src/scenes/a.tif"
    assert loc.options["AWS_HTTPS"] == "NO"
    assert loc.options["AWS_VIRTUAL_HOSTING"] == "FALSE"
    assert loc.options["GDAL_CACHEMAX"] == "64"
    # the session dials the PINNED host:port, scheme stripped — rasterio's AWSSession form
    assert loc.session.credentials["aws_access_key_id"] == "AK"
    assert loc.session.credentials["aws_secret_access_key"] == "SK"
    assert loc.session._creds.get("endpoint_url", None) in (None, "10.0.0.5:9000") or True
    assert "10.0.0.5:9000" in repr(loc.session.credentials) or loc.session.endpoint_url == "10.0.0.5:9000"
    assert "SK" not in repr(loc)


def test_gdal_location_on_real_aws_keeps_https_and_virtual_hosting():
    adapter = _adapter(region="us-east-1")
    loc = adapter.gdal_location("scenes/a.tif")
    assert loc.uri == "/vsis3/src/scenes/a.tif"
    assert "AWS_HTTPS" not in loc.options
    assert "AWS_VIRTUAL_HOSTING" not in loc.options


def test_gdal_location_for_an_anonymous_bucket_is_unsigned():
    adapter = S3Adapter({"bucket": "noaa-goes19", "anonymous": True}, {}, allow_hosts=ALLOW)
    loc = adapter.gdal_location("ABI/a.nc")
    assert loc.session.unsigned is True


def test_copy_source_and_endpoint():
    adapter = _adapter(endpoint="http://minio:9000")
    assert adapter.copy_source("scenes/a.tif") == ("src", "scenes/a.tif")
    assert adapter.endpoint == "http://minio:9000"
    assert _adapter().endpoint is None


def test_base_adapter_defaults_keep_sftp_ftp_on_the_buffered_path():
    class Buffered(StorageAdapter):
        protocol = "sftp"

        async def test(self):  # pragma: no cover
            return {"ok": True}

        async def list(self, prefix=""):  # pragma: no cover
            return []

        async def get(self, path):
            return b"bytes-" + path.encode()

        async def put(self, path, data):  # pragma: no cover
            pass

        async def delete(self, path):  # pragma: no cover
            pass

    adapter = Buffered()
    assert adapter.endpoint is None
    assert adapter.copy_source("x") is None
    with pytest.raises(NotImplementedError):
        adapter.gdal_location("x")


async def test_base_open_is_the_buffered_get():
    class Buffered(StorageAdapter):
        protocol = "ftp"

        async def test(self):  # pragma: no cover
            return {"ok": True}

        async def list(self, prefix=""):  # pragma: no cover
            return []

        async def get(self, path):
            return b"payload"

        async def put(self, path, data):  # pragma: no cover
            pass

        async def delete(self, path):  # pragma: no cover
            pass

    stream = await Buffered().open("x")
    assert isinstance(stream, io.BytesIO) and stream.read() == b"payload"
```

Simplify the first test's session assertions to exactly these (delete the two hedged `or True` lines above — they are shown only to make the intent explicit; write this):

```python
    session = loc.session
    from rasterio.session import AWSSession

    assert isinstance(session, AWSSession)
    assert session.credentials["aws_access_key_id"] == "AK"
    assert session.credentials["aws_secret_access_key"] == "SK"
    assert session.endpoint_url == "10.0.0.5:9000"
    assert "SK" not in repr(loc)
```
(`AWSSession` exposes `.credentials` (a dict of `aws_*` keys) and the constructor's `endpoint_url` as `.endpoint_url` in rasterio ≥ 1.3 — if the installed version stores it under a different attribute, assert through `session.get_credential_options()["AWS_S3_ENDPOINT"] == "10.0.0.5:9000"` instead, which is the stable API. Prefer `get_credential_options()` for all three keys if `.credentials` is absent.)

Append to `tests/test_platform_get.py`:

```python
def test_platform_s3_access_pins_the_endpoint_and_raster_location_is_vsis3():
    from pipeline.config import Settings
    from pipeline.storage.platform import platform_s3_access, raster_location

    settings = Settings.from_env(
        env={
            "STAGING_S3_ENDPOINT": "http://minio:9000",
            "STAGING_S3_ACCESS_KEY": "AK",
            "STAGING_S3_SECRET_KEY": "SK",
            "EGRESS_ALLOW_HOSTS": "minio",
        }
    )
    access = platform_s3_access(settings)
    assert access.endpoint_url is not None and access.endpoint_url.endswith(":9000")
    assert access.force_path_style is True
    loc = raster_location(access, "stac-higher", "assets/c/i/scene.tif", gdal_cachemax_mb=64)
    assert loc.uri == "/vsis3/stac-higher/assets/c/i/scene.tif"
    assert loc.options == {"AWS_HTTPS": "NO", "AWS_VIRTUAL_HOSTING": "FALSE", "GDAL_CACHEMAX": "64"}
    assert loc.session.get_credential_options()["AWS_SECRET_ACCESS_KEY"] == "SK"


def test_gdal_endpoint_strips_the_scheme():
    from pipeline.storage.platform import gdal_endpoint

    assert gdal_endpoint("http://10.0.0.5:9000") == "10.0.0.5:9000"
    assert gdal_endpoint("https://s3.example.com") == "s3.example.com"
    assert gdal_endpoint(None) is None
```
(`EGRESS_ALLOW_HOSTS` semantics: check how `Settings.from_env` parses it and how `resolve_pinned` treats an allowlisted hostname that does not resolve in the test environment — if `minio` cannot resolve, `pinned_endpoint_url` may raise; in that case monkeypatch `pipeline.storage.platform.pinned_endpoint_url` to return `"http://10.0.0.5:9000"` for this test and assert `access.endpoint_url == "http://10.0.0.5:9000"`.)

- [ ] **Step 2:** run both files → FAIL (AttributeError / ImportError).

- [ ] **Step 3: `platform.py`.** Add after `build_platform_client`:

```python
@dataclass(frozen=True)
class PlatformS3Access:
    """What a GDAL session needs to read the platform bucket in place (M3-C):
    the PINNED endpoint (egress parity with `build_platform_client`) and the
    platform's own keys. Frozen and never logged."""

    endpoint_url: str | None
    region: str
    access_key: str
    secret_key: str
    force_path_style: bool


def platform_s3_access(settings: Settings) -> PlatformS3Access:
    return PlatformS3Access(
        endpoint_url=pinned_endpoint_url(
            settings.staging_s3_endpoint, settings.staging_s3_region, settings.egress_allow_hosts
        ),
        region=settings.staging_s3_region,
        access_key=settings.staging_s3_access_key,
        secret_key=settings.staging_s3_secret_key,
        force_path_style=settings.staging_s3_force_path_style,
    )


def gdal_endpoint(endpoint_url: str | None) -> str | None:
    """`AWSSession(endpoint_url=...)` wants ``host[:port]`` with no scheme."""
    if not endpoint_url:
        return None
    parsed = urlparse(endpoint_url)
    return parsed.netloc or None


def gdal_session_options(
    endpoint_url: str | None, force_path_style: bool, gdal_cachemax_mb: int | None
) -> dict[str, str]:
    """The `rasterio.Env` kwargs a custom endpoint needs. rasterio refuses raw
    `AWS_*` options except these two (S-E caveat): plaintext endpoints need
    `AWS_HTTPS=NO`, path-style ones `AWS_VIRTUAL_HOSTING=FALSE`."""
    options: dict[str, str] = {}
    if endpoint_url and urlparse(endpoint_url).scheme == "http":
        options["AWS_HTTPS"] = "NO"
    if endpoint_url and force_path_style:
        options["AWS_VIRTUAL_HOSTING"] = "FALSE"
    if gdal_cachemax_mb is not None:
        options["GDAL_CACHEMAX"] = str(gdal_cachemax_mb)
    return options


def raster_location(
    access: PlatformS3Access, bucket: str, key: str, *, gdal_cachemax_mb: int
) -> RasterLocation:
    """`/vsis3/<bucket>/<key>` under the platform's session (copy-mode EXTRACT)."""
    from rasterio.session import AWSSession

    session = AWSSession(
        aws_access_key_id=access.access_key,
        aws_secret_access_key=access.secret_key,
        region_name=access.region,
        endpoint_url=gdal_endpoint(access.endpoint_url),
    )
    return RasterLocation(
        uri=f"/vsis3/{bucket}/{key}",
        session=session,
        options=gdal_session_options(access.endpoint_url, access.force_path_style, gdal_cachemax_mb),
    )
```
Imports: `from dataclasses import dataclass`, `from pipeline.ingest.raster_io import RasterLocation` (check for an import cycle: `raster_io.py` imports nothing from `pipeline`, so this is safe). Add to `S3Like`:
```python
    def upload_fileobj(self, Fileobj: Any, Bucket: str, Key: str, **kwargs: Any) -> None: ...
    def copy(self, CopySource: dict[str, str], Bucket: str, Key: str, **kwargs: Any) -> None: ...
```

- [ ] **Step 4: `base.py`.** Add imports `import io`, `from collections.abc import Mapping`, `from typing import BinaryIO, TYPE_CHECKING`; under `TYPE_CHECKING` import `RasterLocation` from `pipeline.ingest.raster_io`. After `copy_object_from` add:

```python
    @property
    def endpoint(self) -> str | None:
        """The custom endpoint URL this adapter dials, when its protocol has
        one (s3 with a MinIO/custom endpoint). Feeds the server-side-copy gate."""
        return None

    def copy_source(self, path: str) -> tuple[str, str] | None:
        """``(bucket, key)`` a same-endpoint ``CopyObject`` could read ``path``
        from, or None when this protocol cannot be copied server-side (M3-C)."""
        return None

    def gdal_location(self, path: str, *, options: Mapping[str, str] | None = None) -> RasterLocation:
        """A `RasterLocation` GDAL can range-read in place. Object stores only —
        SFTP/FTP raise (I-83) and EXTRACT falls back to the buffered `get`."""
        raise NotImplementedError(f"{self.protocol}: no VSI handler authenticates through this adapter")

    async def open(self, path: str) -> BinaryIO:
        """A readable binary stream of ``path``. Default: the buffered `get`
        (I-83 — SFTP/FTP); `S3Adapter` overrides with a true stream."""
        return io.BytesIO(await self.get(path))
```

- [ ] **Step 5: `s3.py`.** Imports: `from collections.abc import Mapping`, `from typing import BinaryIO`, `from pipeline.ingest.raster_io import RasterLocation`, `from pipeline.storage.platform import gdal_endpoint, gdal_session_options` (check `platform.py` does not import the adapter package — it does not). After `copy_object_from` add:

```python
    @property
    def endpoint(self) -> str | None:
        return self._endpoint

    def copy_source(self, path: str) -> tuple[str, str] | None:
        return (self._bucket, path)

    def gdal_location(self, path: str, *, options: Mapping[str, str] | None = None) -> RasterLocation:
        """`/vsis3/<bucket>/<path>` under THIS connection's credentials (M3-C).
        The session is built here and handed out opaque — the decrypted keys
        now also live inside a GDAL session (spec §5 review point). The
        endpoint is the pinned one, like every boto3 client this adapter makes."""
        from rasterio.session import AWSSession

        endpoint_url = self._pinned_endpoint()
        if self._anonymous:
            session = AWSSession(
                aws_unsigned=True, region_name=self._region, endpoint_url=gdal_endpoint(endpoint_url)
            )
        else:
            session = AWSSession(
                aws_access_key_id=self._creds.get("access_key_id"),
                aws_secret_access_key=self._creds.get("secret_access_key"),
                aws_session_token=self._creds.get("session_token"),
                region_name=self._region,
                endpoint_url=gdal_endpoint(endpoint_url),
            )
        merged = gdal_session_options(endpoint_url, self._force_path_style, None)
        merged.update(options or {})
        return RasterLocation(uri=f"/vsis3/{self._bucket}/{path}", session=session, options=merged)

    async def open(self, path: str) -> BinaryIO:
        """A streaming read of ``path`` (botocore `StreamingBody`): nothing is
        buffered beyond what the caller reads. The GetObject call itself runs
        in a thread; the returned body is read by FETCH's upload thread."""
        endpoint_url = self._pinned_endpoint()

        def _open() -> BinaryIO:
            client = self._make_client(endpoint_url)
            return client.get_object(Bucket=self._bucket, Key=path)["Body"]

        return await asyncio.to_thread(_open)
```
(Note `_pinned_endpoint()` returns the full URL, e.g. `http://10.0.0.5:9000`; `gdal_session_options` reads its scheme, `gdal_endpoint` strips it.) `AWSSession`'s `aws_unsigned` kwarg exists in rasterio ≥ 1.1; the test asserts `session.unsigned is True` — if the attribute is named differently in the installed version, assert via `get_credential_options()["AWS_NO_SIGN_REQUEST"] == "YES"`.

- [ ] **Step 6:** `uv run pytest tests/test_adapter_locations.py tests/test_platform_get.py tests/test_adapters.py -q && uv run ruff check .` → green. Commit: `feat(storage): /vsis3 raster locations, copy sources and streaming open on S3Adapter + the platform client (M3-C)`.

---

### Task 4: EXTRACT prefers a location; ITEMIZE wires it

**Files:**
- Modify: `services/pipeline/src/pipeline/ingest/extract.py` — `MemberByteSource` protocol, `CanonicalByteSource`, `SourceAdapterByteSource`, `_best_effort_raster_geometry`, the `raster_auto` branch of `build_item`
- Modify: `services/pipeline/src/pipeline/ingest/itemize.py` — `run_itemize` signature (anchor: `asset_href_base: str,` parameter) and the byte-source construction (anchor: `SourceAdapterByteSource(adapter, config.source_path)`)
- Modify: `services/pipeline/src/pipeline/jobs/ingest.py` — the `itemize` job (anchor: `run_itemize(`), pass `raster_access`
- Test: `services/pipeline/tests/test_ingest_extract.py` (append), `services/pipeline/tests/test_ingest_itemize.py` (append one)

**Interfaces:**
- Produces:
  ```python
  class MemberByteSource(Protocol):
      async def read(self, member: ExtractMember) -> bytes: ...
      def locate(self, member: ExtractMember) -> RasterLocation | None: ...   # None ⇒ caller must read()
  @dataclass(frozen=True)
  class RasterAccess:            # in extract.py
      platform: PlatformS3Access | None = None   # copy mode: how to reach the canonical bucket in place
      gdal_cachemax_mb: int = DEFAULT_GDAL_CACHEMAX_MB
  CanonicalByteSource(s3_client, bucket, access: RasterAccess | None = None)
  SourceAdapterByteSource(adapter, source_path, access: RasterAccess | None = None)
  run_itemize(..., asset_href_base: str, raster_access: RasterAccess | None = None, ...)
  ```

- [ ] **Step 1: Failing tests.** Append to `tests/test_ingest_extract.py`:

```python
class _RaisingRead:
    """A byte source that can LOCATE but refuses to READ — the M3-C memory
    assertion: a raster_auto item is built without buffering the raster."""

    def __init__(self, path):
        self.path = path
        self.reads = 0

    async def read(self, member):
        self.reads += 1
        raise AssertionError("EXTRACT buffered the raster")

    def locate(self, member):
        from pipeline.ingest.raster_io import RasterLocation

        return RasterLocation(uri=str(self.path))


async def test_build_item_raster_auto_opens_the_location_and_never_reads_bytes(tmp_path):
    path = tmp_path / "scene.tif"
    path.write_bytes(_geotiff_bytes())
    source = _RaisingRead(path)
    item = await build_item(
        collection_id="col", item_id="scene", members=[_member("scene.tif")],
        metadata={"strategy": "raster_auto"}, byte_source=source, asset_href_base="/api/assets",
    )
    assert item["geometry"] is not None
    assert source.reads == 0


async def test_build_item_falls_back_to_bytes_when_the_source_cannot_locate():
    # SFTP/FTP (I-83): locate() -> None, so the buffered path is used as before.
    s3 = _FakeS3({("bucket", "assets/col/scene/scene.tif"): _geotiff_bytes()})
    source = CanonicalByteSource(s3, "bucket")  # no access -> cannot locate
    assert source.locate(_member("scene.tif")) is None
    item = await build_item(
        collection_id="col", item_id="scene", members=[_member("scene.tif")],
        metadata={"strategy": "raster_auto"}, byte_source=source, asset_href_base="/api/assets",
    )
    assert item["geometry"] is not None


async def test_best_effort_geometry_falls_back_to_bytes_when_the_location_is_unreadable(tmp_path):
    from pipeline.ingest.extract import _best_effort_raster_geometry
    from pipeline.ingest.raster_io import RasterLocation

    class _Both:
        def __init__(self):
            self.reads = 0

        def locate(self, member):
            return RasterLocation(uri=str(tmp_path / "missing.tif"))

        async def read(self, member):
            self.reads += 1
            return _geotiff_bytes()

    source = _Both()
    recovered = await _best_effort_raster_geometry(_member("scene.tif"), source)
    assert recovered is not None
    assert source.reads == 1


def test_canonical_byte_source_locates_through_the_platform_access():
    from pipeline.ingest.extract import RasterAccess
    from pipeline.storage.platform import PlatformS3Access

    access = RasterAccess(
        platform=PlatformS3Access("http://10.0.0.5:9000", "us-east-1", "AK", "SK", True),
        gdal_cachemax_mb=32,
    )
    source = CanonicalByteSource(_FakeS3({}), "stac-higher", access)
    loc = source.locate(_member("scene.tif"))
    assert loc is not None
    assert loc.uri == "/vsis3/stac-higher/assets/col/scene/scene.tif"
    assert loc.options["GDAL_CACHEMAX"] == "32"


def test_source_adapter_byte_source_locates_through_the_adapter_or_not_at_all():
    from pipeline.ingest.extract import RasterAccess, SourceAdapterByteSource
    from pipeline.ingest.raster_io import RasterLocation

    class _Locating:
        protocol = "s3"

        def gdal_location(self, path, *, options=None):
            return RasterLocation(uri=f"/vsis3/src/{path}", options=dict(options or {}))

    class _Buffered:
        protocol = "sftp"

        def gdal_location(self, path, *, options=None):
            raise NotImplementedError

    member = _member("scene.tif")
    located = SourceAdapterByteSource(_Locating(), "in/", RasterAccess(gdal_cachemax_mb=16)).locate(member)
    assert located is not None and located.uri.startswith("/vsis3/src/") and located.options["GDAL_CACHEMAX"] == "16"
    assert SourceAdapterByteSource(_Buffered(), "in/").locate(member) is None
```
(`_member("scene.tif")` in this file builds an `ExtractMember` with `canonical_key="assets/col/scene/scene.tif"` and a `source_path` — check its exact fields and adjust the expected keys above to what `_member` produces; the assertions must reflect the real helper.)

Append to `tests/test_ingest_itemize.py` one test that `run_itemize` accepts and threads `raster_access` — locate how the file calls `run_itemize` and add:

```python
async def test_run_itemize_accepts_raster_access_and_still_itemizes():
    from pipeline.ingest.extract import RasterAccess
    # copy the arrangement of the file's simplest happy-path run_itemize test here,
    # pass `raster_access=RasterAccess()` and assert the same outcome as that test.
```
(Fill the body by copying the file's simplest passing `run_itemize` call verbatim and adding the kwarg; the point is only that the new parameter threads through with no behaviour change when `platform` is None.)

- [ ] **Step 2:** run the two files → FAIL.

- [ ] **Step 3: `extract.py`.**

Imports: `from pipeline.config import DEFAULT_GDAL_CACHEMAX_MB`; `from pipeline.storage.platform import PlatformS3Access, raster_location` (platform.py imports `pipeline.config` and `raster_io` — no cycle with extract.py; verify with `uv run python -c "import pipeline.ingest.extract"`).

Replace the protocol and the two byte sources with:

```python
class MemberByteSource(Protocol):
    """Where EXTRACT reads a member from — canonical storage (copy mode) or the
    source adapter (reference mode). `read` buffers (sidecars, and the I-83
    fallback); `locate` names a place GDAL can open without buffering, or None
    when this source cannot (M3-C)."""

    async def read(self, member: ExtractMember) -> bytes: ...

    def locate(self, member: ExtractMember) -> RasterLocation | None: ...


@dataclass(frozen=True)
class RasterAccess:
    """How EXTRACT reaches rasters in place (M3-C): the platform's own access
    for copy mode (None ⇒ canonical reads stay buffered — tests, CLIs), and
    the GDAL cache ceiling every location is opened under."""

    platform: PlatformS3Access | None = None
    gdal_cachemax_mb: int = DEFAULT_GDAL_CACHEMAX_MB


@dataclass(frozen=True)
class CanonicalByteSource:
    """Copy mode: the object FETCH wrote to canonical platform storage."""

    s3_client: platform.S3Like
    bucket: str
    access: RasterAccess | None = None

    async def read(self, member: ExtractMember) -> bytes:
        return await asyncio.to_thread(
            platform.get_object, self.s3_client, self.bucket, member.canonical_key
        )

    def locate(self, member: ExtractMember) -> RasterLocation | None:
        if self.access is None or self.access.platform is None:
            return None
        return raster_location(
            self.access.platform, self.bucket, member.canonical_key,
            gdal_cachemax_mb=self.access.gdal_cachemax_mb,
        )


@dataclass(frozen=True)
class SourceAdapterByteSource:
    """Reference mode: the object in place on the source adapter."""

    adapter: StorageAdapter
    source_path: str
    access: RasterAccess | None = None

    async def read(self, member: ExtractMember) -> bytes:
        return await self.adapter.get(source_fetch_path(self.source_path, member.source_path))

    def locate(self, member: ExtractMember) -> RasterLocation | None:
        cachemax = (self.access or RasterAccess()).gdal_cachemax_mb
        try:
            return self.adapter.gdal_location(
                source_fetch_path(self.source_path, member.source_path),
                options={"GDAL_CACHEMAX": str(cachemax)},
            )
        except NotImplementedError:  # SFTP/FTP — I-83
            return None
```

`_best_effort_raster_geometry`: replace the body after the candidate check with:
```python
    location = byte_source.locate(primary)
    if location is not None:
        recovered = geometry_from_raster(location)
        if recovered is not None:
            return recovered
        # GDAL could not open it in place (HDF-backed netCDF/GRIB need a real
        # file — the temp-file retry inside geometry_from_raster needs bytes).
    try:
        data = await byte_source.read(primary)
    except Exception:
        return None
    return geometry_from_raster(data)
```

`build_item` raster_auto branch: replace
```python
            data = await byte_source.read(primary)
            item = build_raster_auto(collection_id, item_id, members, cfg, data, asset_href_base)
```
with
```python
            raster: RasterSource | None = byte_source.locate(primary)
            if raster is None:
                raster = await byte_source.read(primary)
            item = build_raster_auto(collection_id, item_id, members, cfg, raster, asset_href_base)
```
(`RasterSource` is imported from `raster_io` in Task 2.)

- [ ] **Step 4: `itemize.py` + `jobs/ingest.py`.** `run_itemize` gains `raster_access: RasterAccess | None = None` after `asset_href_base: str,` (import `RasterAccess` from `pipeline.ingest.extract`); the byte-source construction becomes
```python
    byte_source = (
        SourceAdapterByteSource(adapter, config.source_path, raster_access)
        if config.storage_mode == "reference"
        else CanonicalByteSource(s3_client, bucket, raster_access)
    )
```
In `jobs/ingest.py`'s `itemize` job, pass
```python
            raster_access=RasterAccess(
                platform=platform_s3_access(settings), gdal_cachemax_mb=settings.gdal_cachemax_mb
            ),
```
to `run_itemize` (imports: `RasterAccess` from `pipeline.ingest.extract`, `platform_s3_access` from `pipeline.storage.platform`). `platform_s3_access` calls `pinned_endpoint_url`, which can raise `EgressBlocked` — it is the same vetting `build_platform_client` does two lines earlier, so no new failure mode.

- [ ] **Step 5:** `uv run pytest -q && uv run ruff check .` → green (the `tests/test_ingest_jobs.py` fakes must still satisfy `run_itemize`'s call — if a fake `run_itemize` is patched in with a fixed signature, add the kwarg there). Commit: `feat(ingest): EXTRACT opens rasters in place through MemberByteSource.locate; ITEMIZE threads RasterAccess (M3-C, closes I-26)`.

---

### Task 5: FETCH — server-side copy, else a bounded streamed upload

**Files:**
- Create: `services/pipeline/src/pipeline/ingest/transfer.py`
- Modify: `services/pipeline/src/pipeline/storage/platform.py` — add `upload_stream` and `copy_from_bucket` after `copy_object`
- Modify: `services/pipeline/src/pipeline/ingest/fetch.py` — module docstring paragraph on buffering; `fetch_stage` signature + the copy-mode loop body
- Modify: `services/pipeline/src/pipeline/jobs/ingest.py` — the `fetch` job builds the policy and passes it
- Modify: `services/pipeline/src/pipeline/metrics.py` — one counter (anchor: the existing ingest counters)
- Modify: `services/pipeline/tests/_ingest_fake.py` — `FakeS3` gains `upload_fileobj` and `copy` (recording), keeps `puts`
- Test: `services/pipeline/tests/test_ingest_transfer.py` (new), `services/pipeline/tests/test_ingest_fetch.py` (append), `services/pipeline/tests/test_platform_get.py` (append)

**Interfaces:**
- Produces:
  ```python
  # pipeline/ingest/transfer.py
  @dataclass(frozen=True)
  class TransferPolicy:
      server_side_copy: bool = False
      chunk_bytes: int = DEFAULT_FETCH_CHUNK_BYTES
      concurrency: int = DEFAULT_FETCH_TRANSFER_CONCURRENCY
  def transfer_policy(adapter: StorageAdapter, settings: Settings) -> TransferPolicy
  class HashingStream:  # wraps a readable; .read(n) updates sha256 + size
      hexdigest() -> str; size -> int
  # platform.py
  def upload_stream(client: S3Like, bucket: str, key: str, body: BinaryIO, *, chunk_bytes: int, concurrency: int) -> tuple[str, int]   # (sha256 hex, bytes)
  def copy_from_bucket(client: S3Like, src_bucket: str, src_key: str, bucket: str, key: str, *, chunk_bytes: int, concurrency: int) -> None
  # fetch.py
  async def fetch_stage(repo, association, config, adapter, s3_client, bucket, item_id, source_paths, *, transfer: TransferPolicy | None = None) -> int
  # metrics.py
  INGEST_FETCH_TRANSFERS = Counter("pipeline_ingest_fetch_transfers_total", "...", ["mode"])  # mode = copy | stream | copy_fallback
  ```

- [ ] **Step 1: Failing tests.** Create `tests/test_ingest_transfer.py`:

```python
"""FETCH transfer policy + the hashing stream (M3-C)."""

from __future__ import annotations

import hashlib
import io

from pipeline.config import Settings
from pipeline.ingest.transfer import HashingStream, TransferPolicy, transfer_policy


class _S3Like:
    protocol = "s3"

    def __init__(self, endpoint):
        self._endpoint = endpoint

    @property
    def endpoint(self):
        return self._endpoint


class _Sftp:
    protocol = "sftp"
    endpoint = None


def _settings(endpoint: str | None) -> Settings:
    env = {"FETCH_CHUNK_BYTES": "1024", "FETCH_TRANSFER_CONCURRENCY": "2"}
    if endpoint is not None:
        env["STAGING_S3_ENDPOINT"] = endpoint
    else:
        env["STAGING_S3_ENDPOINT"] = ""
    return Settings.from_env(env=env)


def test_same_endpoint_s3_source_may_be_copied_server_side():
    policy = transfer_policy(_S3Like("http://minio:9000"), _settings("http://minio:9000"))
    assert policy == TransferPolicy(server_side_copy=True, chunk_bytes=1024, concurrency=2)


def test_a_different_endpoint_streams():
    assert transfer_policy(_S3Like("http://other:9000"), _settings("http://minio:9000")).server_side_copy is False


def test_both_on_real_aws_may_copy():
    assert transfer_policy(_S3Like(None), _settings(None)).server_side_copy is True


def test_sftp_never_copies():
    assert transfer_policy(_Sftp(), _settings("http://minio:9000")).server_side_copy is False


def test_hashing_stream_hashes_exactly_what_was_read():
    payload = b"x" * 5000
    stream = HashingStream(io.BytesIO(payload))
    out = b""
    while chunk := stream.read(1024):
        out += chunk
    assert out == payload
    assert stream.size == 5000
    assert stream.hexdigest() == hashlib.sha256(payload).hexdigest()
```
(`Settings.from_env` with `STAGING_S3_ENDPOINT=""` yields `staging_s3_endpoint=None` per `config.py`'s `or None` — the "real AWS" case.)

Update `tests/_ingest_fake.py` `FakeS3`:
```python
@dataclass
class FakeS3:
    """Captures the platform-storage calls the FETCH stage makes: buffered
    put_object (legacy), streamed upload_fileobj (M3-C) — both recorded as
    `puts` with the body bytes so assertions read the same — and server-side
    `copy` calls. `fail_copy=True` makes `copy` raise (fallback path)."""

    puts: list[dict[str, Any]] = field(default_factory=list)
    copies: list[dict[str, Any]] = field(default_factory=list)
    fail_copy: bool = False

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.puts.append(kwargs)
        return {}

    def upload_fileobj(self, Fileobj: Any, Bucket: str, Key: str, **kwargs: Any) -> None:
        self.puts.append({"Bucket": Bucket, "Key": Key, "Body": Fileobj.read(), **kwargs})

    def copy(self, CopySource: dict[str, str], Bucket: str, Key: str, **kwargs: Any) -> None:
        if self.fail_copy:
            raise RuntimeError("copy denied")
        self.copies.append({"CopySource": CopySource, "Bucket": Bucket, "Key": Key, **kwargs})
```
(Existing tests assert on `s3.puts[...]["Body"]` and `["Key"]` — unchanged. Check for any assertion on `put_object`-specific kwargs such as `ContentType` and adapt only if one exists.)

Append to `tests/test_ingest_fetch.py`:

```python
from pipeline.ingest.transfer import TransferPolicy


async def test_fetch_streams_with_the_policy_chunking_and_records_sha256():
    repo = FakeIngestRepo()
    assoc = _assoc({"source_path": "in/", "storage_mode": "copy"})
    entry = await _settled(repo, "in/a.bin", size=5000)
    adapter = FakeAdapter(blobs={"in/a.bin": b"y" * 5000})
    s3 = FakeS3()
    policy = TransferPolicy(server_side_copy=False, chunk_bytes=1024, concurrency=2)
    stored = await fetch_stage(
        repo, assoc, parse_ingest_config(assoc.config), adapter, s3, "bucket", "item-1", ["in/a.bin"],
        transfer=policy,
    )
    assert stored == 1
    put = s3.puts[0]
    assert put["Body"] == b"y" * 5000
    assert put["Config"].multipart_chunksize == 1024 and put["Config"].max_concurrency == 2
    latest = await repo.get_latest_ledger(assoc.id, "in/a.bin")
    assert latest.status == "stored" and latest.checksum == hashlib.sha256(b"y" * 5000).hexdigest()
    assert s3.copies == []


async def test_fetch_copies_server_side_when_the_policy_allows_and_reads_no_bytes():
    repo = FakeIngestRepo()
    assoc = _assoc({"source_path": "in/", "storage_mode": "copy"})
    await _settled(repo, "in/a.bin")
    adapter = FakeAdapter(blobs={"in/a.bin": b"zzz"}, copy_bucket="src")
    s3 = FakeS3()
    stored = await fetch_stage(
        repo, assoc, parse_ingest_config(assoc.config), adapter, s3, "bucket", "item-1", ["in/a.bin"],
        transfer=TransferPolicy(server_side_copy=True),
    )
    assert stored == 1
    assert s3.puts == []
    assert s3.copies[0]["CopySource"] == {"Bucket": "src", "Key": "in/a.bin"}
    assert s3.copies[0]["Bucket"] == "bucket"
    assert adapter.get_calls == [] and adapter.open_calls == []
    latest = await repo.get_latest_ledger(assoc.id, "in/a.bin")
    assert latest.status == "stored" and latest.checksum is None


async def test_fetch_falls_back_to_streaming_when_the_copy_fails():
    repo = FakeIngestRepo()
    assoc = _assoc({"source_path": "in/", "storage_mode": "copy"})
    await _settled(repo, "in/a.bin")
    adapter = FakeAdapter(blobs={"in/a.bin": b"zzz"}, copy_bucket="src")
    s3 = FakeS3(fail_copy=True)
    stored = await fetch_stage(
        repo, assoc, parse_ingest_config(assoc.config), adapter, s3, "bucket", "item-1", ["in/a.bin"],
        transfer=TransferPolicy(server_side_copy=True),
    )
    assert stored == 1
    assert s3.puts[0]["Body"] == b"zzz"
    latest = await repo.get_latest_ledger(assoc.id, "in/a.bin")
    assert latest.checksum == hashlib.sha256(b"zzz").hexdigest()


async def test_fetch_never_copies_from_an_adapter_without_a_copy_source():
    repo = FakeIngestRepo()
    assoc = _assoc({"source_path": "in/", "storage_mode": "copy"})
    await _settled(repo, "in/a.bin")
    adapter = FakeAdapter(blobs={"in/a.bin": b"zzz"})  # copy_bucket None -> copy_source() None
    s3 = FakeS3()
    await fetch_stage(
        repo, assoc, parse_ingest_config(assoc.config), adapter, s3, "bucket", "item-1", ["in/a.bin"],
        transfer=TransferPolicy(server_side_copy=True),
    )
    assert s3.copies == [] and s3.puts[0]["Body"] == b"zzz"
```
and extend `FakeAdapter` in `_ingest_fake.py` with:
```python
    copy_bucket: str | None = None
    open_calls: list[str] = field(default_factory=list)

    @property
    def endpoint(self) -> str | None:
        return None

    def copy_source(self, path: str) -> tuple[str, str] | None:
        return (self.copy_bucket, path) if self.copy_bucket else None

    async def open(self, path: str):
        self.open_calls.append(path)
        return io.BytesIO(self.blobs[path])
```
(add `import io`). Append to `tests/test_platform_get.py`:

```python
def test_upload_stream_uses_a_transfer_config_and_returns_the_digest():
    import hashlib
    import io

    from pipeline.storage.platform import upload_stream

    class _Client:
        def __init__(self):
            self.calls = []

        def upload_fileobj(self, Fileobj, Bucket, Key, Config=None):
            self.calls.append((Bucket, Key, Fileobj.read(), Config))

    client = _Client()
    digest, size = upload_stream(client, "b", "k", io.BytesIO(b"abc" * 100), chunk_bytes=64, concurrency=3)
    bucket, key, body, config = client.calls[0]
    assert (bucket, key, body) == ("b", "k", b"abc" * 100)
    assert config.multipart_chunksize == 64 and config.multipart_threshold == 64 and config.max_concurrency == 3
    assert digest == hashlib.sha256(b"abc" * 100).hexdigest() and size == 300


def test_copy_from_bucket_uses_the_managed_copy():
    from pipeline.storage.platform import copy_from_bucket

    class _Client:
        def __init__(self):
            self.calls = []

        def copy(self, CopySource, Bucket, Key, Config=None):
            self.calls.append((CopySource, Bucket, Key, Config))

    client = _Client()
    copy_from_bucket(client, "src", "in/a", "plat", "assets/a", chunk_bytes=64, concurrency=2)
    source, bucket, key, config = client.calls[0]
    assert source == {"Bucket": "src", "Key": "in/a"} and (bucket, key) == ("plat", "assets/a")
    assert config.multipart_chunksize == 64 and config.max_concurrency == 2
```

- [ ] **Step 2:** run the three test files → FAIL.

- [ ] **Step 3: `transfer.py`:**

```python
"""How FETCH moves bytes (M3-C, spec §3 / S-E).

Three strategies, tried in this order per member:

1. **Server-side copy** — when the source is an s3 bucket the platform's own
   credentials can read on the same endpoint (`can_server_side_copy`, the gate
   the delivery path already uses in the other direction): `CopyObject`,
   zero bytes through the worker, 7.5x faster than today (S-E).
2. **Streamed multipart** — otherwise, or when the copy fails (the platform
   keys may not be able to read the source bucket): the adapter's `open()`
   stream is hashed on the way through and uploaded with boto3's transfer
   manager, whose buffers are bounded by `chunk_bytes x concurrency`.
3. The buffered `get()` is what `StorageAdapter.open()` falls back to for
   SFTP/FTP (I-83) — same code path here, the adapter decides.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import BinaryIO

from pipeline.config import (
    DEFAULT_FETCH_CHUNK_BYTES,
    DEFAULT_FETCH_TRANSFER_CONCURRENCY,
    Settings,
)
from pipeline.connections.adapters.base import StorageAdapter
from pipeline.delivery.transfer import can_server_side_copy


@dataclass(frozen=True)
class TransferPolicy:
    server_side_copy: bool = False
    chunk_bytes: int = DEFAULT_FETCH_CHUNK_BYTES
    concurrency: int = DEFAULT_FETCH_TRANSFER_CONCURRENCY


def transfer_policy(adapter: StorageAdapter, settings: Settings) -> TransferPolicy:
    """Decided once per FETCH job from the adapter and the platform endpoint."""
    return TransferPolicy(
        server_side_copy=can_server_side_copy(
            adapter.protocol, adapter.endpoint, settings.staging_s3_endpoint
        ),
        chunk_bytes=settings.fetch_chunk_bytes,
        concurrency=settings.fetch_transfer_concurrency,
    )


class HashingStream:
    """A read-only wrapper that sha256s everything read through it. Exposes
    only `read`, so boto3 treats it as non-seekable and buffers per part."""

    def __init__(self, raw: BinaryIO) -> None:
        self._raw = raw
        self._sha = hashlib.sha256()
        self.size = 0

    def read(self, n: int = -1) -> bytes:
        chunk = self._raw.read(n) if n is not None and n >= 0 else self._raw.read()
        self._sha.update(chunk)
        self.size += len(chunk)
        return chunk

    def hexdigest(self) -> str:
        return self._sha.hexdigest()
```

`platform.py` (after `copy_object`):

```python
def upload_stream(
    client: S3Like, bucket: str, key: str, body: BinaryIO, *, chunk_bytes: int, concurrency: int
) -> tuple[str, int]:
    """Streamed multipart upload of ``body`` (M3-C): boto3's transfer manager
    reads `chunk_bytes` parts with at most `concurrency` in flight, so worker
    memory is bounded by their product, not by the object. Returns the sha256
    hex digest and byte count of what went through. Synchronous — wrap in
    ``asyncio.to_thread``."""
    from boto3.s3.transfer import TransferConfig

    from pipeline.ingest.transfer import HashingStream

    hashing = HashingStream(body)
    client.upload_fileobj(
        hashing,
        bucket,
        key,
        Config=TransferConfig(
            multipart_threshold=chunk_bytes,
            multipart_chunksize=chunk_bytes,
            max_concurrency=concurrency,
            use_threads=True,
        ),
    )
    return hashing.hexdigest(), hashing.size


def copy_from_bucket(
    client: S3Like, src_bucket: str, src_key: str, bucket: str, key: str, *, chunk_bytes: int, concurrency: int
) -> None:
    """Server-side copy from ANOTHER bucket on the platform endpoint into the
    platform bucket (M3-C FETCH fast path). boto3's managed `copy` switches to
    multipart copy above the threshold, so objects over 5 GB work. Raises when
    the platform keys cannot read ``src_bucket`` — the caller streams instead.
    Synchronous — wrap in ``asyncio.to_thread``."""
    from boto3.s3.transfer import TransferConfig

    client.copy(
        {"Bucket": src_bucket, "Key": src_key},
        bucket,
        key,
        Config=TransferConfig(
            multipart_threshold=chunk_bytes, multipart_chunksize=chunk_bytes, max_concurrency=concurrency
        ),
    )
```
(`ingest.transfer` imports `pipeline.delivery.transfer` and `config`; `platform.upload_stream` imports `ingest.transfer` lazily inside the function to avoid a module cycle through `connections.adapters.base` — keep the lazy import.)

`metrics.py` — beside the existing ingest counters:
```python
INGEST_FETCH_TRANSFERS = Counter(
    "pipeline_ingest_fetch_transfers_total",
    "Copy-mode FETCH transfers by strategy (M3-C): copy = server-side, stream = bounded multipart, copy_fallback = copy failed then streamed.",
    ["mode"],
)
```
(match the file's registry/labelling conventions — copy the shape of the nearest existing `Counter`.)

`fetch.py` — signature: append `*, transfer: TransferPolicy | None = None` to `fetch_stage`; `policy = transfer or TransferPolicy()`. Replace the copy-mode `try:` body

```python
            fetch_path = source_fetch_path(config.source_path, source_path)
            data = await adapter.get(fetch_path)
            checksum = hashlib.sha256(data).hexdigest()
            filename = source_path.rsplit("/", 1)[-1]
            key = canonical_asset_key(association.collection_id, item_id, filename)
            await asyncio.to_thread(platform.put_object, s3_client, bucket, key, data)
            await repo.set_ledger_fields(
                latest.id, status=STATUS_STORED, checksum=checksum
            )
            stored += 1
```
with
```python
            fetch_path = source_fetch_path(config.source_path, source_path)
            filename = source_path.rsplit("/", 1)[-1]
            key = canonical_asset_key(association.collection_id, item_id, filename)
            checksum, mode = await _transfer(adapter, s3_client, bucket, key, fetch_path, policy)
            metrics.INGEST_FETCH_TRANSFERS.labels(mode=mode).inc()
            await repo.set_ledger_fields(latest.id, status=STATUS_STORED, checksum=checksum)
            stored += 1
```
and add the helper (below `fetch_stage`):

```python
async def _transfer(
    adapter: StorageAdapter,
    s3_client: platform.S3Like,
    bucket: str,
    key: str,
    fetch_path: str,
    policy: TransferPolicy,
) -> tuple[str | None, str]:
    """Move one member into canonical storage. Returns ``(sha256 or None, mode)``:
    the copy path reads no bytes, so it records no checksum (an honest absence,
    nothing reads the column today)."""
    source = adapter.copy_source(fetch_path) if policy.server_side_copy else None
    mode = "stream"
    if source is not None:
        try:
            await asyncio.to_thread(
                platform.copy_from_bucket, s3_client, source[0], source[1], bucket, key,
                chunk_bytes=policy.chunk_bytes, concurrency=policy.concurrency,
            )
            return None, "copy"
        except Exception:  # the platform keys cannot read the source bucket — stream instead
            logger.warning(
                "server-side copy failed; streaming instead",
                extra={"bucket": bucket, "key": key, "source_bucket": source[0]},
                exc_info=True,
            )
            mode = "copy_fallback"
    body = await adapter.open(fetch_path)
    checksum, _size = await asyncio.to_thread(
        platform.upload_stream, s3_client, bucket, key, body,
        chunk_bytes=policy.chunk_bytes, concurrency=policy.concurrency,
    )
    return checksum, mode
```
Add `transfer: mode` to the existing "ingest fetch group done" log's `extra` only if cheap (a per-member log is not wanted); the counter is the evidence. Module docstring: replace "The whole object is buffered in memory in copy mode (ISSUES I-19: streaming + multipart deferred)." with "Copy mode moves bytes by server-side copy when the platform can read the source bucket, else by a bounded streamed multipart upload (M3-C, `ingest/transfer.py`); nothing is buffered whole except for SFTP/FTP sources (I-83)." Remove the now-unused `hashlib` import.

`jobs/ingest.py` `fetch` job: after building `adapter`, `policy = transfer_policy(adapter, settings)` and pass `transfer=policy` to `fetch_stage` (import from `pipeline.ingest.transfer`).

- [ ] **Step 4:** `uv run pytest -q && uv run ruff check .` → green. Commit: `feat(ingest): FETCH server-side-copies when the platform can read the source, else streams a bounded multipart upload (M3-C, closes I-19)`.

---

### Task 6: Docs — the envelope written down, issues closed

**Files:**
- Modify: `services/pipeline/README.md` — new section "Memory envelope (M3-C)" immediately before "## Docker" (after M3-B's "Connection pooling" section)
- Modify: `docs/ISSUES.md` — I-19 and I-26 → 🟢 resolved; I-83 gains one sentence
- Modify: `docs/FEATURES.md` — the M3 table: a `| M3-C · bounded-memory byte path | ✅ | ... |` row after M3-B's

- [ ] **Step 1: README section**

```markdown
## Memory envelope (M3-C)

Worker memory no longer scales with asset size. Two paths changed:

- **EXTRACT** opens rasters in place through GDAL (`/vsis3/<bucket>/<key>`
  under the platform's or the source connection's session — `ingest/raster_io.py`,
  `MemberByteSource.locate`) instead of buffering the object into a
  `MemoryFile`. GDAL's block cache does the reading and `GDAL_CACHEMAX` caps
  it. SFTP/FTP sources cannot be located and keep the buffered read (I-83).
- **FETCH** (copy mode) server-side-copies when the platform's keys can read
  the source bucket on the same endpoint (`ingest/transfer.py`, the delivery
  path's `can_server_side_copy` gate), and otherwise streams a multipart upload
  whose buffers are `FETCH_CHUNK_BYTES × FETCH_TRANSFER_CONCURRENCY`. A failed
  copy falls back to streaming. `pipeline_ingest_fetch_transfers_total{mode}`
  counts which path ran.

Per-worker peak RSS, S-E's formula with these settings:

```
255 MiB + (GDAL_CACHEMAX + FETCH_CHUNK_BYTES × FETCH_TRANSFER_CONCURRENCY) × WORKER_CONCURRENCY
= 255 MiB + (64 + 32) MiB × concurrency      → ~1.4 GB at M3-D's default 12
```

Size a deployment by that line, not by the largest asset. A reference-mode
`/vsis3` read puts the connection's decrypted credentials into a GDAL session
(built inside `S3Adapter.gdal_location`, never logged) — the same keys the
worker already decrypts for boto3, in one more place.
```

- [ ] **Step 2: ISSUES.** Replace the I-19 entry's heading and body with:
```markdown
### I-19 · Adapter `get` fully buffers large assets — 🟢 resolved for object stores (M3-C, 2026-09-09)
Copy-mode FETCH now server-side-copies when the platform can read the source
bucket and otherwise streams a bounded multipart upload
(`ingest/transfer.py`, `platform.upload_stream`); `StorageAdapter.open()` is
the streaming seam and `S3Adapter` implements it. SFTP/FTP keep the buffered
`get()` — that residual is I-83.
```
and I-26's with:
```markdown
### I-26 · Memory-buffered raster reads in EXTRACT — 🟢 resolved for object stores (M3-C, 2026-09-09)
EXTRACT opens the primary raster in place through a `RasterLocation`
(`/vsis3`, `ingest/raster_io.py`); `MemberByteSource.locate` supplies it and
`build_raster_auto` / `geometry_from_raster` accept it. Buffering remains only
for sources that cannot be located (I-83) and as the best-effort geometry
fallback for HDF-backed files GDAL refuses to open through VSI.
```
Append to I-83's body: "M3-C landed both halves for s3 (2026-09-09); this entry is now the only buffered path."

- [ ] **Step 3: FEATURES row** (after the M3-B row the lead added):
```markdown
| M3-C · bounded-memory byte path | ✅ | EXTRACT opens rasters in place (`/vsis3` `RasterLocation`, `ingest/raster_io.py`); copy-mode FETCH server-side-copies when the platform can read the source, else streams a bounded multipart upload (`ingest/transfer.py`); the envelope is `GDAL_CACHEMAX` + `FETCH_CHUNK_BYTES × FETCH_TRANSFER_CONCURRENCY` per job (README "Memory envelope"). Closes I-19/I-26 for object stores; I-83 remains. Measured: `TODO.md` follow-ups. |
```

- [ ] **Step 4:** `uv run ruff check .` (docs only, but the gate is cheap) → commit: `docs: M3-C memory envelope, I-19/I-26 closed for object stores`.

---

### Task 7: Measure, record, merge (lead only, Docker)

**Files:** `TODO.md` (M3-C checkbox, queue row, follow-ups), `docs/FEATURES.md` (measured line), `services/pipeline/README.md` (measured line under the new section).

- [ ] **Step 1:** Full gates on the branch (`npm run verify`; pytest + ruff).
- [ ] **Step 2: Baseline RSS on `ai/main` (M3-B merged, M3-C not).** Stack up, `docker compose build pipeline && up -d pipeline`. `loadgen --label m3c setup --mode copy --metadata raster_auto`, then `feed --profile raster --rate 0 --count 60 --asset-bytes 67108864` (64 MB rasters — the S-E size) while sampling `docker stats --no-stream --format '{{.MemUsage}}' stac-higher-pipeline-1` every 5 s into a file; `watch --seconds 180 --interval 20` overlapping; record peak RSS; `teardown`.
- [ ] **Step 3: Same on the M3-C build.** Expected: peak RSS lower and flat across the run; `pipeline_ingest_fetch_transfers_total{mode="copy"}` ≈ the item count (loadgen's source bucket is on the same MinIO endpoint → the copy path); `ingest_itemize` mean not worse than baseline; the items byte-identical to a baseline item (compare one item's `raster:bands` statistics).
- [ ] **Step 4:** Record (`TODO.md` follow-up "M3-C landed": both peak RSS numbers, the transfer counter, itemize means, any deviation), tick the slice, FEATURES/README measured lines. Merge `--no-ff`, gates on `ai/main`, `docker compose build pipeline && up -d pipeline`, canary. Remove worktree + branch.

## Self-review

**Spec coverage.** M3-C's three parts: (1) URI not buffer → Tasks 2–4 (`RasterLocation`, `locate`, both rasterio call sites, the `rasterio.Env(session=AWSSession(...), AWS_HTTPS, AWS_VIRTUAL_HOSTING)` form from the S-E caveat, byte-identical item pinned by a test); (2) FETCH copy-when-gated + streamed multipart with fallback → Task 5 (reusing `can_server_side_copy`; `copy_object_from` is the adapter's INTO-its-bucket method, so the platform-side `copy_from_bucket` is new, as S-E's "FETCH is the same question in the other direction" implies); (3) the bound explicit and configured → Task 1 + README formula (Task 6). Review point (credentials in GDAL) → Global Constraints + `S3Adapter.gdal_location` docstring + README; SFTP/FTP keep buffered `get()` → base-class defaults + I-83. Spec §5 "memory assertion" → the `_RaisingRead` unit test (no bytes read) + Task 7's RSS measurement. ✓

**Placeholder scan.** Two test bodies defer to "copy the file's existing helper/call" by explicit instruction (`_geotiff_bytes`, the `run_itemize` happy path) because their exact text lives in files the plan does not reproduce; each says precisely what to copy and what to assert. No "TBD".

**Type consistency.** `RasterLocation(uri, session, options)` is constructed identically in `platform.raster_location`, `S3Adapter.gdal_location`, and the tests. `MemberByteSource.locate -> RasterLocation | None` is what `build_item` and `_best_effort_raster_geometry` consume. `RasterAccess(platform, gdal_cachemax_mb)` is built in `jobs/ingest.py`, threaded by `run_itemize`, read by both byte sources. `TransferPolicy(server_side_copy, chunk_bytes, concurrency)` is produced by `transfer_policy` and consumed by `fetch_stage`/`_transfer`; `upload_stream` returns `(digest, size)` and `copy_from_bucket` returns None, matching `_transfer`'s use. `S3Like` gains exactly the two boto3 methods the new platform functions call (`upload_fileobj`, `copy`), and `FakeS3` implements both.

**Ambiguities resolved (rulings for the reviewer):** copy path records `checksum=None`; the platform-side copy uses boto3's managed `copy` (multipart-capable) rather than `copy_object`; `GDAL_CACHEMAX` is both GDAL's own env var and a Setting so the bound holds either way; the metric is a plain counter with a `mode` label (no histogram).
