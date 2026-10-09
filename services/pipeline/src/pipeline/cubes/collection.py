"""The cube's collection asset (virtual cube spec §6.3, §3.3; ADR 0022).

After every batch that reached the repository, ``cube_append`` publishes the
cube on its collection: ``assets.{asset_key}``, ``extent.temporal``,
``cube:dimensions`` and the Datacube extension in ``stac_extensions``. Z-6's
maintenance trim publishes through the same hook. This is the pipeline's
first production collection write:
- One transaction on a plain pool connection, not the item writer pool:
  ``SELECT … FOR NO KEY UPDATE`` on the pgstac row, merge,
  ``pgstac.update_collection``. The row lock serializes against a BFF edit,
  which also goes through pgstac. ``NO KEY`` (spec §6.3 says ``FOR UPDATE``)
  because the id never changes: it still conflicts with the BFF's UPDATE, but
  not with the KEY SHARE locks item inserts take through ``items_collections_fk``.
  A ``lock_timeout`` bounds the wait: the job holds the sink's ``cube:{id}``
  lock meanwhile, and a timeout is a DB error the job retries.
- Only those four keys change, plus any stale cube asset (one carrying a
  ``stac_higher:cube_sink_id`` under another key: an earlier ``asset_key``
  or a deleted sink's), which is removed. A user's other edits survive, and
  a removed asset comes back.
- Only the sink's RECORDED tip is published (``cube_sinks.last_snapshot_id``,
  read under the lock), so a double run never publishes an older snapshot
  over a newer one (``superseded``).
- An unchanged document is not rewritten (``unchanged``).
- No audit row (spec §14.3): one structured log line per publish.

The builders never raise on odd metadata: they drop a field
(``reference_system``, a spatial dimension) instead, because a raise here
keeps the batch's ledger rows pending while the job retries. The ``x``/``y``
extents are in the projected metres the cube-server presents: a geostationary
grid's scan angles times ``perspective_point_height`` (spec §8.2). The
cube-server also checks ``x.units == "rad"``; the writer cannot, because
``StaticSpec`` keeps no attributes for 1-D variables, so it assumes radians,
as CF requires of a geostationary grid's coordinates (true of every GOES file).
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

from pipeline.config import Settings
from pipeline.cubes.append import AfterBatch
from pipeline.cubes.config import CubeSinkConfig
from pipeline.cubes.icerepo import cube_prefix
from pipeline.cubes.repo import CubeSink
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
    # A NaT would print as "NaTZ", which pgstac's generated datetime columns
    # cannot cast: a deterministic error that would hold the rows pending.
    values = values[~np.isnat(values)]
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
    ``assets.{asset_key}`` (and any stale cube asset under another key),
    ``extent.temporal``, ``cube:dimensions`` and the Datacube entry of
    ``stac_extensions``."""
    merged = copy.deepcopy(dict(content))
    times = time_strings(result.values)

    assets = merged.get("assets")
    assets = dict(assets) if isinstance(assets, dict) else {}
    # asset_key is not layout, so the app lets it change after a publish; a
    # deleted sink's asset stays on the collection (spec §7). cube_collection_id
    # is UNIQUE, so every cube asset here other than the one written below is
    # stale and would stay behind, frozen.
    assets = {
        key: asset
        for key, asset in assets.items()
        if not (isinstance(asset, dict) and "stac_higher:cube_sink_id" in asset)
    }
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
        if not dims:
            # The UI's collection form drops cube:dimensions on every save;
            # only a caller that passes the grid can put x/y back.
            logger.warning(
                "cube collection asset: no spatial dimensions to keep or build",
                extra={"cube_sink_id": sink_id, "snapshot_id": result.snapshot_id},
            )
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


@dataclass
class PgCollectionPublisher:
    database_url: str
    #: the platform bucket the repository lives in (``Settings.staging_bucket``)
    bucket: str
    #: how long to wait for the collection row; a BFF edit holds it for
    #: milliseconds, so a longer wait is a stuck transaction (retry later)
    lock_timeout: str = "10s"

    async def _connect(self):  # pragma: no cover - thin pool wrapper
        from pipeline.db.pool import get_async_pool

        return (await get_async_pool(self.database_url)).connection()

    async def publish(self, sink: CubeSink, config: CubeSinkConfig, result: BatchResult) -> str:
        from psycopg.types.json import Jsonb

        href = cube_href(self.bucket, sink.cube_collection_id)
        async with await self._connect() as conn, conn.transaction():
            await conn.execute("SELECT set_config('lock_timeout', %s, true)", (self.lock_timeout,))
            cur = await conn.execute(
                "SELECT content FROM pgstac.collections WHERE id = %s FOR NO KEY UPDATE",
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
