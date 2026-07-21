"""EXTRACT stage: build a STAC item dict from a group's stored members (§6.1).

Three metadata strategies (§5.1): `raster_auto` (rio-stac over the primary
raster), `sidecar` (parse an adjacent XML/JSON file), `defaults_only` (no
extraction — a null-geometry item from collection defaults). Member bytes are
read via a `MemberByteSource` seam — `CanonicalByteSource` (copy mode: FETCH
already wrote them to canonical storage) or `SourceAdapterByteSource`
(reference mode: read in place from the source adapter) — so `build_item` is
storage-mode-agnostic; raster reads go through an in-memory `rasterio.MemoryFile`
either way — no GDAL S3 config needed. Output is a plain STAC item dict ready
for the ITEMIZE validation gate; a field that can't be resolved raises
`ExtractError` (→ group marked failed) rather than emitting a bad item.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
import posixpath
from dataclasses import dataclass
from typing import Any, Protocol
from xml.etree.ElementTree import Element, ParseError  # types only

import pystac

# defusedxml hardens against XXE + entity-expansion DoS (billion-laughs /
# quadratic-blowup) that stdlib xml.etree does NOT defend against. Required by
# the FISMA-High posture — never swap this back to stdlib.
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring as _xml_fromstring

from pipeline.connections.adapters.base import StorageAdapter
from pipeline.ingest.discover import source_fetch_path
from pipeline.storage import platform
from pipeline.storage.keys import asset_href

STAC_VERSION = "1.0.0"

_RASTER_MEDIA_TYPES: dict[str, str] = {
    ".tif": "image/tiff; application=geotiff",
    ".tiff": "image/tiff; application=geotiff",
    ".jp2": "image/jp2",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
RASTER_EXTS = frozenset(_RASTER_MEDIA_TYPES)
MEDIA_TYPES: dict[str, str] = {
    **_RASTER_MEDIA_TYPES,
    ".json": "application/json",
    ".geojson": "application/geo+json",
    ".xml": "application/xml",
}
#: Best-effort GDAL open (ISSUE I-27) also covers gridded/scientific formats
#: GDAL can read but that raster_auto/sidecar don't otherwise target.
GDAL_CANDIDATE_EXTS = RASTER_EXTS | frozenset(
    {".nc", ".nc4", ".grib", ".grib2", ".grb", ".zarr", ".hdf", ".h5", ".vrt", ".img"}
)

#: Provenance key stac-higher stamps on `item.properties` recording how the
#: item's geometry was resolved (`"sidecar"`, `"raster"`, `"collection_extent"`,
#: `"global_fallback"`).
GEOMETRY_SOURCE_PROP = "stac_higher:geometry_source"


class ExtractError(ValueError):
    """A group cannot be turned into a valid STAC item (missing required field,
    unreadable raster, unparseable sidecar)."""


@dataclass(frozen=True)
class ExtractMember:
    """One stored group member EXTRACT reads. ``canonical_key`` is the object
    key FETCH wrote; ``observed_at`` is the ledger's settle time (the
    ``file_mtime`` datetime proxy — no durable source mtime exists)."""

    source_path: str
    filename: str
    canonical_key: str
    observed_at: dt.datetime | None


@dataclass(frozen=True)
class MetadataConfig:
    strategy: str
    sidecar_pattern: str | None
    sidecar_parser: str
    default_datetime: str | None
    #: `"collection"` opts into the collection-extent geometry fallback
    #: (ISSUE I-27); any other/absent value is treated as unset (forward-
    #: compatible — don't raise on a value a newer app might send).
    default_geometry: str | None


def parse_metadata(raw: dict[str, Any]) -> MetadataConfig:
    """Typed view over the association config's ``metadata`` block (§5.1)."""
    sidecar = raw.get("sidecar") or {}
    defaults = raw.get("defaults") or {}
    strategy = str(raw.get("strategy", "raster_auto"))
    if strategy not in ("raster_auto", "sidecar", "defaults_only"):
        raise ExtractError(f"unknown metadata.strategy {strategy!r}")
    default_geometry = defaults.get("geometry")
    if default_geometry != "collection":
        default_geometry = None
    return MetadataConfig(
        strategy=strategy,
        sidecar_pattern=sidecar.get("pattern"),
        sidecar_parser=str(sidecar.get("parser", "generic_xml")),
        default_datetime=defaults.get("datetime"),
        default_geometry=default_geometry,
    )


