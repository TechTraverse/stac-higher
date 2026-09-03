"""DISCOVER stage: adapter.list → diff vs ledger → settled check (ROADMAP §6.1).

Lists the association's ``source_path`` once, filters by the include/exclude
globs, computes a change fingerprint per file, and drives the ``ingest_files``
ledger state machine. The **settled check** requires a file to be unchanged
(same size/fingerprint) across two polls before it is eligible — FTP/SFTP
sources are frequently mid-upload — so a file goes ``seen`` on first sight and
``settled`` only when the next poll finds it unchanged. GROUP consumes the
``settled`` rows.

Path normalization (the reason this stage is the adapter layer's first real
consumer, ISSUES I-4): ``StorageAdapter.list`` is not uniform across protocols —
S3 returns full keys, SFTP/FTP return names relative to the listed prefix. We
normalize every entry to a **source-relative** path for the ledger + globs, and
reconstruct the adapter-consumable fetch path as ``source_path`` joined with it.
Stripping a prefix that isn't there is a no-op, so the same code handles both
conventions. Discovery is **non-recursive** this slice (one ``list`` of
``source_path``); S3's prefix listing is naturally deep, SFTP/FTP see one level —
a recursive walk is a follow-up (ISSUES).

The date window (W-1, spec §3) bounds what a tick takes in, in three places:
with a ``path_template`` the stage lists only the prefixes the window touches
(bounding the LISTING, not just its result); every entry's modified time is
then gated by the window; and ``max_files_per_poll`` caps how many NEW files
one tick admits, oldest first. All three are optional and absent by default.
"""

from __future__ import annotations

import datetime as dt
import logging
import posixpath
from dataclasses import dataclass

from pipeline.config import DEFAULT_INGEST_MAX_WINDOW_PREFIXES
from pipeline.connections.adapters.base import FileEntry, StorageAdapter
from pipeline.ingest.config import IngestConfig, effective_settle, path_matches
from pipeline.ingest.repo import (
    STATUS_FAILED,
    STATUS_FETCHING,
    STATUS_ITEMIZED,
    STATUS_SEEN,
    STATUS_SETTLED,
    STATUS_STORED,
    IngestAssociation,
    IngestRepo,
    LedgerEntry,
)
from pipeline.ingest.window import expand_prefixes, in_window, resolve_window

logger = logging.getLogger(__name__)


def relative_source_path(entry_path: str, source_path: str) -> str:
    """Normalize a listing entry to a path relative to ``source_path``.

    Idempotent whether the adapter returned a full key (S3, includes the prefix)
    or a bare name (SFTP/FTP, relative already): a prefix that isn't present is
    simply not stripped.
    """
    ep = entry_path.lstrip("/")
    src = source_path.strip("/")
    if src and (ep == src or ep.startswith(src + "/")):
        return ep[len(src) :].lstrip("/")
    return ep


def source_fetch_path(source_path: str, relpath: str) -> str:
    """The path to hand ``adapter.get`` for a source-relative ``relpath``.

    Equals the original S3 key (``get`` wants the full key) and the
    root-relative SFTP/FTP path (``get`` re-resolves it under ``root_path``).
    """
    return posixpath.join(source_path, relpath) if source_path else relpath


def fingerprint_of(entry: FileEntry) -> str | None:
    """A change signal for the settled check: prefer the etag, else size+mtime.

    ``None`` when the protocol exposed neither a size nor an etag — such a file
    can't be settle-checked and is skipped (logged) rather than ingested blind.
    """
    if entry.etag:
        return entry.etag
    if entry.size is not None and entry.mtime is not None:
        return f"{entry.size}:{int(entry.mtime)}"
    if entry.size is not None:
        return f"{entry.size}:"
    return None


@dataclass
class DiscoverResult:
    """Per-run counters (for logging + tests)."""

    listed: int = 0
    skipped: int = 0
    unfingerprinted: int = 0
    new_seen: int = 0
    settled: int = 0
    #: total size of the files settled this run — the flow_stats bytes feed (M2-A).
    settled_bytes: int = 0
    changed_while_seen: int = 0
    unsettled: int = 0
    unchanged: int = 0
    reingest: int = 0
    #: W-1 counters.
    out_of_window: int = 0
    #: listed, in-scope, but carrying no modified time to gate on.
    undateable: int = 0
    #: matched everything but was held back by max_files_per_poll.
    deferred_by_cap: int = 0
    prefixes_listed: int = 0


@dataclass(frozen=True)
class _Candidate:
    """A listed file that passed every filter and is ready to reconcile."""

    relpath: str
    entry: FileEntry
    fingerprint: str
    latest: LedgerEntry | None


