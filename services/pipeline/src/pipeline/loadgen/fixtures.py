"""The rows and documents the harness writes to stand a flow up.

The harness inserts `connections` / `collection_connections` directly, going
around the app's Zod write gate — there is no app process in the compose
stack, and a load run must not depend on one. The cost of that shortcut is
that a drifted config reads as "zero throughput" rather than "bad config", so
the configs are built here, in one place, and a test round-trips them through
the pipeline's own `parse_ingest_config`.
"""

from __future__ import annotations

from typing import Any

from pipeline.ingest.config import STORAGE_MODES

#: Every row the harness creates carries this group, so teardown and the
#: monitoring UI can both find the load run's leavings.
LOAD_GROUP = "earth-observation"
LOAD_CREATED_BY = "m3-loadgen"

#: A one-minute poll, not the §5.1 default of 300s: at five minutes the report
#: would be measuring the scheduler's cadence rather than the pipeline's
#: throughput. One minute is the floor Procrastinate's periodic scheduler can
#: express, and the feed runs long enough to cross several ticks.
LOAD_POLL_SECONDS = 60


#: EXTRACT strategies the harness offers, and what each measures.
METADATA_STRATEGIES = ("defaults_only", "raster_auto")


def metadata_config(strategy: str = "defaults_only") -> dict[str, Any]:
    """The §5.1 `metadata` block for a load profile.

    `defaults_only` needs BOTH defaults set: without a datetime EXTRACT raises
    "no datetime could be resolved", and without a geometry every item gets the
    global fallback footprint. Either way the run would be measuring an error
    path rather than throughput — the mistake the first S-A run actually made.
    """
    if strategy not in METADATA_STRATEGIES:
        raise ValueError(
            f"strategy must be one of {METADATA_STRATEGIES}, got {strategy!r}"
        )
    if strategy == "raster_auto":
        # rio-stac reads the datetime and geometry off the raster itself; the
        # datetime default is the documented fallback for a tag-less file.
        return {"strategy": "raster_auto", "defaults": {"datetime": "file_mtime"}}
    return {
        "strategy": "defaults_only",
        "defaults": {"datetime": "file_mtime", "geometry": "collection"},
    }


def ingest_config(
    source_path: str,
    storage_mode: str = "copy",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The §5.1 ingest config for a load association."""
    if storage_mode not in STORAGE_MODES:
        raise ValueError(f"storage_mode must be one of {STORAGE_MODES}, got {storage_mode!r}")
    return {
        "source_path": source_path,
        "include": ["**/*.tif"],
        "exclude": [],
        "poll_frequency_seconds": LOAD_POLL_SECONDS,
        "storage_mode": storage_mode,
        # One file, one item — the property the offered-rate arithmetic rests
        # on. Grouping would make files/s and items/s different numbers.
        "grouping": {"rule": "none", "timeout_seconds": 900, "on_timeout": "ingest_partial"},
        "post_ingest": "leave",
        # Default to the cheap profile: a baseline measures the pipeline
        # first, and GDAL as a deliberate second run.
        "metadata": metadata if metadata is not None else metadata_config(),
    }


def deliver_config(path_template: str) -> dict[str, Any]:
    """The §5.1 delivery config for the fan-out leg of a load run."""
    return {
        "path_template": path_template,
        "item_filter": None,
        "asset_keys": None,
        "on_update": "redeliver",
        "overwrite": "if_newer",
        "max_concurrent_transfers": 4,
        "payload": {"item_json": False, "checksums": None, "completion_marker": False},
        "retry": {"max_attempts": 5, "backoff": "exponential"},
    }


def s3_connection_config(bucket: str, endpoint: str) -> dict[str, Any]:
    return {
        "bucket": bucket,
        "region": "us-east-1",
        "endpoint": endpoint,
        "force_path_style": True,
    }


def collection_document(collection_id: str) -> dict[str, Any]:
    """A minimal valid STAC collection for pgstac to accept."""
    return {
        "type": "Collection",
        "stac_version": "1.0.0",
        "id": collection_id,
        "description": "M3 load harness synthetic collection",
        "license": "proprietary",
        "extent": {
            "spatial": {"bbox": [[-180.0, -90.0, 180.0, 90.0]]},
            "temporal": {"interval": [["2020-01-01T00:00:00Z", None]]},
        },
        "links": [],
    }
