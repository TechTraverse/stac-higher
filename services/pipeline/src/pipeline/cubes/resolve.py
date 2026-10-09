"""Item → its NODD source object, through the association that produced it (spec §6.1).

The item's reference-mode source files come from the ingest ledger
(``PgProcessRepo.reference_source_files``: the catalog stores canonical hrefs,
not source ones), each with the association that produced it. Its header is
the one HDF file among them. That association, still enabled and in reference
mode, owns the file; its connection derives the key
(``connections/sources.py``) and, egress-checked, is the only way the cube
writer reads the source (ADR 0022). Another association whose connection
happens to prefix the same href (a second collection on the same bucket) is
never used.

Source errors split in two (the lead's rule on PR #101, amending plan
Decision 7). File-specific errors (missing, denied, corrupt, wrong layout, a
real egress block) are the row's outcome. Transport errors (:func:`is_transport_error`)
are the NODD or the network failing, not the file, so the job fails and
retries instead: a row failed by an outage could never be backfilled.
"""

from __future__ import annotations

import abc
import asyncio
import datetime as dt
import socket
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

import obstore
from obstore.exceptions import (
    AlreadyExistsError,
    BaseError,
    InvalidPathError,
    NotFoundError,
    NotSupportedError,
    PermissionDeniedError,
    UnauthenticatedError,
    UnknownConfigurationKeyError,
)

from pipeline.config import Settings
from pipeline.connections.egress import EgressBlocked
from pipeline.connections.sources import association_for_href
from pipeline.cubes.source import SourceConnectionError, SourceLibs, libs_from_connection
from pipeline.ingest.repo import IngestAssociation, PgIngestRepo
from pipeline.process.repo import PgProcessRepo

HDF_SUFFIXES = (".nc", ".nc4", ".h5", ".hdf5", ".he5")

#: about the file, or a deterministic configuration error: decides the chain
#: walk as "not transport" (obstore 0.11 raises the builtin FileNotFoundError
#: for a missing object; its own NotFoundError is listed defensively)
_FILE_SPECIFIC: tuple[type[BaseException], ...] = (
    FileNotFoundError,
    NotFoundError,
    PermissionDeniedError,
    UnauthenticatedError,
    InvalidPathError,
    NotSupportedError,
    AlreadyExistsError,
    UnknownConfigurationKeyError,
)
#: the source or the network failing in transit: any other obstore error
#: (GenericError: 5xx and throttling after obstore's own retries, timeouts,
#: refused or reset connections, DNS), the builtin timeout and connection
#: errors, and a DNS failure (``resolve_pinned`` wraps it in EgressBlocked)
_TRANSPORT: tuple[type[BaseException], ...] = (
    BaseError,
    TimeoutError,
    ConnectionError,
    socket.gaierror,
)


def is_transport_error(exc: BaseException | None) -> bool:
    """Whether a source read failed in transit rather than on the file (the
    lead's rule on PR #101, amending plan Decision 7): the job then fails and
    retries, instead of failing the row for good.

    Walks the whole ``__cause__``/``__context__`` chain (h5py and
    obspec-utils may wrap the obstore error); the outermost decisive link
    wins, so a not-found or a denial is about the file even if a transport
    error sits under it. A chain with neither is file-specific (a corrupt
    header, a layout error, a genuine egress block)."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, _FILE_SPECIFIC):
            return False
        if isinstance(exc, _TRANSPORT):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


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
        """The object's LastModified (one HEAD). Raises ``FileNotFoundError``
        for a missing object, and obstore's errors otherwise."""


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
    #: (collection_id, item_id) -> filename -> (source_href, association_id)
    files: Callable[[str, str], Awaitable[dict[str, tuple[str, str]]]]
    associations: Callable[[], Awaitable[list[IngestAssociation]]]
    _assocs: list[IngestAssociation] | None = None
    _libs: dict[str, SourceLibs] = field(default_factory=dict)
    #: connection id -> why its libraries could not be built (one DNS
    #: lookup per connection per job, not one per row, during an outage)
    _refused: dict[str, SourceUnavailable] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, settings: Settings, master_key: bytes) -> PgSourceResolver:
        return cls(
            settings,
            master_key,
            PgProcessRepo(settings.database_url).reference_source_files,
            PgIngestRepo(settings.database_url).list_enabled_ingest_associations,
        )

    async def resolve(self, source_collection_id: str, item_id: str) -> ResolvedSource:
        files = await self.files(source_collection_id, item_id)
        href = pick_hdf_href({name: href for name, (href, _) in files.items()})
        producer = next(assoc_id for h, assoc_id in files.values() if h == href)
        if self._assocs is None:
            self._assocs = await self.associations()
        allow = self.settings.egress_allow_hosts
        # Only the producing association, and only while it is enabled and in
        # reference mode (association_for_href passes over any other mode).
        owner = [a for a in self._assocs if a.id == producer]
        match = association_for_href(href, owner, self.master_key, allow)
        if match is None:
            raise SourceUnavailable("skipped", "no_source_connection")
        connection = match.association.connection
        refused = self._refused.get(connection.id)
        if refused is not None:
            raise SourceUnavailable(refused.status, refused.reason) from refused.__cause__
        libs = self._libs.get(connection.id)
        if libs is None:
            try:
                # resolve_pinned does DNS: keep it off the event loop.
                libs = await asyncio.to_thread(libs_from_connection, connection, allow)
            except (EgressBlocked, SourceConnectionError) as exc:
                # A DNS failure keeps its gaierror cause: is_transport_error
                # then retries the job instead of failing the row.
                unavailable = SourceUnavailable("failed", f"{type(exc).__name__}: {exc}")
                self._refused[connection.id] = unavailable
                raise unavailable from exc
            self._libs[connection.id] = libs
        return ResolvedSource(libs, match.key)

    async def last_modified(self, source: ResolvedSource) -> dt.datetime:
        meta = await obstore.head_async(source.libs.store, source.key)
        return meta["last_modified"]
