"""EXTRACT stage: build a STAC item dict from a group's stored members (§6.1).

Three metadata strategies (§5.1): `raster_auto` (rio-stac over the primary
raster), `sidecar` (parse an adjacent XML/JSON file), `defaults_only` (no
extraction — a null-geometry item from collection defaults). The bytes are read
back from canonical storage (FETCH already put them there), so raster reads go
through an in-memory `rasterio.MemoryFile` — no GDAL S3 config needed. Output is
a plain STAC item dict ready for the ITEMIZE validation gate; a field that can't
be resolved raises `ExtractError` (→ group marked failed) rather than emitting a
bad item.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import posixpath
from dataclasses import dataclass
from typing import Any
from xml.etree.ElementTree import Element, ParseError  # types only

import pystac

# defusedxml hardens against XXE + entity-expansion DoS (billion-laughs /
# quadratic-blowup) that stdlib xml.etree does NOT defend against. Required by
# the FISMA-High posture — never swap this back to stdlib.
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring as _xml_fromstring

from pipeline.storage import platform
from pipeline.storage.keys import asset_href

STAC_VERSION = "1.0.0"

MEDIA_TYPES: dict[str, str] = {
    ".tif": "image/tiff; application=geotiff",
    ".tiff": "image/tiff; application=geotiff",
    ".jp2": "image/jp2",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".json": "application/json",
    ".geojson": "application/geo+json",
    ".xml": "application/xml",
}
RASTER_EXTS = frozenset({".tif", ".tiff", ".jp2", ".png", ".jpg", ".jpeg"})


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


def parse_metadata(raw: dict[str, Any]) -> MetadataConfig:
    """Typed view over the association config's ``metadata`` block (§5.1)."""
    sidecar = raw.get("sidecar") or {}
    defaults = raw.get("defaults") or {}
    strategy = str(raw.get("strategy", "raster_auto"))
    if strategy not in ("raster_auto", "sidecar", "defaults_only"):
        raise ExtractError(f"unknown metadata.strategy {strategy!r}")
    return MetadataConfig(
        strategy=strategy,
        sidecar_pattern=sidecar.get("pattern"),
        sidecar_parser=str(sidecar.get("parser", "generic_xml")),
        default_datetime=defaults.get("datetime"),
    )


def media_type_for(filename: str) -> str:
    ext = posixpath.splitext(filename)[1].lower()
    return MEDIA_TYPES.get(ext, "application/octet-stream")


def is_raster(filename: str) -> bool:
    return posixpath.splitext(filename)[1].lower() in RASTER_EXTS


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
    item: dict[str, Any] = {
        "type": "Feature",
        "stac_version": STAC_VERSION,
        "stac_extensions": [],
        "id": item_id,
        "collection": collection_id,
        "geometry": parsed["geometry"],
        "properties": {**parsed["properties"], "datetime": _rfc3339(when)},
        "assets": build_assets(
            members, collection_id, item_id, asset_href_base, primary.filename
        ),
        "links": [],
    }
    if parsed["geometry"] is None:
        item.pop("bbox", None)  # keep null geometry without a bbox
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
    return item.to_dict(include_self_link=False, transform_hrefs=False)


async def build_item(
    *,
    collection_id: str,
    item_id: str,
    members: list[ExtractMember],
    metadata: dict[str, Any],
    s3_client: platform.S3Like,
    bucket: str,
    asset_href_base: str,
) -> dict[str, Any]:
    """Dispatch on metadata.strategy, reading member bytes from canonical storage
    only when the strategy needs them."""
    if not members:
        raise ExtractError("no members to itemize")
    cfg = parse_metadata(metadata)

    if cfg.strategy == "defaults_only":
        return build_defaults_only(collection_id, item_id, members, cfg, asset_href_base)

    if cfg.strategy == "sidecar":
        sidecar = _match_sidecar(members, cfg)
        data = await asyncio.to_thread(
            platform.get_object, s3_client, bucket, sidecar.canonical_key
        )
        return build_sidecar(collection_id, item_id, members, cfg, data, asset_href_base)

    # raster_auto
    primary = _primary(members)
    if not is_raster(primary.filename):
        # No raster to read — fall back to a null-geometry item.
        return build_defaults_only(collection_id, item_id, members, cfg, asset_href_base)
    data = await asyncio.to_thread(
        platform.get_object, s3_client, bucket, primary.canonical_key
    )
    return build_raster_auto(collection_id, item_id, members, cfg, data, asset_href_base)


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