def media_type_for(filename: str) -> str:
    ext = posixpath.splitext(filename)[1].lower()
    return MEDIA_TYPES.get(ext, "application/octet-stream")


def is_raster(filename: str) -> bool:
    return posixpath.splitext(filename)[1].lower() in RASTER_EXTS


def is_gdal_candidate(filename: str) -> bool:
    return posixpath.splitext(filename)[1].lower() in GDAL_CANDIDATE_EXTS


def _parse_rfc3339(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed


def resolve_datetime(
    extracted: dt.datetime | None, cfg: MetadataConfig, primary: ExtractMember
) -> dt.datetime:
    """extracted → metadata.defaults.datetime → error. `file_mtime` uses the
    member's ledger settle time (documented approximation)."""
    if extracted is not None:
        return extracted if extracted.tzinfo else extracted.replace(tzinfo=dt.UTC)
    default = cfg.default_datetime
    if default == "file_mtime":
        if primary.observed_at is not None:
            return primary.observed_at
        raise ExtractError("file_mtime requested but no observed time on the ledger row")
    if default:
        return _parse_rfc3339(default)
    raise ExtractError("no datetime could be resolved (no extraction, no default)")


def _rfc3339(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")


class MemberByteSource(Protocol):
    """Where EXTRACT reads a member's bytes from — canonical storage (copy mode)
    or the source adapter (reference mode). Lets build_item stay mode-agnostic."""

    async def read(self, member: ExtractMember) -> bytes: ...


@dataclass(frozen=True)
class CanonicalByteSource:
    """Copy mode: read the object FETCH wrote to canonical platform storage."""

    s3_client: platform.S3Like
    bucket: str

    async def read(self, member: ExtractMember) -> bytes:
        return await asyncio.to_thread(
            platform.get_object, self.s3_client, self.bucket, member.canonical_key
        )


@dataclass(frozen=True)
class SourceAdapterByteSource:
    """Reference mode: read the object in place from the source adapter."""

    adapter: StorageAdapter
    source_path: str

    async def read(self, member: ExtractMember) -> bytes:
        return await self.adapter.get(source_fetch_path(self.source_path, member.source_path))


def build_assets(
    members: list[ExtractMember],
    collection_id: str,
    item_id: str,
    asset_href_base: str,
    primary_filename: str,
) -> dict[str, dict[str, Any]]:
    """One asset per member, keyed by filename stem; primary gets role `data`,
    others `metadata`. Hrefs point at the app asset route. On a stem collision
    (e.g. a raster and its sidecar sharing a basename), the `data` entry always
    wins, regardless of member order."""
    assets: dict[str, dict[str, Any]] = {}
    for m in members:
        key = posixpath.splitext(m.filename)[0]
        role = "data" if m.filename == primary_filename else "metadata"
        if assets.get(key, {}).get("roles") == ["data"] and role != "data":
            continue
        assets[key] = {
            "href": asset_href(collection_id, item_id, m.filename, base=asset_href_base),
            "type": media_type_for(m.filename),
            "roles": [role],
        }
    return assets


def _primary(members: list[ExtractMember]) -> ExtractMember:
    for m in members:
        if is_raster(m.filename):
            return m
    return members[0]


def _find_datetime_in_xml(root: Element) -> dt.datetime | None:
    # Look for a small set of common date-ish tags (namespace-agnostic: compare
    # local tag names). Minimal MVP field set — richer mapping is a follow-up.
    wanted = {"datetime", "acquired", "date", "acquisitiondate", "start_datetime"}
    for el in root.iter():
        local = el.tag.rsplit("}", 1)[-1].lower()
        if local in wanted and el.text and el.text.strip():
            try:
                return _parse_rfc3339(el.text.strip())
            except ValueError:
                continue
    return None


def parse_sidecar(data: bytes, parser: str) -> dict[str, Any]:
    """Extract a minimal field set from a sidecar. XXE-safe: the stdlib XML
    parser does not resolve external entities, and a DOCTYPE with entities is
    rejected."""
    if parser == "json":
        try:
            doc = json.loads(data)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ExtractError(f"sidecar JSON parse failed: {exc}") from exc
        when = doc.get("datetime")
        return {
            "datetime": _parse_rfc3339(when) if isinstance(when, str) else None,
            "geometry": doc.get("geometry"),
            "properties": doc.get("properties") or {},
        }
    if parser == "generic_xml":
        try:
            root = _xml_fromstring(data)
        except (ParseError, DefusedXmlException) as exc:
            # DefusedXmlException = DOCTYPE/entity/external-ref blocked (XXE/DoS).
            raise ExtractError(f"sidecar XML parse failed/blocked: {exc}") from exc
        return {"datetime": _find_datetime_in_xml(root), "geometry": None, "properties": {}}
    raise ExtractError(f"unknown sidecar parser {parser!r}")


def _collect_coordinate_pairs(coordinates: Any, out: list[tuple[float, float]]) -> None:
    """Recursively walk a GeoJSON ``coordinates`` structure, collecting the
    first two ordinates (x, y) of every leaf position."""
    if not coordinates:
        return
    first = coordinates[0]
    if isinstance(first, int | float):
        # A leaf position: [x, y] or [x, y, z].
        out.append((coordinates[0], coordinates[1]))
        return
    for item in coordinates:
        _collect_coordinate_pairs(item, out)


def bbox_from_geometry(geom: dict[str, Any]) -> list[float]:
    """Compute a 2D ``[min_x, min_y, max_x, max_y]`` bbox from a GeoJSON
    geometry's ``coordinates`` (Point/LineString/Polygon/MultiPoint/
    MultiLineString/MultiPolygon). Raises ``ExtractError`` if the geometry has
    no usable coordinates (e.g. a ``GeometryCollection``)."""
    pairs: list[tuple[float, float]] = []
    _collect_coordinate_pairs(geom.get("coordinates"), pairs)
    if not pairs:
        raise ExtractError(
            f"cannot compute bbox for geometry type {geom.get('type')!r}: no coordinates"
        )
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    return [min(xs), min(ys), max(xs), max(ys)]


def bbox_to_polygon(bbox: list[float]) -> dict[str, Any]:
    """Axis-aligned Polygon geojson from a ``[w, s, e, n]`` bbox."""
    w, s, e, n = bbox
    return {
        "type": "Polygon",
        "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
    }


def _set_geometry(
    item: dict[str, Any], geometry: dict[str, Any], bbox: list[float], source: str
) -> None:
    """Set geometry/bbox/provenance together (the three always change as a unit)."""
    item["geometry"] = geometry
    item["bbox"] = bbox
    item["properties"][GEOMETRY_SOURCE_PROP] = source


def _raster_crs_and_bounds(ds: Any) -> tuple[Any, Any]:
    """CRS + bounds for an opened rasterio dataset, or ``(None, None)`` if it
    isn't georeferenced. Falls through to the first subdataset for container
    formats (netCDF/HDF/GRIB) whose top-level dataset lacks its own CRS."""
    if ds.crs is not None and ds.transform is not None and not ds.transform.is_identity:
        return ds.crs, ds.bounds
    subdatasets = getattr(ds, "subdatasets", None) or []
    if subdatasets:
        import rasterio

        with rasterio.open(subdatasets[0]) as sub:
            if sub.crs is not None and sub.transform is not None:
                return sub.crs, sub.bounds
    return None, None


def geometry_from_raster(data: bytes) -> tuple[dict[str, Any], list[float]] | None:
    """Best-effort GDAL open of arbitrary raster/gridded bytes (COG, GeoTIFF,
    netCDF, GRIB, ...) — returns ``(geometry, bbox)`` reprojected to
    EPSG:4326, or ``None`` if the bytes can't be opened or georeferenced.
    Never raises (ISSUE I-27 best-effort layer). ``rasterio`` is imported
    lazily so this module stays GDAL-free at import time.
    """
    import rasterio
    from rasterio.warp import transform_bounds

    crs = bounds = None
    try:
        with rasterio.io.MemoryFile(data) as mem, mem.open() as ds:
            crs, bounds = _raster_crs_and_bounds(ds)
    except Exception:
        crs = bounds = None

    if crs is None:
        # Some HDF-backed netCDF/GRIB files can't be opened from /vsimem —
        # spill to a real temp file and retry (verified feasibility finding).
        import tempfile

        try:
            with tempfile.NamedTemporaryFile(suffix=".tmp") as tmp:
                tmp.write(data)
                tmp.flush()
                with rasterio.open(tmp.name) as ds:
                    crs, bounds = _raster_crs_and_bounds(ds)
        except Exception:
            crs = bounds = None

    if crs is None or bounds is None:
        return None

    try:
        w, s, e, n = transform_bounds(crs, "EPSG:4326", *bounds, densify_pts=21)
    except Exception:
        return None

    if not all(math.isfinite(v) for v in (w, s, e, n)) or w >= e or s >= n:
        return None

    bbox = [w, s, e, n]
    return bbox_to_polygon(bbox), bbox


def _base_item(
    collection_id: str,
    item_id: str,
    members: list[ExtractMember],
    primary: ExtractMember,
    when: dt.datetime,
    asset_href_base: str,
) -> dict[str, Any]:
    """The Feature skeleton shared by `build_defaults_only` and `build_sidecar`
    (identical keys; geometry/bbox/extra properties are layered on by the
    caller). `build_raster_auto` goes through rio-stac and doesn't use this."""
    return {
        "type": "Feature",
        "stac_version": STAC_VERSION,
        "stac_extensions": [],
        "id": item_id,
        "collection": collection_id,
        "geometry": None,
        "properties": {"datetime": _rfc3339(when)},
        "assets": build_assets(
            members, collection_id, item_id, asset_href_base, primary.filename
        ),
        "links": [],
    }


def build_sidecar(
    collection_id: str,
    item_id: str,
    members: list[ExtractMember],
    cfg: MetadataConfig,
    sidecar_bytes: bytes,
    asset_href_base: str,
) -> dict[str, Any]:
    if not members:
        raise ExtractError("no members to itemize")
    primary = _primary(members)
    parsed = parse_sidecar(sidecar_bytes, cfg.sidecar_parser)
    when = resolve_datetime(parsed["datetime"], cfg, primary)
    item = _base_item(collection_id, item_id, members, primary, when, asset_href_base)
    item["geometry"] = parsed["geometry"]
    # `datetime` (already resolved into item["properties"]) always wins over a
    # same-named key in the sidecar's own properties — matches the original
    # `{**parsed["properties"], "datetime": ...}` merge order.
    item["properties"] = {**parsed["properties"], **item["properties"]}
    if parsed["geometry"] is not None:
        item["bbox"] = bbox_from_geometry(parsed["geometry"])
        item["properties"][GEOMETRY_SOURCE_PROP] = "sidecar"
    return item


def build_defaults_only(
    collection_id: str,
    item_id: str,
    members: list[ExtractMember],
    cfg: MetadataConfig,
    asset_href_base: str,
) -> dict[str, Any]:
    """A null-geometry STAC item from collection defaults (no extraction)."""
    if not members:
        raise ExtractError("no members to itemize")
    primary = _primary(members)
    when = resolve_datetime(None, cfg, primary)
    return _base_item(collection_id, item_id, members, primary, when, asset_href_base)


def build_raster_auto(
    collection_id: str,
    item_id: str,
    members: list[ExtractMember],
    cfg: MetadataConfig,
    raster_bytes: bytes,
    asset_href_base: str,
) -> dict[str, Any]:
    """rio-stac over the primary raster (read from an in-memory file), with asset
    hrefs rewritten to the app route and all group members attached as assets.

    ``rasterio``/``rio_stac`` are imported here, not at module scope, so
    importing this module stays cheap and non-raster tests don't require GDAL.
    """
    import rasterio
    from rio_stac.stac import create_stac_item

    primary = _primary(members)
    when = resolve_datetime(None, cfg, primary) if cfg.default_datetime else None
    try:
        with rasterio.io.MemoryFile(raster_bytes) as mem, mem.open() as src:
            item = create_stac_item(
                source=src,
                id=item_id,
                collection=collection_id,
                input_datetime=when,  # None → rio-stac uses dataset/now
                asset_name=posixpath.splitext(primary.filename)[0],
                asset_href=asset_href(
                    collection_id, item_id, primary.filename, base=asset_href_base
                ),
                asset_roles=["data"],
                asset_media_type=media_type_for(primary.filename),
                with_proj=True,
                with_raster=True,
            )
    except Exception as exc:
        raise ExtractError(f"raster_auto extraction failed: {exc}") from exc

    # Attach non-primary members as additional assets. A member sharing the
    # primary's stem (e.g. a scene.xml sidecar next to scene.tif) must not
    # clobber the data asset create_stac_item already added under that key —
    # the data asset wins on stem collision, same rule as build_assets.
    for m in members:
        if m.filename == primary.filename:
            continue
        key = posixpath.splitext(m.filename)[0]
        if key in item.assets:
            continue
        item.add_asset(
            key,
            pystac.Asset(
                href=asset_href(collection_id, item_id, m.filename, base=asset_href_base),
                media_type=media_type_for(m.filename),
                roles=["metadata"],
            ),
        )
    if item.geometry is not None:
        item.properties[GEOMETRY_SOURCE_PROP] = "raster"
    return item.to_dict(include_self_link=False, transform_hrefs=False)


async def _best_effort_raster_geometry(
    primary: ExtractMember, byte_source: MemberByteSource
) -> tuple[dict[str, Any], list[float]] | None:
    """Best-effort GDAL open of the primary member (ISSUE I-27 layer 2) — only
    attempted for a GDAL-candidate extension, and never raises (an unreadable
    or missing object just means "no geometry recovered")."""
    if not is_gdal_candidate(primary.filename):
        return None
    try:
        data = await byte_source.read(primary)
    except Exception:
        return None
    return geometry_from_raster(data)


async def _resolve_geometry_fallback(
    item: dict[str, Any],
    members: list[ExtractMember],
    *,
    byte_source: MemberByteSource,
    collection_fallback: dict[str, Any] | None,
) -> None:
    """Layers 2-4 of the ISSUE I-27 resolution order: best-effort GDAL open,
    then the opt-in collection-extent fallback, then fail-fast. Mutates
    ``item`` in place (geometry/bbox/provenance) or raises ``ExtractError``.
    """
    primary = _primary(members)
    recovered = await _best_effort_raster_geometry(primary, byte_source)
    if recovered is not None:
        geometry, bbox = recovered
        _set_geometry(item, geometry, bbox, "raster")
        return

    if collection_fallback is not None:
        _set_geometry(
            item,
            collection_fallback["geometry"],
            collection_fallback["bbox"],
            collection_fallback["source"],
        )
        return

    raise ExtractError(
        f"no geometry could be resolved for item {item['id']!r}: no strategy geometry, "
        "no best-effort GDAL read, and no collection-extent fallback opted in (ISSUE I-27)"
    )


async def build_item(
    *,
    collection_id: str,
    item_id: str,
    members: list[ExtractMember],
    metadata: dict[str, Any],
    byte_source: MemberByteSource,
    asset_href_base: str,
    collection_fallback: dict[str, Any] | None = None,
    cfg: MetadataConfig | None = None,
) -> dict[str, Any]:
    """Dispatch on metadata.strategy, reading member bytes via ``byte_source``
    only when the strategy needs them — storage-mode-agnostic (copy reads
    canonical storage, reference reads the source adapter; see
    `CanonicalByteSource`/`SourceAdapterByteSource`). If the strategy leaves the
    item with a null geometry, falls through the ISSUE I-27 resolution chain
    (best-effort GDAL open → opt-in collection-extent fallback → fail-fast)
    rather than emitting a null-geometry item, which pgstac rejects.

    ``cfg`` lets a caller that already parsed ``metadata`` (e.g. `run_itemize`)
    pass it through instead of having it re-parsed here."""
    if not members:
        raise ExtractError("no members to itemize")
    cfg = cfg if cfg is not None else parse_metadata(metadata)

    if cfg.strategy == "defaults_only":
        item = build_defaults_only(collection_id, item_id, members, cfg, asset_href_base)
    elif cfg.strategy == "sidecar":
        sidecar = _match_sidecar(members, cfg)
        data = await byte_source.read(sidecar)
        item = build_sidecar(collection_id, item_id, members, cfg, data, asset_href_base)
    else:
        # raster_auto
        primary = _primary(members)
        if not is_raster(primary.filename):
            # No raster to read via rio-stac — fall through to the same
            # null-geometry base as defaults_only, then the resolution chain.
            item = build_defaults_only(collection_id, item_id, members, cfg, asset_href_base)
        else:
            data = await byte_source.read(primary)
            item = build_raster_auto(collection_id, item_id, members, cfg, data, asset_href_base)

    if item.get("geometry") is None:
        await _resolve_geometry_fallback(
            item,
            members,
            byte_source=byte_source,
            collection_fallback=collection_fallback,
        )
    return item


def _match_sidecar(members: list[ExtractMember], cfg: MetadataConfig) -> ExtractMember:
    """Locate the sidecar member by pattern (`{basename}.xml` → suffix match)."""
    if cfg.sidecar_pattern:
        suffix = cfg.sidecar_pattern.split("}", 1)[-1]  # `{basename}.xml` → `.xml`
        for m in members:
            if m.filename.endswith(suffix) and not is_raster(m.filename):
                return m
    for m in members:
        if not is_raster(m.filename):
            return m
    raise ExtractError("sidecar strategy but no sidecar member found")
