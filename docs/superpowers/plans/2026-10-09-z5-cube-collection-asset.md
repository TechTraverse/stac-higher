# Z-5 · The Cube's Collection Asset — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After every batch that reached a cube's Icechunk repository, publish the cube on its **cube collection**: `assets.{asset_key}`, `extent.temporal`, `cube:dimensions` and the Datacube extension. This is the pipeline's first production collection write.

**Architecture:**
- **New module `pipeline/cubes/collection.py`, in two layers.**
  - *Pure builders.* They take the collection document, the sink config and Z-4's `BatchResult`, and return the merged document plus an outcome (`plan_publish`).
  - *A thin pgstac writer.* `PgCollectionPublisher` runs one transaction on a plain pool connection: `SELECT content … FOR UPDATE`, read the sink's recorded tip, merge, `pgstac.update_collection`.
- **`production_after_batch(settings)`** wraps the writer as Z-4's `AfterBatch` hook. `cube_append` wires it in, and Z-6's maintenance trim reuses it (agreed with the Z-6 planner, 2026-10-09).
- **Z-4's `BatchResult` gains the grid** (`statics`, `spatial_dims`), so the writer never re-reads the repository.
- **`cube_append` now awaits the hook BEFORE writing the ledger.** That closes the "hook not re-run" gap PR #101 left open.

**Tech Stack:** Python 3.12, psycopg 3 (async pool, `Jsonb`), pgstac 0.9.11 (`pgstac.update_collection` replaces `content` wholesale), rasterio 1.5 (`CRS.from_proj4(...).to_dict(projjson=True)`, so no new dependency), numpy, pytest (asyncio auto mode), ruff. No app change, no migration, no new dependency.

**Spec:** `docs/superpowers/specs/2026-10-03-virtual-cube-sink-design.md` §6.3 (the writer), §3.3 (the asset shape), §8.2 (the x/y rescale the cube-server applies), decisions §14.3 (no audit row) and §14.5 (`time_values` on the asset). Issue #91. ADR 0022. PR #101's open follow-up "`after_batch` is not re-run…" and its peer-review finding #5.

## Global Constraints

- **Worktree:** `.claude/worktrees/z5-cube-collection-asset`, branch `feat/z5-cube-collection-asset`, off `origin/main` at 12c2cc4. Never work in the main checkout.
- **Gates:** each task ends green on what it touches. The final task runs `npm run verify` (repo root), plus `uv run pytest` and `uv run ruff check .` from `services/pipeline/`. No e2e, no dev server, no Docker, no load harness.
- **DB-gated tests** (`test_integration_cube_collection.py`) skip without `DATABASE_URL`. CI runs them against its pgstac service after `npm run db:migrate`, and that is the gate. A teammate does not run them locally. The lead may run them against the compose DB: each test makes and removes its own `z5-cube-<uuid>` collection and sink.
- **No migration and no DDL** (ADR 0001). The writer READS `cube_sinks.last_snapshot_id` and `source_prefixes`. It writes no `stac_higher.*` row at all.
- **Collection writes only through `pgstac.update_collection`**, in a transaction that first takes `SELECT content FROM pgstac.collections WHERE id = %s FOR UPDATE`. Use a plain pool connection (`pipeline.db.pool.get_async_pool`), never `stac/pgstac_writer.py`'s item writer pool (spec §6.3).
- **The writer touches exactly four keys:** `assets.{asset_key}`, `extent.temporal`, `cube:dimensions` and the Datacube entry of `stac_extensions`. Everything else in the document is the user's.
- **The asset shape is spec §3.3, verbatim:** media type `application/vnd.zarr+icechunk`, roles `["data", "references", "virtual", "latest-version"]`, title `Virtual cube`, `version` = the snapshot id, `stac_higher:virtual_chunk_prefixes`, `stac_higher:cube_sink_id`, `stac_higher:time_values` (exact nanosecond RFC 3339 strings with a `Z`).
- **Datacube extension URL:** `https://stac-extensions.github.io/datacube/v2.2.0/schema.json`.
- **No audit row per publish** (§14.3). One structured log line, data in `extra={…}` (pipeline logging rule).
- **The `AfterBatch` signature does not change:** `(CubeSink, CubeSinkConfig, BatchResult) -> Awaitable[None]`. The Z-6 plan (`feat/z6-cube-maintain`) builds a `BatchResult` itself and calls the same hook.

## Decisions this plan takes (flag in the PR)

