"""Item → its NODD source object, through the association that produced it (spec §6.1).

The item's reference-mode source hrefs come from the ingest ledger
(``PgProcessRepo.reference_source_hrefs``: the catalog stores canonical hrefs,
not source ones). Its header is the one HDF file among them. The
reference-mode association whose connection prefixes that href owns it
(``connections/sources.py``), and that connection, egress-checked, is the
only way the cube writer reads the source (ADR 0022).
"""

from __future__ import annotations

import abc
import asyncio
import datetime as dt
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

import obstore

from pipeline.config import Settings
from pipeline.connections.egress import EgressBlocked
from pipeline.connections.sources import association_for_href
from pipeline.cubes.source import SourceConnectionError, SourceLibs, libs_from_connection
from pipeline.ingest.repo import IngestAssociation, PgIngestRepo
from pipeline.process.repo import PgProcessRepo

HDF_SUFFIXES = (".nc", ".nc4", ".h5", ".hdf5", ".he5")


class SourceUnavailable(Exception):
    """The row cannot be appended: its ledger ``status`` and ``reason``."""

    def __init__(self, status: str, reason: str) -> None:
        super().__init__(f"{status}: {reason}")
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class ResolvedSource:
    libs: SourceLibs
    key: str

    @property
    def url(self) -> str:
        return self.libs.url(self.key)


class SourceResolver(abc.ABC):
    @abc.abstractmethod
    async def resolve(self, source_collection_id: str, item_id: str) -> ResolvedSource:
        """Raises :class:`SourceUnavailable`."""

    @abc.abstractmethod
    async def last_modified(self, source: ResolvedSource) -> dt.datetime:
        """The object's LastModified (one HEAD). Raises ``FileNotFoundError``."""


def pick_hdf_href(hrefs: Mapping[str, str]) -> str:
    """filename -> href: the item's one HDF source file."""
    if not hrefs:
        raise SourceUnavailable("skipped", "source_missing")
    hdf = [href for name, href in sorted(hrefs.items()) if name.lower().endswith(HDF_SUFFIXES)]
    if len(hdf) != 1:
        raise SourceUnavailable("skipped", "unsupported_layout")
    return hdf[0]


@dataclass
class PgSourceResolver(SourceResolver):
    """One per job: associations are listed once, and each connection's
    libraries are built once."""

    settings: Settings
    master_key: bytes
    hrefs: Callable[[str, str], Awaitable[dict[str, str]]]
    associations: Callable[[], Awaitable[list[IngestAssociation]]]
    _assocs: list[IngestAssociation] | None = None
    _libs: dict[str, SourceLibs] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, settings: Settings, master_key: bytes) -> PgSourceResolver:
        return cls(
            settings,
            master_key,
            PgProcessRepo(settings.database_url).reference_source_hrefs,
            PgIngestRepo(settings.database_url).list_enabled_ingest_associations,
        )

    async def resolve(self, source_collection_id: str, item_id: str) -> ResolvedSource:
        href = pick_hdf_href(await self.hrefs(source_collection_id, item_id))
        if self._assocs is None:
            self._assocs = await self.associations()
        allow = self.settings.egress_allow_hosts
        match = association_for_href(href, self._assocs, self.master_key, allow)
        if match is None:
            raise SourceUnavailable("skipped", "no_source_connection")
        connection = match.association.connection
        libs = self._libs.get(connection.id)
        if libs is None:
            try:
                # resolve_pinned does DNS: keep it off the event loop.
                libs = await asyncio.to_thread(libs_from_connection, connection, allow)
            except (EgressBlocked, SourceConnectionError) as exc:
                raise SourceUnavailable("failed", f"{type(exc).__name__}: {exc}") from exc
            self._libs[connection.id] = libs
        return ResolvedSource(libs, match.key)

    async def last_modified(self, source: ResolvedSource) -> dt.datetime:
        meta = await obstore.head_async(source.libs.store, source.key)
        return meta["last_modified"]