async def discover_stage(
    repo: IngestRepo,
    association: IngestAssociation,
    config: IngestConfig,
    adapter: StorageAdapter,
    *,
    now: dt.datetime | None = None,
    max_prefixes: int = DEFAULT_INGEST_MAX_WINDOW_PREFIXES,
) -> DiscoverResult:
    """Reconcile the live listing against the ledger. Idempotent per file."""
    at = now or dt.datetime.now(dt.UTC)
    result = DiscoverResult()
    window = resolve_window(config.window_begin, config.window_end, at)

    # W-1: with a template, list only the prefixes the window touches; the
    # window otherwise filters AFTER a full listing, which bounds the result
    # but not the cost of getting it.
    if config.path_template and window is not None:
        prefixes = [
            f"{config.source_path}{suffix}"
            for suffix in expand_prefixes(config.path_template, window, max_prefixes=max_prefixes)
        ]
    else:
        prefixes = [config.source_path]

    entries: list[FileEntry] = []
    for prefix in prefixes:
        entries.extend(await adapter.list(prefix))
    result.prefixes_listed = len(prefixes)

    # G-3: whether a first sighting is already settled, decided once per tick
    # from the config and the source protocol.
    immediate = effective_settle(config, adapter.protocol) == "immediate"
    candidates: list[_Candidate] = []
    for entry in entries:
        result.listed += 1
        if entry.is_dir:
            continue
        relpath = relative_source_path(entry.path, config.source_path)
        if not relpath:
            continue
        if not path_matches(relpath, config.include, config.exclude):
            result.skipped += 1
            continue
        if window is not None:
            if entry.mtime is None:
                # Cannot be gated, so it is not admitted — the same posture the
                # stage takes for an unfingerprintable file. Counted and logged
                # so "my FTP source ingests nothing" is diagnosable.
                result.undateable += 1
                logger.warning(
                    "ingest discover: no modified time, cannot apply the window",
                    extra={"association_id": association.id, "source_path": relpath},
                )
                continue
            if not in_window(window, entry.mtime):
                result.out_of_window += 1
                continue
        fingerprint = fingerprint_of(entry)
        if fingerprint is None:
            result.unfingerprinted += 1
            logger.warning(
                "ingest discover: file has no size/etag, cannot settle",
                extra={"association_id": association.id, "source_path": relpath},
            )
            continue
        latest = await repo.get_latest_ledger(association.id, relpath)
        candidates.append(_Candidate(relpath, entry, fingerprint, latest))

    # W-1: oldest first, so a backfill advances forward in time and the ledger
    # shows it progressing. Only NEW files count against the cap — a steady
    # state of known files must not starve new arrivals.
    candidates.sort(key=lambda c: (c.entry.mtime is None, c.entry.mtime or 0.0))
    admitted = 0
    for candidate in candidates:
        is_new = candidate.latest is None
        if (
            config.max_files_per_poll is not None
            and is_new
            and admitted >= config.max_files_per_poll
        ):
            result.deferred_by_cap += 1
            continue
        if is_new:
            admitted += 1
        await _reconcile(
            repo,
            association.id,
            candidate.relpath,
            candidate.entry,
            candidate.fingerprint,
            candidate.latest,
            result,
            immediate,
        )
    logger.info(
        "ingest discover tick",
        extra={
            "association_id": association.id,
            "collection_id": association.collection_id,
            "prefixes_listed": result.prefixes_listed,
            "listed": result.listed,
            "out_of_window": result.out_of_window,
            "undateable": result.undateable,
            "deferred_by_cap": result.deferred_by_cap,
            "new_seen": result.new_seen,
            "settled": result.settled,
            "reingest": result.reingest,
        },
    )
    return result


async def _reconcile(
    repo: IngestRepo,
    association_id: str,
    relpath: str,
    entry: FileEntry,
    fingerprint: str,
    latest: LedgerEntry | None,
    result: DiscoverResult,
    immediate: bool,
) -> None:
    if latest is None:
        # `immediate` (G-3): an S3 object is whole or absent, so the first
        # sight is already the settled state. The settled→seen guard below
        # still catches a key that is overwritten before FETCH.
        await repo.insert_ledger_version(
            association_id,
            relpath,
            version=1,
            status=STATUS_SETTLED if immediate else STATUS_SEEN,
            size=entry.size,
            fingerprint=fingerprint,
        )
        if immediate:
            result.settled += 1
            result.settled_bytes += entry.size or 0
        else:
            result.new_seen += 1
        return

    if latest.status == STATUS_SEEN:
        if latest.fingerprint == fingerprint:
            # unchanged across two polls → eligible.
            await repo.set_ledger_fields(latest.id, status=STATUS_SETTLED, size=entry.size)
            result.settled += 1
            result.settled_bytes += entry.size or 0
        else:
            # still changing (mid-upload) → record the new state, restart window.
            await repo.set_ledger_fields(latest.id, size=entry.size, fingerprint=fingerprint)
            result.changed_while_seen += 1
        return

    if latest.status == STATUS_SETTLED:
        if latest.fingerprint != fingerprint:
            # changed after settling but before fetch — it wasn't settled.
            await repo.set_ledger_fields(
                latest.id, status=STATUS_SEEN, size=entry.size, fingerprint=fingerprint
            )
            result.unsettled += 1
        return

    if latest.status in (STATUS_FETCHING, STATUS_STORED):
        # mid-pipeline; a change is reconciled after itemization completes.
        return

    if latest.status == STATUS_ITEMIZED:
        if latest.fingerprint == fingerprint:
            result.unchanged += 1
        else:
            # re-ingest: a new version of the same product (same item_id).
            await repo.insert_ledger_version(
                association_id,
                relpath,
                version=latest.version + 1,
                status=STATUS_SEEN,
                size=entry.size,
                fingerprint=fingerprint,
                item_id=latest.item_id,
            )
            result.reingest += 1
        return

    if latest.status == STATUS_FAILED:
        # a failed file is retried only when its bytes change (else Phase 6 GC).
        if latest.fingerprint != fingerprint:
            await repo.insert_ledger_version(
                association_id,
                relpath,
                version=latest.version + 1,
                status=STATUS_SEEN,
                size=entry.size,
                fingerprint=fingerprint,
            )
            result.reingest += 1
        return
