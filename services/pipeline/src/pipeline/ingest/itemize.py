"""ITEMIZE stage: validate + upsert a group's STAC item, then post-ingest (§6.1).

Orchestrates the chain tail against seams (repo, pgstac writer, adapter, S3
client) so it is fully unit-testable. Re-reads each source file's latest ledger
row and acts only on `stored` members (idempotent, restart-safe): a crash mid-run
leaves them `stored` for the re-enqueued job to re-upsert (upsert is idempotent).
EXTRACT failure or a validation failure marks the members `failed` (no bad item
reaches the catalog); a missing collection is a permanent `failed`. On success
the members go `itemized` and post-ingest cleans the source.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pipeline.connections.adapters.base import StorageAdapter
from pipeline.ingest.config import IngestConfig
from pipeline.ingest.extract import ExtractError, ExtractMember, build_item
from pipeline.ingest.postingest import apply_post_ingest
from pipeline.ingest.repo import (
    STATUS_FAILED,
    STATUS_ITEMIZED,
    STATUS_STORED,
    IngestAssociation,
    IngestRepo,
    LedgerEntry,
)
from pipeline.stac.pgstac_writer import CollectionMissing, PgstacWriter
from pipeline.storage import platform
from pipeline.storage.keys import canonical_asset_key

logger = logging.getLogger(__name__)


class ItemValidationError(Exception):
    """The built item fails stac-pydantic validation."""


@dataclass
class ItemizeOutcome:
    status: str  # "itemized" | "failed" | "skipped"
    item_id: str
    detail: str = ""


def validate_item(item_dict: Mapping[str, Any]) -> None:
    """stac-pydantic gate (offline, core-structural). Raises on invalid.

    Uses the core ``stac_pydantic.Item`` (not ``stac_pydantic.api.Item``): the
    API variant additionally requires a ``root`` link, which EXTRACT-built
    items never carry (they are plain catalog items, not API page entries).
    """
    from pydantic import ValidationError
    from stac_pydantic import Item

    try:
        Item.model_validate(dict(item_dict))
    except ValidationError as exc:
        raise ItemValidationError(str(exc)) from exc


def _member(entry: LedgerEntry, collection_id: str, item_id: str) -> ExtractMember:
    filename = entry.source_path.rsplit("/", 1)[-1]
    return ExtractMember(
        source_path=entry.source_path,
        filename=filename,
        canonical_key=canonical_asset_key(collection_id, item_id, filename),
        observed_at=entry.updated_at,
    )


async def _mark(
    repo: IngestRepo, entries: list[LedgerEntry], status: str, item_id: str | None
) -> None:
    # One statement for all members (all-or-nothing): a crash mid-mark must
    # never leave the group split across statuses, which would let a retry
    # rebuild the item from a subset of members (§ final-review fix).
    if not entries:
        return
    await repo.set_ledger_status_many([e.id for e in entries], status=status, item_id=item_id)


async def run_itemize(
    repo: IngestRepo,
    writer: PgstacWriter,
    adapter: StorageAdapter,
    s3_client: platform.S3Like,
    *,
    association: IngestAssociation,
    config: IngestConfig,
    item_id: str,
    source_paths: Sequence[str],
    bucket: str,
    asset_href_base: str,
) -> ItemizeOutcome:
    # Re-read: act only on members still `stored` (idempotent guard).
    stored: list[LedgerEntry] = []
    for sp in source_paths:
        row = await repo.get_latest_ledger(association.id, sp)
        if row is not None and row.status == STATUS_STORED:
            stored.append(row)
    if not stored:
        return ItemizeOutcome("skipped", item_id, "no stored members")

    members = [_member(e, association.collection_id, item_id) for e in stored]

    # EXTRACT
    try:
        item_dict = await build_item(
            collection_id=association.collection_id,
            item_id=item_id,
            members=members,
            metadata=config.metadata,
            s3_client=s3_client,
            bucket=bucket,
            asset_href_base=asset_href_base,
        )
    except ExtractError as exc:
        await _mark(repo, stored, STATUS_FAILED, None)
        logger.warning("itemize extract failed", extra={"item_id": item_id, "error": str(exc)})
        return ItemizeOutcome("failed", item_id, f"extract: {exc}")

    # VALIDATE
    try:
        validate_item(item_dict)
    except ItemValidationError as exc:
        await _mark(repo, stored, STATUS_FAILED, None)
        logger.warning("itemize validation failed", extra={"item_id": item_id, "error": str(exc)})
        return ItemizeOutcome("failed", item_id, f"validation: {exc}")

    # UPSERT
    try:
        await writer.upsert_items([item_dict])
    except CollectionMissing as exc:
        await _mark(repo, stored, STATUS_FAILED, None)
        logger.error("itemize upsert failed: collection missing", extra={"item_id": item_id})
        return ItemizeOutcome("failed", item_id, f"collection missing: {exc}")
    # Any other exception propagates → the job retries (transient DB errors).

    await _mark(repo, stored, STATUS_ITEMIZED, item_id)

    # post-ingest (non-fatal)
    await apply_post_ingest(adapter, config, source_paths=[e.source_path for e in stored])

    logger.info("itemize done", extra={"item_id": item_id, "members": len(stored)})
    return ItemizeOutcome("itemized", item_id)
