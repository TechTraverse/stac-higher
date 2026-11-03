"""The registry of servable cubes (virtual cube spec §8.2).

Every poll reads the enabled sinks and, per sink, ``main``'s snapshot. A cube
is (re)opened only when its key — snapshot, variables, source prefixes,
append dimension — changed; an unchanged sink costs one ``lookup_branch``.

Failures stay local (plan Decision 9): a DB error keeps every open dataset, a
sink's error keeps that sink's previous dataset. The server never takes a
store location from a request: repositories are found from
``cube_sinks.cube_collection_id`` alone.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import icechunk as ic
import xarray as xr

from stac_higher_cube_server.datasets import prepare_dataset
from stac_higher_cube_server.settings import Settings
from stac_higher_cube_server.sinks import SinkRow

log = logging.getLogger(__name__)

BRANCH = "main"
#: The pipeline's layout (pipeline/cubes/icerepo.py::cube_prefix, ADR 0022)
CUBE_ITEM_ID = "_cube"


class UnsupportedSourceError(ValueError):
    """A source prefix the server cannot authorize (v1: anonymous ``s3://`` only)."""


class SinkSource(Protocol):
    def enabled_sinks(self) -> list[SinkRow]: ...


class RepoOpener(Protocol):
    def open(self, sink: SinkRow) -> ic.Repository | None: ...


def check_source_prefixes(prefixes: tuple[str, ...]) -> None:
    for prefix in prefixes:
        if not prefix.startswith("s3://"):
            raise UnsupportedSourceError(f"unsupported source prefix {prefix!r}")


class S3RepoOpener:
    """The platform bucket, read-only, plus anonymous access to the sources.

    Opened exactly like the writer (``pipeline/cubes/icerepo.py::cube_storage``):
    path-style, explicit endpoint. One ``Repository`` handle is cached per
    collection and replaced when its prefixes change; a cached handle sees new
    commits on ``lookup_branch``.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache: dict[str, tuple[tuple[str, ...], ic.Repository]] = {}

    def _storage(self, cube_collection_id: str) -> ic.Storage:
        s = self._settings
        return ic.s3_storage(
            bucket=s.platform_bucket,
            prefix=f"assets/{cube_collection_id}/{CUBE_ITEM_ID}",
            region=s.s3_region,
            endpoint_url=s.s3_endpoint,
            allow_http=s.s3_endpoint.startswith("http://"),
            force_path_style=True,
            access_key_id=s.s3_access_key_id,
            secret_access_key=s.s3_secret_access_key,
        )

    def open(self, sink: SinkRow) -> ic.Repository | None:
        check_source_prefixes(sink.source_prefixes)
        cached = self._cache.get(sink.cube_collection_id)
        if cached is not None and cached[0] == sink.source_prefixes:
            return cached[1]
        storage = self._storage(sink.cube_collection_id)
        if not ic.Repository.exists(storage):
            return None
        credentials = ic.containers_credentials(
            {p: ic.s3_credentials(anonymous=True) for p in sink.source_prefixes}
        )
        repo = ic.Repository.open(storage, authorize_virtual_chunk_access=credentials)
        self._cache[sink.cube_collection_id] = (sink.source_prefixes, repo)
        return repo


@dataclass(frozen=True)
class _Entry:
    key: tuple
    snapshot_id: str
    dataset: xr.Dataset
    steps: int
    opened_at: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _describe(err: BaseException) -> str:
    return f"{type(err).__name__}: {err}"


class CubeRegistry:
    def __init__(self, source: SinkSource, opener: RepoOpener) -> None:
        self._source = source
        self._opener = opener
        self._entries: dict[str, _Entry] = {}
        self._pending: list[str] = []
        self._empty: list[str] = []
        self._errors: dict[str, str] = {}
        self._last_reload_at: str | None = None
        self._last_reload_error: str | None = None
        # refresh() runs in a worker thread; readers only do dict lookups
        self._lock = threading.Lock()

    def get(self, collection_id: str) -> xr.Dataset | None:
        entry = self._entries.get(collection_id)
        return entry.dataset if entry is not None else None

    def ids(self) -> list[str]:
        return sorted(self._entries)

    def health(self) -> dict:
        entries = dict(self._entries)
        return {
            "datasets": {
                cid: {"snapshot_id": e.snapshot_id, "steps": e.steps, "opened_at": e.opened_at}
                for cid, e in sorted(entries.items())
            },
            "pending": list(self._pending),
            "empty": list(self._empty),
            "errors": dict(self._errors),
            "last_reload_at": self._last_reload_at,
            "last_reload_error": self._last_reload_error,
        }

    def refresh(self) -> list[str]:
        """One poll; returns the collection ids (re)opened, for the warm-up."""
        with self._lock:
            return self._refresh()

    def _refresh(self) -> list[str]:
        self._last_reload_at = _now()
        try:
            sinks = self._source.enabled_sinks()
        except Exception as err:  # keep serving what is open (Decision 9)
            self._last_reload_error = _describe(err)
            log.warning("cube sinks unreadable; keeping open datasets",
                        extra={"error": self._last_reload_error})
            return []
        self._last_reload_error = None

        opened: list[str] = []
        pending: list[str] = []
        empty: list[str] = []
        errors: dict[str, str] = {}
        for sink in sinks:
            cid = sink.cube_collection_id
            try:
                status = self._refresh_sink(sink)
            except Exception as err:
                errors[cid] = _describe(err)
                log.warning("cube not reloaded; keeping its previous dataset",
                            extra={"cube_collection_id": cid, "error": errors[cid]})
                continue
            if status == "opened":
                opened.append(cid)
            elif status == "pending":
                pending.append(cid)
            elif status == "empty":
                empty.append(cid)

        wanted = {s.cube_collection_id for s in sinks}
        for cid in [c for c in self._entries if c not in wanted]:
            del self._entries[cid]
            log.info("cube unregistered", extra={"cube_collection_id": cid})
        self._pending, self._empty, self._errors = pending, empty, errors
        return opened

    def _refresh_sink(self, sink: SinkRow) -> str:
        cid = sink.cube_collection_id
        repo = self._opener.open(sink)
        if repo is None:
            self._entries.pop(cid, None)
            return "pending"
        snapshot_id = repo.lookup_branch(BRANCH)
        key = (snapshot_id, sink.variables, sink.source_prefixes, sink.append_dim)
        current = self._entries.get(cid)
        if current is not None and current.key == key:
            return "unchanged"
        session = repo.readonly_session(snapshot_id=snapshot_id)
        raw = xr.open_zarr(
            session.store, zarr_format=3, consolidated=False, chunks=None, decode_coords="all"
        )
        steps = int(raw.sizes.get(sink.append_dim, 0))
        if steps == 0:
            self._entries.pop(cid, None)
            return "empty"
        dataset = prepare_dataset(
            raw,
            variables=sink.variables,
            append_dim=sink.append_dim,
            collection_id=cid,
            snapshot_id=snapshot_id,
        )
        self._entries[cid] = _Entry(key, snapshot_id, dataset, steps, _now())
        log.info("cube opened",
                 extra={"cube_collection_id": cid, "snapshot_id": snapshot_id, "steps": steps})
        return "opened"