1. **Only the RECORDED tip is published.** Under the collection row lock, the writer reads `cube_sinks.last_snapshot_id`. If that isn't `result.snapshot_id`, it writes nothing (`superseded`). A double run (I-144) therefore never publishes an older snapshot over a newer one. A run that records a newer tip later waits on the row lock and publishes after. Every caller (Z-6 included) must `record_commit` before calling the hook.
2. **`virtual_chunk_prefixes` comes from `cube_sinks.source_prefixes`**, read in the same statement as the tip, not from the `CubeSink` the job loaded at start. On a first commit, that object still has the empty prefixes.
3. **The hook moves before `finish_rows`.** If it raises (a DB blip), the job's exception path gives the attempts back and sets `last_error`, and Procrastinate retries. The redo reads every step as a duplicate, re-records nothing and publishes again. A worker that dies inside the hook leaves the rows claimed, and the next take redoes them the same way. This is the first option PR #101 offered ("run before `finish_rows`"); it costs a relabel (`appended` → `appended/duplicate`) after a failed publish.
4. **The builders never raise on odd metadata.** Since a raise now holds the batch's rows pending, an unusable projection drops only `reference_system`. A geostationary grid without its height drops the spatial dimensions. A non-dict `assets` or `extent`, or a non-list `stac_extensions`, is replaced. Only DB errors propagate.
5. **The x/y extents are projected metres**, matching the cube-server (spec §8.2, §14.6). For a `geostationary` grid that is the scan angle × `perspective_point_height`; any other grid counts as already projected. They are min/max over finite values. The last data dimension is `axis: x` and the one before it `axis: y`.
6. **`reference_system` is PROJJSON for `geostationary` only.** It is built as a PROJ string and converted by rasterio, so pyproj is not added. v1's only parser is HDF5, and NODD's gridded HDF5 products are GOES ABI. Any other grid mapping omits it (Datacube would otherwise read EPSG:4326). `semi_minor_axis` is preferred and `inverse_flattening` stands in. `sweep_angle_axis` defaults to `y` (the CF default).
7. **An empty cube** (trimmed to zero steps, which Z-6's age trim can do): `time_values: []`, `t` extent `[null, null]`, `extent.temporal.interval: [[null, null]]`. pgstac accepts nulls (checked on pgstac 0.9.11).
8. **A non-datetime `append_dim`** gets no `time_values` and no temporal dimension, and leaves `extent.temporal` alone.
9. **The Datacube extension is listed once, at v2.2.0.** Any other `datacube/` version the user listed is replaced.
10. **An unchanged document is not rewritten** (`unchanged`). Equality is checked on the decoded JSON, so a pgstac round trip still compares equal. This keeps a duplicate-only redo or a Z-6 no-op from bumping the collection.
11. **A missing cube collection is a WARNING and returns `missing_collection`.** It doesn't raise, because the app deletes the sink with its collection, so this is only a race.
12. **`BatchResult.statics` / `spatial_dims` default to empty.** When they are empty (a caller that didn't read the grid, as Z-6's trim may not), the writer keeps the document's existing spatial dimensions. The layout check forbids a grid change within a cube, so they cannot be stale.

## Review Focus

1. **A user's edit made from a stale copy:** a user opens the collection, a publish lands, and the user saves. `pgstac.update_collection` replaces the whole document, so the asset and `cube:dimensions` disappear until the next commit (≤ 5 min at the GOES cadence). The row lock only orders concurrent transactions. Expected: documented in `docs/serving.md`. Spec §7 already accepts hand edits being overwritten.
2. **A real GOES file's projection attributes come back from h5py as one-element arrays and bytes.** Expected: the same PROJJSON and extents as plain values. Pinned by `test_hdf5_attribute_types_read_like_plain_values` (Task 2).
3. **A publish that keeps failing** (a bug in the writer, not an outage) keeps the batch's rows pending. Each `cube_kick` re-takes, re-HEADs and re-parses them; the cube itself stays correct and `last_error` names the error. Expected: Decision 4 keeps the builders total, so only DB errors raise. Pinned by the malformed-document and unusable-projection tests (Task 2) and `test_a_failing_asset_hook_releases_the_rows_and_the_redo_publishes` (Task 4).
4. **Document size:** each step adds ~33 bytes of `time_values`, so 288 steps ≈ 10 KB, and the 10,000-step maximum ≈ 330 KB rides in every `/collections` response. Expected: documented in `docs/serving.md`. No code change in v1 (§14.5 chose the asset over asking the server).
5. **Nanosecond strings in the browser:** `Date.parse("2026-10-03T17:02:36.714359936Z")` truncates to milliseconds in Node 22 / V8 (checked). Z-8 must select frames by the exact strings, never by a re-serialized `Date`. Expected: documented in `docs/serving.md` for Z-8. Pinned by `test_time_values_are_exact_nanosecond_strings` (Task 2).

## Pre-validation (2026-10-09)

The plan's code was written and run in this worktree before the plan was saved, then reverted; the patch is in the planning session's scratchpad.
- `uv run pytest`: 2025 passed, 47 skipped (no `DATABASE_URL`).
- `DATABASE_URL=…5433… uv run pytest tests/test_integration_cube*.py tests/test_cube_*.py`: 195 passed, 1 skipped (the `CUBE_IT` test).
- `ruff check .` is clean.
- **Mutation check:** dropping `FOR UPDATE` makes `test_a_concurrent_bff_edit_is_not_lost` fail.
- The compose DB was left with no `z5-cube-%` collection or sink.

## File Structure

```
services/pipeline/src/pipeline/cubes/icerepo.py        # Task 1: CubeState.spatial_dims
services/pipeline/src/pipeline/cubes/write.py          # Task 1: BatchResult.statics / spatial_dims
services/pipeline/tests/_cube_sources.py               # Task 1: real GOES projection attrs in the fixture
services/pipeline/tests/test_cube_write.py             # Task 1
services/pipeline/src/pipeline/cubes/collection.py     # Task 2 (builders) + Task 3 (pgstac writer)
services/pipeline/tests/test_cube_collection.py        # Task 2
services/pipeline/tests/test_integration_cube_collection.py  # Task 3 (DB-gated)
services/pipeline/src/pipeline/cubes/append.py         # Task 4: hook before the ledger
services/pipeline/src/pipeline/jobs/cubes.py           # Task 4: wire production_after_batch
services/pipeline/tests/test_cube_append.py            # Task 4
services/pipeline/tests/test_cube_jobs.py              # Task 4
docs/serving.md, docs/FEATURES.md                      # Task 5
```

---

### Task 1: The batch result carries the cube's grid

**Files:**
- Modify: `services/pipeline/src/pipeline/cubes/icerepo.py` (`CubeState`, `read_state`)
- Modify: `services/pipeline/src/pipeline/cubes/write.py` (`BatchResult`, `_attempt`)
- Modify: `services/pipeline/tests/_cube_sources.py` (`GOES_PROJECTION`, `write_goes_file`)
- Test: `services/pipeline/tests/test_cube_write.py`

**Interfaces:**
- Produces: `CubeState.spatial_dims: tuple[str, ...] = ()`; `BatchResult.statics: Mapping[str, StaticSpec] = {}` and `BatchResult.spatial_dims: tuple[str, ...] = ()` (both defaulted, so existing constructions still work); `_cube_sources.GOES_PROJECTION: dict` (a real GOES-East `goes_imager_projection`'s attributes).

- [ ] **Step 1: Give the fixture real projection attributes.** In `tests/_cube_sources.py`, add after `GOES_CONFIG`:

```python
#: ``goes_imager_projection``'s attributes in a real GOES-East ABI L2 file
GOES_PROJECTION = {
    "grid_mapping_name": "geostationary",
    "perspective_point_height": 35786023.0,
    "semi_major_axis": 6378137.0,
    "semi_minor_axis": 6356752.31414,
    "inverse_flattening": 298.2572221,
    "latitude_of_projection_origin": 0.0,
    "longitude_of_projection_origin": -75.0,
    "sweep_angle_axis": "x",
}
```

and in `write_goes_file` replace `proj.attrs["grid_mapping_name"] = "geostationary"` with:

```python
        for key, attr in GOES_PROJECTION.items():
            proj.attrs[key] = attr
```

Keep the existing `proj.attrs["perspective_point_height"] = perspective_point_height` line after the loop: tests that vary the height still work.

- [ ] **Step 2: Write the failing test.** Append to `tests/test_cube_write.py`:

```python
def test_the_result_carries_the_grid_for_the_asset_writer(cube):
    result = cube.write(cube.parsed(1, 0))
    assert result.spatial_dims == ("y", "x")
    assert set(result.statics) == {"x", "y", "goes_imager_projection"}
    assert result.statics["x"].values.tolist() == pytest.approx([i * 1e-4 for i in range(6)])
    assert '"grid_mapping_name": "geostationary"' in result.statics["goes_imager_projection"].attrs
```

- [ ] **Step 3: Run it to see it fail.** `cd services/pipeline && uv run pytest tests/test_cube_write.py -k grid -v`. Expected: FAIL (`AttributeError: 'BatchResult' object has no attribute 'spatial_dims'`).

- [ ] **Step 4: `CubeState.spatial_dims`.** In `cubes/icerepo.py`, add the field after `statics`:

```python
    #: the data variables' dimensions after ``append_dim`` (e.g. ``("y", "x")``),
    #: from the first of them by name (they share one layout)
    spatial_dims: tuple[str, ...] = ()
```

In `read_state`, declare `spatial_dims: tuple[str, ...] = ()` next to `specs`. Set it from the first data array (names are sorted):

```python
            if name != append_dim:
                if not specs:
                    spatial_dims = tuple(dims[1:])
                specs[name] = ArraySpec(
```

Then pass `spatial_dims=spatial_dims,` as the last argument of the returned `CubeState(...)`.

- [ ] **Step 5: `BatchResult` fields.** In `cubes/write.py`:
  - Change the imports to `from collections.abc import Mapping, Sequence` and `from dataclasses import dataclass, field`.
  - Add `StaticSpec,` to the `pipeline.cubes.steps` import list.
  - Add after `initialised`:

```python
    #: the cube's variables without the append axis (``x``, ``y``, the grid
    #: mapping) and its data variables' other dimensions, for the collection
    #: asset writer (Z-5). Empty when the caller did not read them (Z-6's
    #: trim may not): the writer then keeps the document's spatial dimensions.
    statics: Mapping[str, StaticSpec] = field(default_factory=dict)
    spatial_dims: tuple[str, ...] = ()
```

  In `_attempt`, pass the two fields from the state it already read after the write:

```python
        initialised=after.initialised,
        statics=after.statics,
        spatial_dims=after.spatial_dims,
    )
```

- [ ] **Step 6: Run the cube tests.** `uv run pytest tests/test_cube_*.py -q`. Expected: all pass. The fixture change must not break any Z-4 test: every file now carries the same extra attributes, so the grid check still compares like with like.

- [ ] **Step 7: Lint and commit.**

```bash
uv run ruff check .
git add src/pipeline/cubes/icerepo.py src/pipeline/cubes/write.py tests/_cube_sources.py tests/test_cube_write.py
git commit -m "Z-5: the batch result carries the cube's grid for the asset writer"
```

---

### Task 2: `cubes/collection.py` — the builders and the publish decision

**Files:**
- Create: `services/pipeline/src/pipeline/cubes/collection.py`
- Test: `services/pipeline/tests/test_cube_collection.py`

**Interfaces:**
- Consumes: `BatchResult` (with Task 1's `statics`, `spatial_dims`), `CubeSinkConfig` (`asset_key`, `append_dim`), `StaticSpec` and `canonical_attrs` (`cubes/steps.py`), `cube_prefix` (`cubes/icerepo.py`).
- Produces (Task 3 and Z-6 rely on these names):
  - `cube_href(bucket: str, cube_collection_id: str) -> str`
  - `time_strings(values: np.ndarray) -> list[str] | None`
  - `crs_projjson(gm: Mapping | None) -> dict | None`
  - `merge_collection(content, *, config, result, sink_id, href, prefixes) -> dict`
  - `Recorded(last_snapshot_id: str | None, source_prefixes: tuple[str, ...])`
  - `plan_publish(content: Mapping | None, recorded: Recorded | None, *, sink_id, config, result, href) -> tuple[str, dict | None]`
  - constants `CUBE_MEDIA_TYPE`, `DATACUBE_EXTENSION`, and the outcomes `PUBLISHED`, `UNCHANGED`, `SUPERSEDED`, `MISSING_COLLECTION`.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_cube_collection.py`:

```python
"""The cube's collection asset: builders, merge and the publish decision (spec §6.3, §3.3)."""

from __future__ import annotations

import copy
import json

import numpy as np
import pytest

from _cube_sources import GOES_CONFIG, GOES_PROJECTION, as_ns, scan
from pipeline.cubes.collection import (
    CUBE_MEDIA_TYPE,
    DATACUBE_EXTENSION,
    MISSING_COLLECTION,
    PUBLISHED,
    SUPERSEDED,
    UNCHANGED,
    Recorded,
    crs_projjson,
    cube_href,
    merge_collection,
    plan_publish,
    time_strings,
)
from pipeline.cubes.config import parse_cube_sink_config
from pipeline.cubes.steps import StaticSpec, canonical_attrs
from pipeline.cubes.write import BatchResult

CONFIG = parse_cube_sink_config({**GOES_CONFIG, "window": {"max_steps": 288}})
H = GOES_PROJECTION["perspective_point_height"]
HREF = "s3://platform/assets/goes-cube/_cube/"
PREFIXES = ("s3://noaa-goes19/",)
#: a real GOES-19 scan start, with the sub-microsecond part the tiler needs
T_EXACT = np.datetime64("2026-10-03T17:02:36.714359936", "ns")


def statics(**projection) -> dict[str, StaticSpec]:
    gm = {**GOES_PROJECTION, **projection}
    return {
        "x": StaticSpec(np.array([-0.1, 0.0, 0.1], dtype="f4"), None),
        "y": StaticSpec(np.array([0.08, 0.04, 0.0], dtype="f4"), None),
        "goes_imager_projection": StaticSpec(np.array(-2147483647, "i4"), canonical_attrs(gm)),
    }


def result(values=None, *, snapshot="SNAP2", grid=True, **projection) -> BatchResult:
    values = np.array([T_EXACT, as_ns(scan(1))]) if values is None else values
    return BatchResult(
        outcomes={},
        snapshot_id=snapshot,
        committed=True,
        values=values,
        trimmed=0,
        initialised=True,
        statics=statics(**projection) if grid else {},
        spatial_dims=("y", "x") if grid else (),
    )


def collection(**over) -> dict:
    doc = {
        "type": "Collection",
        "id": "goes-cube",
        "stac_version": "1.0.0",
        "stac_extensions": ["https://stac-extensions.github.io/scientific/v1.0.0/schema.json"],
        "title": "GOES-19 C13 cube",
        "description": "a user's words",
        "license": "proprietary",
        "keywords": ["goes"],
        "links": [],
        "extent": {
            "spatial": {"bbox": [[-150.0, 10.0, -50.0, 60.0]]},
            "temporal": {"interval": [[None, None]]},
        },
        "assets": {"thumbnail": {"href": "https://example.com/t.png", "roles": ["thumbnail"]}},
    }
    return {**doc, **over}


def merged(content=None, res=None) -> dict:
    return merge_collection(
        collection() if content is None else content,
        config=CONFIG,
        result=result() if res is None else res,
        sink_id="sink-1",
        href=HREF,
        prefixes=PREFIXES,
    )


def test_the_href_is_the_repository_prefix():
    assert cube_href("platform", "goes-cube") == HREF


def test_time_values_are_exact_nanosecond_strings():
    assert time_strings(np.array([T_EXACT])) == ["2026-10-03T17:02:36.714359936Z"]
    assert time_strings(np.array([], dtype="datetime64[ns]")) == []
    assert time_strings(np.array([1.0, 2.0])) is None


def test_the_document_matches_the_spec_shape():
    doc = merged()
    projjson = crs_projjson(GOES_PROJECTION)
    first, last = "2026-10-03T17:02:36.714359936Z", "2026-10-03T17:05:00.000000000Z"
    assert doc["assets"]["cube"] == {
        "href": HREF,
        "type": CUBE_MEDIA_TYPE,
        "roles": ["data", "references", "virtual", "latest-version"],
        "title": "Virtual cube",
        "version": "SNAP2",
        "stac_higher:virtual_chunk_prefixes": ["s3://noaa-goes19/"],
        "stac_higher:cube_sink_id": "sink-1",
        "stac_higher:time_values": [first, last],
    }
    assert doc["extent"]["temporal"] == {"interval": [[first, last]]}
    dims = doc["cube:dimensions"]
    assert dims["t"] == {"type": "temporal", "extent": [first, last]}
    assert dims["x"]["type"] == "spatial" and dims["x"]["axis"] == "x"
    assert dims["y"]["axis"] == "y"
    # scan angles (radians) x perspective_point_height = the metres the cube-server serves
    assert dims["x"]["extent"] == pytest.approx([-0.1 * H, 0.1 * H], rel=1e-6)
    assert dims["y"]["extent"] == pytest.approx([0.0, 0.08 * H], rel=1e-6)
    assert dims["x"]["reference_system"] == projjson
    assert projjson["type"] == "ProjectedCRS"
    assert "Geostationary" in json.dumps(projjson["conversion"]["method"])
    assert DATACUBE_EXTENSION in doc["stac_extensions"]


def test_only_the_cube_keys_change():
    before = collection()
    doc = merged(copy.deepcopy(before))
    for key in ("title", "description", "keywords", "license", "links", "id"):
        assert doc[key] == before[key]
    assert doc["assets"]["thumbnail"] == before["assets"]["thumbnail"]
    assert doc["extent"]["spatial"] == before["extent"]["spatial"]
    assert doc["stac_extensions"][0] == before["stac_extensions"][0]
    assert before == collection()  # the input is not mutated


def test_a_removed_asset_and_dimensions_come_back():
    content = merged()
    del content["assets"]["cube"]
    del content["cube:dimensions"]
    content["stac_extensions"].remove(DATACUBE_EXTENSION)
    assert merged(content) == merged()


def test_the_datacube_extension_is_listed_once_at_the_current_version():
    old = "https://stac-extensions.github.io/datacube/v2.1.0/schema.json"
    doc = merged(collection(stac_extensions=[old, DATACUBE_EXTENSION, "x"]))
    assert doc["stac_extensions"] == ["x", DATACUBE_EXTENSION]


def test_malformed_user_keys_are_replaced_not_raised():
    doc = merged(collection(assets=["not", "a", "dict"], extent=None, stac_extensions="x"))
    assert set(doc["assets"]) == {"cube"}
    assert doc["extent"]["temporal"]["interval"][0][0].startswith("2026-10-03T17:02:36")
    assert doc["stac_extensions"] == [DATACUBE_EXTENSION]


def test_a_cube_trimmed_to_zero_steps():
    doc = merged(res=result(np.array([], dtype="datetime64[ns]")))
    assert doc["assets"]["cube"]["stac_higher:time_values"] == []
    assert doc["cube:dimensions"]["t"]["extent"] == [None, None]
    assert doc["extent"]["temporal"] == {"interval": [[None, None]]}
    assert doc["cube:dimensions"]["x"]["axis"] == "x"


def test_a_result_without_the_grid_keeps_the_documents_spatial_dimensions():
    published = merged()
    trimmed = merged(published, result(np.array([as_ns(scan(1))]), snapshot="SNAP3", grid=False))
    assert trimmed["cube:dimensions"]["x"] == published["cube:dimensions"]["x"]
    assert trimmed["cube:dimensions"]["y"] == published["cube:dimensions"]["y"]
    assert trimmed["cube:dimensions"]["t"]["extent"][0] == "2026-10-03T17:05:00.000000000Z"
    assert trimmed["assets"]["cube"]["version"] == "SNAP3"


@pytest.mark.parametrize(
    "projection",
    [
        {"sweep_angle_axis": "z"},
        {"longitude_of_projection_origin": "east"},
        {"semi_major_axis": float("nan")},
    ],
)
def test_an_unusable_projection_drops_only_the_reference_system(projection):
    dims = merged(res=result(**projection))["cube:dimensions"]
    assert "reference_system" not in dims["x"]
    assert dims["x"]["extent"] == pytest.approx([-0.1 * H, 0.1 * H], rel=1e-6)


def test_a_geostationary_grid_without_its_height_has_no_spatial_dimensions():
    res = result()
    gm = {k: v for k, v in GOES_PROJECTION.items() if k != "perspective_point_height"}
    res.statics["goes_imager_projection"] = StaticSpec(np.array(0, "i4"), canonical_attrs(gm))
    assert set(merged(res=res)["cube:dimensions"]) == {"t"}


def test_the_inverse_flattening_stands_in_for_the_semi_minor_axis():
    gm = {k: v for k, v in GOES_PROJECTION.items() if k != "semi_minor_axis"}
    assert crs_projjson(gm)["type"] == "ProjectedCRS"


def test_a_non_time_append_dim_leaves_the_temporal_extent_alone():
    doc = merged(res=result(np.array([1.0, 2.0])))
    assert "stac_higher:time_values" not in doc["assets"]["cube"]
    assert doc["extent"]["temporal"] == {"interval": [[None, None]]}
    assert "t" not in doc["cube:dimensions"]


def plan(content, recorded, res=None):
    return plan_publish(
        content,
        recorded,
        sink_id="sink-1",
        config=CONFIG,
        result=result() if res is None else res,
        href=HREF,
    )


def test_only_the_recorded_tip_is_published():
    outcome, doc = plan(collection(), Recorded("SNAP2", PREFIXES))
    assert outcome == PUBLISHED and doc == merged()
    assert plan(collection(), Recorded("SNAP3", PREFIXES)) == (SUPERSEDED, None)
    assert plan(collection(), Recorded(None, ())) == (SUPERSEDED, None)
    assert plan(collection(), None) == (SUPERSEDED, None)  # the sink is gone


def test_the_recorded_prefixes_are_published():
    _, doc = plan(collection(), Recorded("SNAP2", ("s3://b/", "s3://a/")))
    assert doc["assets"]["cube"]["stac_higher:virtual_chunk_prefixes"] == ["s3://a/", "s3://b/"]


def test_a_missing_collection_and_an_unchanged_document_write_nothing():
    assert plan(None, Recorded("SNAP2", PREFIXES)) == (MISSING_COLLECTION, None)
    assert plan(merged(), Recorded("SNAP2", PREFIXES)) == (UNCHANGED, None)
    # through a JSON round trip, as pgstac hands the document back
    assert plan(json.loads(json.dumps(merged())), Recorded("SNAP2", PREFIXES)) == (UNCHANGED, None)


def test_hdf5_attribute_types_read_like_plain_values():
    # h5py hands real GOES attributes back as one-element arrays and bytes
    raw = {
        k: (np.array([v]) if isinstance(v, float) else v.encode() if isinstance(v, str) else v)
        for k, v in GOES_PROJECTION.items()
    }
    res = result()
    res.statics["goes_imager_projection"] = StaticSpec(np.array(0, "i4"), canonical_attrs(raw))
    assert merged(res=res)["cube:dimensions"] == merged()["cube:dimensions"]
```

- [ ] **Step 2: Run them to see them fail.** `uv run pytest tests/test_cube_collection.py -q`. Expected: collection error (`ModuleNotFoundError: No module named 'pipeline.cubes.collection'`).

- [ ] **Step 3: Implement the builders.** Create `src/pipeline/cubes/collection.py`:

```python
"""The cube's collection asset (virtual cube spec §6.3, §3.3; ADR 0022).

After every batch that reached the repository, ``cube_append`` publishes the
cube on its collection: ``assets.{asset_key}``, ``extent.temporal``,
``cube:dimensions`` and the Datacube extension in ``stac_extensions``. Z-6's
maintenance trim publishes through the same hook. This is the pipeline's
first production collection write:
- One transaction on a plain pool connection, not the item writer pool:
  ``SELECT … FOR UPDATE`` on the pgstac row, merge, ``pgstac.update_collection``.
  The row lock serializes against a BFF edit, which also goes through pgstac.
- Only those four keys change. A user's other edits survive, and a removed
  asset comes back.
- Only the sink's RECORDED tip is published (``cube_sinks.last_snapshot_id``,
  read under the lock), so a double run never publishes an older snapshot
  over a newer one (``superseded``).
- An unchanged document is not rewritten (``unchanged``).
- No audit row (spec §14.3): one structured log line per publish.

The builders never raise on odd metadata: they drop a field
(``reference_system``, a spatial dimension) instead, because a raise here
keeps the batch's ledger rows pending while the job retries. The ``x``/``y``
extents are in the projected metres the cube-server presents: a geostationary
grid's scan angles (radians) times ``perspective_point_height`` (spec §8.2).
"""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from rasterio.crs import CRS
from rasterio.errors import CRSError

from pipeline.cubes.config import CubeSinkConfig
from pipeline.cubes.icerepo import cube_prefix
from pipeline.cubes.steps import StaticSpec
from pipeline.cubes.write import BatchResult

logger = logging.getLogger(__name__)

CUBE_MEDIA_TYPE = "application/vnd.zarr+icechunk"
CUBE_ROLES = ("data", "references", "virtual", "latest-version")
CUBE_TITLE = "Virtual cube"
DATACUBE_EXTENSION = "https://stac-extensions.github.io/datacube/v2.2.0/schema.json"
_DATACUBE_PREFIX = "https://stac-extensions.github.io/datacube/"

PUBLISHED = "published"
UNCHANGED = "unchanged"
SUPERSEDED = "superseded"
MISSING_COLLECTION = "missing_collection"


def cube_href(bucket: str, cube_collection_id: str) -> str:
    """The repository's URL: ``s3://{platform bucket}/assets/{cube}/_cube/``."""
    return f"s3://{bucket}/{cube_prefix(cube_collection_id)}/"


def time_strings(values: np.ndarray) -> list[str] | None:
    """Exact nanosecond RFC 3339 strings (the tiler needs exact ``t``
    selectors, spike Q1), or ``None`` for an ``append_dim`` that is not a time."""
    if not np.issubdtype(values.dtype, np.datetime64):
        return None
    ns = np.datetime_as_string(values.astype("datetime64[ns]"), unit="ns")
    return [f"{s}Z" for s in ns]


def grid_mapping(statics: Mapping[str, StaticSpec]) -> dict[str, Any] | None:
    """The attributes of the cube's CF grid mapping variable: the scalar
    static whose attributes name a ``grid_mapping_name``."""
    for name in sorted(statics):
        attrs = statics[name].attrs
        if attrs is None:
            continue
        parsed = json.loads(attrs)
        if isinstance(parsed, dict) and "grid_mapping_name" in parsed:
            return parsed
    return None


def _number(attrs: Mapping[str, Any], key: str) -> float | None:
    try:
        value = float(attrs[key])
    except (KeyError, TypeError, ValueError):
        return None
    return value if np.isfinite(value) else None


def crs_projjson(gm: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """PROJJSON for a CF ``geostationary`` grid mapping, else ``None``. v1's
    only parser is HDF5 and NODD's gridded HDF5 products are GOES ABI, so
    that is the one mapping handled; any other leaves ``reference_system``
    out (Datacube then reads EPSG:4326, so the writer omits it rather than
    guess)."""
    if not gm or gm.get("grid_mapping_name") != "geostationary":
        return None
    h = _number(gm, "perspective_point_height")
    lon = _number(gm, "longitude_of_projection_origin")
    a = _number(gm, "semi_major_axis")
    b = _number(gm, "semi_minor_axis")
    rf = _number(gm, "inverse_flattening")
    sweep = gm.get("sweep_angle_axis", "y")
    if None in (h, lon, a) or (b is None and rf is None) or sweep not in ("x", "y"):
        return None
    shape = f"+b={b!r}" if b is not None else f"+rf={rf!r}"
    proj4 = f"+proj=geos +h={h!r} +lon_0={lon!r} +sweep={sweep} +a={a!r} {shape} +units=m +no_defs"
    try:
        return CRS.from_proj4(proj4).to_dict(projjson=True)
    except CRSError:
        return None


def _metres_per_unit(gm: Mapping[str, Any] | None) -> float | None:
    """Factor from the stored ``x``/``y`` to projected metres: a geostationary
    grid stores scan angles (spec §8.2); any other grid is taken as already
    projected. ``None`` when a geostationary grid lacks its height."""
    if gm and gm.get("grid_mapping_name") == "geostationary":
        return _number(gm, "perspective_point_height")
    return 1.0


def spatial_dimensions(
    statics: Mapping[str, StaticSpec], spatial_dims: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """Datacube ``spatial`` dimensions for the last two data dimensions (the
    last is ``x``). A dimension without 1-D numeric finite values is left out."""
    gm = grid_mapping(statics)
    scale = _metres_per_unit(gm)
    if scale is None:
        return {}
    crs = crs_projjson(gm)
    out: dict[str, dict[str, Any]] = {}
    for axis, name in zip(("x", "y"), reversed(tuple(spatial_dims)), strict=False):
        spec = statics.get(name)
        if spec is None or spec.values.ndim != 1 or not np.issubdtype(spec.values.dtype, np.number):
            continue
        metres = spec.values.astype("float64") * scale
        finite = metres[np.isfinite(metres)]
        if finite.size == 0:
            continue
        dim: dict[str, Any] = {
            "type": "spatial",
            "axis": axis,
            "extent": [float(finite.min()), float(finite.max())],
        }
        if crs is not None:
            dim["reference_system"] = crs
        out[name] = dim
    return out


def build_asset(
    *, href: str, sink_id: str, snapshot_id: str, prefixes: Sequence[str], times: list[str] | None
) -> dict[str, Any]:
    """``assets.{asset_key}`` exactly as spec §3.3."""
    asset: dict[str, Any] = {
        "href": href,
        "type": CUBE_MEDIA_TYPE,
        "roles": list(CUBE_ROLES),
        "title": CUBE_TITLE,
        "version": snapshot_id,
        "stac_higher:virtual_chunk_prefixes": sorted(prefixes),
        "stac_higher:cube_sink_id": sink_id,
    }
    if times is not None:
        asset["stac_higher:time_values"] = times
    return asset


def merge_collection(
    content: Mapping[str, Any],
    *,
    config: CubeSinkConfig,
    result: BatchResult,
    sink_id: str,
    href: str,
    prefixes: Sequence[str],
) -> dict[str, Any]:
    """The collection document with the cube published on it. Touches only
    ``assets.{asset_key}``, ``extent.temporal``, ``cube:dimensions`` and the
    Datacube entry of ``stac_extensions``."""
    merged = copy.deepcopy(dict(content))
    times = time_strings(result.values)

    assets = merged.get("assets")
    assets = dict(assets) if isinstance(assets, dict) else {}
    assets[config.asset_key] = build_asset(
        href=href,
        sink_id=sink_id,
        snapshot_id=result.snapshot_id,
        prefixes=prefixes,
        times=times,
    )
    merged["assets"] = assets

    old_dims = merged.get("cube:dimensions")
    old_dims = old_dims if isinstance(old_dims, dict) else {}
    if result.statics:
        dims = spatial_dimensions(result.statics, result.spatial_dims)
    else:
        # The caller did not read the grid (Z-6's trim). The grid cannot
        # change within a cube (the layout check), so keep what is there.
        dims = {k: v for k, v in old_dims.items() if k != config.append_dim}
    if times is not None:
        interval = [times[0], times[-1]] if times else [None, None]
        dims = {config.append_dim: {"type": "temporal", "extent": interval}, **dims}
        extent = merged.get("extent")
        extent = dict(extent) if isinstance(extent, dict) else {}
        extent["temporal"] = {"interval": [interval]}
        merged["extent"] = extent
    merged["cube:dimensions"] = dims

    extensions = merged.get("stac_extensions")
    kept = [
        e
        for e in (extensions if isinstance(extensions, list) else [])
        if not (isinstance(e, str) and e.startswith(_DATACUBE_PREFIX))
    ]
    merged["stac_extensions"] = [*kept, DATACUBE_EXTENSION]
    return merged


@dataclass(frozen=True)
class Recorded:
    """The sink row as the writer reads it under the collection lock."""

    last_snapshot_id: str | None
    source_prefixes: tuple[str, ...]


def plan_publish(
    content: Mapping[str, Any] | None,
    recorded: Recorded | None,
    *,
    sink_id: str,
    config: CubeSinkConfig,
    result: BatchResult,
    href: str,
) -> tuple[str, dict[str, Any] | None]:
    """The outcome, and the document to write (``None``: write nothing)."""
    if content is None:
        return MISSING_COLLECTION, None
    if recorded is None or recorded.last_snapshot_id != result.snapshot_id:
        return SUPERSEDED, None
    merged = merge_collection(
        content,
        config=config,
        result=result,
        sink_id=sink_id,
        href=href,
        prefixes=recorded.source_prefixes,
    )
    if merged == content:
        return UNCHANGED, None
    return PUBLISHED, merged
```

- [ ] **Step 4: Run the tests.** `uv run pytest tests/test_cube_collection.py -q`. Expected: 19 passed.

- [ ] **Step 5: Lint and commit.**

```bash
uv run ruff check .
git add src/pipeline/cubes/collection.py tests/test_cube_collection.py
git commit -m "Z-5: build the cube collection asset, dimensions and merge"
```

---

### Task 3: `PgCollectionPublisher` and `production_after_batch` — the pgstac write

**Files:**
- Modify: `services/pipeline/src/pipeline/cubes/collection.py` (append the writer)
- Test: `services/pipeline/tests/test_integration_cube_collection.py` (DB-gated)

**Interfaces:**
- Consumes: Task 2's `cube_href`, `plan_publish`, `Recorded` and the outcome constants; `AfterBatch` (`cubes/append.py`, unchanged); `CubeSink` (`cubes/repo.py`); `Settings.database_url`, `Settings.staging_bucket`.
- Produces:
  - `PgCollectionPublisher(database_url: str, bucket: str)` with `async publish(sink, config, result) -> str` (the outcome) and `async after_batch(sink, config, result) -> None`.
  - `production_after_batch(settings: Settings) -> AfterBatch`: Task 4 and Z-6 wire it.

- [ ] **Step 1: Write the DB-gated tests.** Create `tests/test_integration_cube_collection.py`:

```python
"""PgCollectionPublisher against a real pgstac (spec §6.3) -- auto-skips unless
DATABASE_URL is set AND migration 032 has been applied.

    DATABASE_URL=postgresql://... uv run pytest tests/test_integration_cube_collection.py

The pipeline's first production collection write. Every test creates its own
uuid-suffixed collection and sink and removes both in teardown.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import numpy as np
import pytest

from _cube_sources import GOES_CONFIG, GOES_PROJECTION, as_ns, scan
from pipeline.cubes.collection import (
    MISSING_COLLECTION,
    PUBLISHED,
    SUPERSEDED,
    UNCHANGED,
    PgCollectionPublisher,
)
from pipeline.cubes.config import parse_cube_sink_config
from pipeline.cubes.repo import CubeSink
from pipeline.cubes.steps import StaticSpec, canonical_attrs
from pipeline.cubes.write import BatchResult

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set -- skipping DB integration tests"
)

CONFIG = parse_cube_sink_config(GOES_CONFIG)
PREFIXES = ["s3://noaa-goes19/"]


def result(snapshot: str) -> BatchResult:
    return BatchResult(
        outcomes={},
        snapshot_id=snapshot,
        committed=True,
        values=np.array([as_ns(scan(0)), as_ns(scan(1))]),
        trimmed=0,
        initialised=True,
        statics={
            "x": StaticSpec(np.array([-0.1, 0.1], "f4"), None),
            "y": StaticSpec(np.array([0.1, 0.0], "f4"), None),
            "goes_imager_projection": StaticSpec(
                np.array(0, "i4"), canonical_attrs(GOES_PROJECTION)
            ),
        },
        spatial_dims=("y", "x"),
    )


@pytest.fixture
async def db():
    """(raw autocommit connection, the CubeSink, the publisher); the
    collection and the sink are removed after."""
    import psycopg
    from psycopg.types.json import Jsonb

    from pipeline.db.pool import close_pools

    conn = await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True)
    cur = await conn.execute("SELECT to_regclass('stac_higher.cube_sinks')")
    if (await cur.fetchone())[0] is None:
        await conn.close()
        pytest.skip("migration 032 not applied")
    cube = f"z5-cube-{uuid.uuid4().hex[:8]}"
    await conn.execute(
        "SELECT pgstac.create_collection(%s)",
        (
            Jsonb(
                {
                    "type": "Collection",
                    "id": cube,
                    "stac_version": "1.0.0",
                    "description": "itest",
                    "license": "proprietary",
                    "links": [],
                    "keywords": ["user"],
                    "extent": {
                        "spatial": {"bbox": [[-180, -90, 180, 90]]},
                        "temporal": {"interval": [[None, None]]},
                    },
                }
            ),
        ),
    )
    cur = await conn.execute(
        "INSERT INTO stac_higher.cube_sinks"
        " (source_collection_id, cube_collection_id, config, created_by,"
        "  source_prefixes, last_snapshot_id)"
        " VALUES (%s, %s, %s, 'itest', %s, 'SNAP1')"
        " RETURNING id::text, updated_at::text",
        (f"{cube}-src", cube, Jsonb(GOES_CONFIG), PREFIXES),
    )
    sink_id, version = await cur.fetchone()
    sink = CubeSink(
        id=sink_id,
        source_collection_id=f"{cube}-src",
        cube_collection_id=cube,
        enabled=True,
        config=dict(GOES_CONFIG),
        source_prefixes=tuple(PREFIXES),
        last_snapshot_id="SNAP1",
        version=version,
    )
    publisher = PgCollectionPublisher(DATABASE_URL, "platform")
    yield conn, sink, publisher
    await conn.execute("DELETE FROM stac_higher.cube_sinks WHERE id = %s", (sink_id,))
    await conn.execute("SELECT pgstac.delete_collection(%s)", (cube,))
    await conn.close()
    await close_pools()


async def content(conn, collection_id: str) -> dict:
    cur = await conn.execute(
        "SELECT content FROM pgstac.collections WHERE id = %s", (collection_id,)
    )
    return (await cur.fetchone())[0]


async def test_a_publish_merges_the_cube_and_keeps_user_keys(db):
    conn, sink, publisher = db
    assert await publisher.publish(sink, CONFIG, result("SNAP1")) == PUBLISHED
    doc = await content(conn, sink.cube_collection_id)
    asset = doc["assets"]["cube"]
    assert asset["href"] == f"s3://platform/assets/{sink.cube_collection_id}/_cube/"
    assert asset["version"] == "SNAP1"
    assert asset["stac_higher:virtual_chunk_prefixes"] == PREFIXES
    assert asset["stac_higher:time_values"] == [
        "2026-10-03T17:00:00.000000000Z",
        "2026-10-03T17:05:00.000000000Z",
    ]
    assert doc["extent"]["temporal"]["interval"] == [
        ["2026-10-03T17:00:00.000000000Z", "2026-10-03T17:05:00.000000000Z"]
    ]
    assert doc["cube:dimensions"]["x"]["reference_system"]["type"] == "ProjectedCRS"
    assert doc["keywords"] == ["user"]
    assert doc["extent"]["spatial"] == {"bbox": [[-180, -90, 180, 90]]}
    # pgstac's generated columns read the nanosecond strings
    cur = await conn.execute(
        "SELECT datetime, end_datetime FROM pgstac.collections WHERE id = %s",
        (sink.cube_collection_id,),
    )
    start, end = await cur.fetchone()
    assert (start.minute, end.minute) == (0, 5)
    assert await publisher.publish(sink, CONFIG, result("SNAP1")) == UNCHANGED


async def test_a_removed_asset_comes_back(db):
    conn, sink, publisher = db
    await publisher.publish(sink, CONFIG, result("SNAP1"))
    doc = await content(conn, sink.cube_collection_id)
    del doc["assets"]["cube"]
    doc["title"] = "edited"
    from psycopg.types.json import Jsonb

    await conn.execute("SELECT pgstac.update_collection(%s)", (Jsonb(doc),))
    assert await publisher.publish(sink, CONFIG, result("SNAP1")) == PUBLISHED
    doc = await content(conn, sink.cube_collection_id)
    assert doc["assets"]["cube"]["version"] == "SNAP1"
    assert doc["title"] == "edited"


async def test_a_concurrent_bff_edit_is_not_lost(db):
    """The BFF's edit holds the row when the publish starts: the publish waits
    on FOR UPDATE, then merges into the edited document. A plain SELECT would
    read the old document and its update would overwrite the edit."""
    import psycopg
    from psycopg.types.json import Jsonb

    conn, sink, publisher = db
    edited = {**await content(conn, sink.cube_collection_id), "title": "bff edit"}
    bff = await psycopg.AsyncConnection.connect(DATABASE_URL)
    try:
        await bff.execute("SELECT pgstac.update_collection(%s)", (Jsonb(edited),))
        task = asyncio.create_task(publisher.publish(sink, CONFIG, result("SNAP1")))
        for _ in range(100):  # until the publish is waiting on the row lock
            cur = await conn.execute(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE wait_event_type = 'Lock' AND query LIKE %s",
                ("%FOR UPDATE%",),
            )
            if (await cur.fetchone())[0]:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("the publish never waited on the collection row lock")
        assert not task.done()
        await bff.commit()
        assert await task == PUBLISHED
    finally:
        await bff.close()
    doc = await content(conn, sink.cube_collection_id)
    assert doc["title"] == "bff edit"
    assert doc["assets"]["cube"]["version"] == "SNAP1"


async def test_a_tip_that_is_not_the_recorded_one_is_not_published(db):
    conn, sink, publisher = db
    assert await publisher.publish(sink, CONFIG, result("SNAP0")) == SUPERSEDED
    assert "assets" not in await content(conn, sink.cube_collection_id)


async def test_a_missing_cube_collection_is_a_warning_not_an_error(db):
    _, sink, publisher = db
    gone = CubeSink(**{**sink.__dict__, "cube_collection_id": f"{sink.cube_collection_id}-gone"})
    assert await publisher.publish(gone, CONFIG, result("SNAP1")) == MISSING_COLLECTION
```

- [ ] **Step 2: Run them to see them fail.** `uv run pytest tests/test_integration_cube_collection.py -q`. Expected: a collection error, `ImportError: cannot import name 'PgCollectionPublisher'`. The module-level import fails even without `DATABASE_URL`.

- [ ] **Step 3: Implement the writer.** In `src/pipeline/cubes/collection.py`, add three imports in their sorted places (`from pipeline.config import Settings`, `from pipeline.cubes.append import AfterBatch`, `from pipeline.cubes.repo import CubeSink`). `collection` imports `append`, never the reverse, so there is no import cycle. Then append to the end of the module:

```python
@dataclass
class PgCollectionPublisher:
    database_url: str
    #: the platform bucket the repository lives in (``Settings.staging_bucket``)
    bucket: str

    async def _connect(self):  # pragma: no cover - thin pool wrapper
        from pipeline.db.pool import get_async_pool

        return (await get_async_pool(self.database_url)).connection()

    async def publish(self, sink: CubeSink, config: CubeSinkConfig, result: BatchResult) -> str:
        from psycopg.types.json import Jsonb

        href = cube_href(self.bucket, sink.cube_collection_id)
        async with await self._connect() as conn, conn.transaction():
            cur = await conn.execute(
                "SELECT content FROM pgstac.collections WHERE id = %s FOR UPDATE",
                (sink.cube_collection_id,),
            )
            row = await cur.fetchone()
            recorded = None
            if row is not None:
                # Read AFTER the lock: a run recording a newer tip from here on
                # publishes after this transaction, so the last write wins.
                cur = await conn.execute(
                    "SELECT last_snapshot_id, source_prefixes FROM stac_higher.cube_sinks"
                    " WHERE id = %s",
                    (sink.id,),
                )
                found = await cur.fetchone()
                if found is not None:
                    recorded = Recorded(found[0], tuple(found[1] or ()))
            outcome, merged = plan_publish(
                row[0] if row is not None else None,
                recorded,
                sink_id=sink.id,
                config=config,
                result=result,
                href=href,
            )
            if merged is not None:
                await conn.execute("SELECT pgstac.update_collection(%s)", (Jsonb(merged),))
        log = logger.warning if outcome == MISSING_COLLECTION else logger.info
        log(
            "cube collection asset",
            extra={
                "cube_sink_id": sink.id,
                "collection_id": sink.cube_collection_id,
                "snapshot_id": result.snapshot_id,
                "steps": len(result.values),
                "outcome": outcome,
            },
        )
        return outcome

    async def after_batch(
        self, sink: CubeSink, config: CubeSinkConfig, result: BatchResult
    ) -> None:
        await self.publish(sink, config, result)


def production_after_batch(settings: Settings) -> AfterBatch:
    """The ``AfterBatch`` both cube jobs wire in (``cube_append``, Z-6's
    ``cube_maintain_sink``)."""
    return PgCollectionPublisher(settings.database_url, settings.staging_bucket).after_batch
```

- [ ] **Step 4: Run the tests.** `uv run pytest tests/test_cube_collection.py tests/test_integration_cube_collection.py -q`. Expected: 19 passed, 5 skipped without a DB. Lead only, optionally: `DATABASE_URL=postgresql://username:password@localhost:5433/postgis uv run pytest tests/test_integration_cube_collection.py -q` should give 5 passed, then `SELECT count(*) FROM pgstac.collections WHERE id LIKE 'z5-cube-%'` should be 0. CI runs these against its own pgstac.

- [ ] **Step 5: Lint and commit.**

```bash
uv run ruff check .
git add src/pipeline/cubes/collection.py tests/test_integration_cube_collection.py
git commit -m "Z-5: publish the cube on its collection through pgstac under a row lock"
```

---

### Task 4: `cube_append` publishes before the ledger, and production wires the writer

**Files:**
- Modify: `services/pipeline/src/pipeline/cubes/append.py` (`AppendDeps.after_batch` comment; the hook call moves before `finish_rows`)
- Modify: `services/pipeline/src/pipeline/jobs/cubes.py` (`production_append_deps`)
- Test: `services/pipeline/tests/test_cube_append.py`, `services/pipeline/tests/test_cube_jobs.py`

**Interfaces:**
- Consumes: `production_after_batch` and `PgCollectionPublisher` (Task 3).
- Produces: the ordering `_record` → `after_batch` → `finish_rows`. Z-6 relies on it only as a pattern: record first, then publish.

- [ ] **Step 1: Write the failing tests.** In `tests/test_cube_append.py`, REPLACE `test_the_asset_hook_catches_up_after_a_crash`. It asserted the old order, where the hook ran after the ledger, and it fails under the new one. Put these two tests in its place:

```python
async def test_the_asset_hook_runs_after_the_record_and_before_the_ledger(h):
    seen = []

    async def hook(sink, config, result):
        seen.append((h.sink().last_snapshot_id == result.snapshot_id, h.ledger()))

    await h.pend(0)
    await run_cube_append(SINK, h.deps(after_batch=hook))
    assert seen == [(True, {"i0": ("pending", None)})]
    assert h.ledger() == {"i0": ("appended", None)}


async def test_a_failing_asset_hook_releases_the_rows_and_the_redo_publishes(h):
    tips: list[str] = []

    async def hook(sink, config, result):
        tips.append(result.snapshot_id)
        if len(tips) == 1:
            raise ConnectionError("pgstac went away")

    await h.pend(0, 1)
    with pytest.raises(ConnectionError):
        await run_cube_append(SINK, h.deps(after_batch=hook))
    tip = h.sink().last_snapshot_id
    assert tips == [tip]  # the commit is recorded; the publish failed
    assert h.ledger() == {"i0": ("pending", None), "i1": ("pending", None)}
    assert [r.attempts for r in h.repo.rows(SINK)] == [0, 0]  # an outage, not a crash
    assert h.sink().last_error == "ConnectionError: pgstac went away"
    await run_cube_append(SINK, h.deps(after_batch=hook))  # the retry: duplicates only
    assert tips == [tip, tip]
    assert h.sink().last_snapshot_id == tip
    assert h.ledger() == {"i0": ("appended", "duplicate"), "i1": ("appended", "duplicate")}
```

In `tests/test_cube_jobs.py`, add `from pipeline.cubes.collection import PgCollectionPublisher` to the imports. At the end of `test_production_deps_wire_the_real_seams`, add:

```python
    publisher = deps.after_batch.__self__
    assert isinstance(publisher, PgCollectionPublisher)
    assert (publisher.database_url, publisher.bucket) == (
        settings.database_url,
        settings.staging_bucket,
    )
```

- [ ] **Step 2: Run them to see them fail.** `uv run pytest tests/test_cube_append.py -k "asset_hook" tests/test_cube_jobs.py -q`. Expected: the ledger assertion in the first test fails (the hook currently sees `appended`). The second fails because rows are already finished. The jobs test fails with `AttributeError: 'NoneType' object has no attribute '__self__'`.

- [ ] **Step 3: Move the hook.** In `cubes/append.py` `_append_rows`, delete the two lines after the report counting loop:

```python
    if result is not None and result.initialised and deps.after_batch is not None:
        await deps.after_batch(sink, config, result)
```

and add the call at the end of the `if parsed:` block, after the `for row_id, (status, reason) in result.outcomes.items():` loop and before `await deps.repo.finish_rows(...)`:

```python
        if result.initialised and deps.after_batch is not None:
            # Publish BEFORE the ledger: a raise here releases the rows and the
            # job retries, and the redo (duplicates only) publishes again. A
            # worker that dies here leaves them claimed; the next take redoes
            # them the same way. The asset never lags a finished batch (#101).
            await deps.after_batch(sink, config, result)
```

Replace the `after_batch` field comment on `AppendDeps` with:

```python
    #: the collection asset writer (``cubes.collection.production_after_batch``):
    #: awaited after every batch that reached the repository, once the tip is
    #: recorded and BEFORE the ledger is written; must be idempotent
```

- [ ] **Step 4: Wire production.** In `jobs/cubes.py`, add `from pipeline.cubes.collection import production_after_batch` after the `pipeline.cubes.append` import. Then add `after_batch=production_after_batch(settings),` as the last argument of the `AppendDeps(...)` in `production_append_deps`. Update the module docstring's `pipeline.cube_append` bullet by appending this sentence to it: "After the commit is recorded and before the ledger is written, it publishes the cube on its collection (`cubes.collection`, Z-5)."

- [ ] **Step 5: Run the whole suite.** `uv run pytest -q`. Expected: all pass (2025 passed, 47 skipped at pre-validation). `test_after_batch_runs_after_every_batch_that_reached_the_cube` must still pass unchanged.

- [ ] **Step 6: Lint and commit.**

```bash
uv run ruff check .
git add src/pipeline/cubes/append.py src/pipeline/jobs/cubes.py tests/test_cube_append.py tests/test_cube_jobs.py
git commit -m "Z-5: cube_append publishes the asset before the ledger; production wires it"
```

---

### Task 5: Docs, gates, PR

**Files:**
- Modify: `docs/serving.md` (new section at the end)
- Modify: `docs/FEATURES.md` (Z-5 row in the "Virtual Icechunk cube sink (Z queue)" table, after the Z-4 row)

- [ ] **Step 1: `docs/serving.md`.** Append:

````markdown
## Virtual cubes: the collection asset

A cube sink (ADR 0022; spec `docs/superpowers/specs/2026-10-03-virtual-cube-sink-design.md`)
publishes its Icechunk repository on the **cube collection** after every batch
that reached the repository, and after Z-6's maintenance trim
(`services/pipeline/src/pipeline/cubes/collection.py`). The `cube-server` that
serves it as tiles and EDR is Z-7.

```jsonc
"assets": {
  "cube": {
    "href": "s3://{platform bucket}/assets/{cube collection}/_cube/",
    "type": "application/vnd.zarr+icechunk",
    "roles": ["data", "references", "virtual", "latest-version"],
    "title": "Virtual cube",
    "version": "<icechunk snapshot id of main>",
    "stac_higher:virtual_chunk_prefixes": ["s3://noaa-goes19/"],
    "stac_higher:cube_sink_id": "<uuid>",
    "stac_higher:time_values": ["2026-10-03T17:02:36.714359936Z", "…"]
  }
},
"extent": { "temporal": { "interval": [["<first t>", "<last t>"]] } },
"cube:dimensions": {
  // x/y: GOES-East CONUS, approximately
  "t": { "type": "temporal", "extent": ["<first t>", "<last t>"] },
  "x": { "type": "spatial", "axis": "x", "extent": [-3626269.5, 1381770.0], "reference_system": { "…PROJJSON" } },
  "y": { "type": "spatial", "axis": "y", "extent": [1584175.1, 4588198.0], "reference_system": { "…PROJJSON" } }
},
"stac_extensions": ["…", "https://stac-extensions.github.io/datacube/v2.2.0/schema.json"]
```

- **Ownership:** the pipeline owns `assets.{asset_key}` (default `cube`),
  `extent.temporal`, `cube:dimensions` and the Datacube entry of
  `stac_extensions`, and rewrites them on every publish; every other key is
  the user's. A hand edit to those four lasts until the next commit, and a
  deleted asset comes back. pgstac has no partial update, so an edit saved
  from a copy read **before** a publish replaces the whole document: the
  asset is gone until the next commit (≤ 5 min at the GOES cadence).
- **`version`** is the snapshot the sink recorded
  (`cube_sinks.last_snapshot_id`). Only that tip is published, under a row lock
  on the collection, so a double run never publishes an older snapshot.
- **`stac_higher:time_values`** holds the exact nanosecond `t` values. Build
  frames from this list and select `t` with these strings verbatim (or
  `nearest::`). JavaScript's `Date` truncates them to milliseconds, so never
  round-trip them through it.
- **`x`/`y` extents are projected metres:** a geostationary grid's scan angle
  times `perspective_point_height`, the same rescale the cube-server applies.
  `reference_system` is the grid mapping's PROJJSON, for `geostationary` only
  in v1; any other grid omits it.
- **An empty cube** (trimmed to zero steps) has `time_values: []` and
  `[null, null]` intervals.
- **Size:** each step adds about 33 bytes to the collection document: 288
  steps ≈ 10 KB, and the 10,000-step maximum ≈ 330 KB, carried by every
  `/collections` response.
- **No audit row per publish** (spec §14.3). Each publish logs one
  `cube collection asset` line with `outcome`: `published`, `unchanged`,
  `superseded` or `missing_collection`.
````

- [ ] **Step 2: `docs/FEATURES.md`.** Add after the Z-4 row:

```markdown
| Z-5 · Collection asset writer | ✅ | `pipeline/cubes/collection.py`: after every batch that reached the repository, `cube_append` publishes the cube on its collection — `assets.{asset_key}` (spec §3.3: `application/vnd.zarr+icechunk`, `version` = snapshot id, `stac_higher:virtual_chunk_prefixes`, `stac_higher:cube_sink_id`, exact-ns `stac_higher:time_values`), `extent.temporal`, Datacube `cube:dimensions` (`x`/`y` in projected metres, geostationary PROJJSON via rasterio) and the Datacube v2.2.0 extension; every other key is the user's. One transaction on a plain pool connection: `SELECT … FOR UPDATE`, then only the sink's **recorded** tip (`cube_sinks.last_snapshot_id`, read under the lock; else `superseded`), merge, `pgstac.update_collection`; an unchanged document is not rewritten; no audit row, one `cube collection asset` log line. The hook (`AppendDeps.after_batch` = `production_after_batch(settings)`, shared with Z-6) now runs after the record and **before** the ledger, so a failed publish releases the rows and the retry republishes. Shape: `docs/serving.md` |
```

- [ ] **Step 3: Gates.** From the repo root, `npm run verify`. From `services/pipeline/`, `uv run pytest -q` and `uv run ruff check .`. All must pass. Paste the summary lines into the PR body.

- [ ] **Step 4: Commit and push (lead).**

```bash
git add docs/serving.md docs/FEATURES.md
git commit -m "Z-5: document the cube collection asset"
git push -u origin feat/z5-cube-collection-asset
```

- [ ] **Step 5: PR (lead).** `gh pr create --base main --title "Z-5: publish the cube asset on its collection after each commit"`. The body starts `Closes #91` and lists:
  - the gates run;
  - that CI runs the DB-gated `test_integration_cube_collection.py`;
  - Decisions 1–12 above, especially 1 (recorded tip only), 3 (the hook moved before the ledger, which resolves PR #101's follow-up) and 6 (geostationary-only PROJJSON);
  - the Z-6 contract: record before publish, `production_after_batch(settings)`, the optional `statics`/`spatial_dims`;
  - lead-only steps: none for #91. Z-9 checks the asset on the live stack.

  On merge:
  - remove the worktree;
  - flip #93 (Z-7) from `blocked` to `ready` if Z-5 was its last blocker (Z-2 is merged);
  - if Z-6 has merged first, its `production_maintain_deps` gets `after_batch=production_after_batch(settings)` in this PR instead (agreed with the Z-6 planner).

---

## Self-review notes (for the reviewer of this plan)

- **Spec coverage:**
  - §6.3 transaction, row lock and the four keys: Tasks 2 and 3.
  - §3.3 asset shape: Task 2 `test_the_document_matches_the_spec_shape`.
  - Exact-ns `t`: `time_strings`.
  - `cube:dimensions` with PROJJSON and metres: `spatial_dimensions`, `crs_projjson`.
  - "Call it from the hook": Task 4.
  - One log line: Task 3.
  - `docs/serving.md`: Task 5.
- **Issue #91's acceptance criteria:**
  - "user-edited keys survive": Task 3 `test_a_publish_merges_the_cube_and_keeps_user_keys`.
  - "a removed `assets.cube` is restored": `test_a_removed_asset_comes_back`.
  - "a concurrent BFF edit is not lost": `test_a_concurrent_bff_edit_is_not_lost`, mutation-checked.
  - "unit test against an expected document": Task 2.
- **Beyond the issue:** the recorded-tip guard (Decision 1), reading the prefixes from the row (2), the hook ordering (3), and Z-6 compatibility (12).
