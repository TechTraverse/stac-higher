"""Finalize's EXTRACT branch (GOES spec §6.2 step 3, §15).

An extractor run does not publish: it hands each fixed-up item back to the
ingest chain, which validates and writes it exactly as it would have without
the extractor — `complete_item` is the one implementation of that tail. So
this module is deliberately NOT a third ADR 0014 producer: the neutral steps
move assets and rewrite hrefs, and an extractor output has neither (its
assets are the ingest's own, already canonical or reference-style).

What it enforces (§6.1): the output document keeps the draft's id, collection
and asset set, and every asset href byte-for-byte; geometry and datetime
must be set (pgstac refuses a null geometry, and validation needs the
datetime). Anything else — properties, bbox, asset metadata — is the
extractor's to change.

Failure is per item (its ledger rows fail with the reason) and the run is
downgraded to `dead` only when NOTHING landed, mirroring the transform
recorder so "succeeded" cannot mean "published nothing".
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pipeline import metrics
from pipeline.connections.adapters.base import StorageAdapter
from pipeline.finalize.store import ObjectStore
from pipeline.ingest.config import parse_ingest_config
from pipeline.ingest.itemize import complete_item
from pipeline.ingest.repo import (
    STATUS_EXTRACTING,
    STATUS_FAILED,
    IngestAssociation,
    IngestRepo,
    LedgerEntry,
)
from pipeline.process.repo import ProcessRepo
from pipeline.stac.pgstac_writer import PgstacWriter
from pipeline.storage.keys import INPUTS_SEGMENT, run_staging_prefix

logger = logging.getLogger(__name__)

ITEM_DOCUMENT_SUFFIX = ".json"

#: Migration 027 declares `process_runs.association_id … ON DELETE SET NULL`,
#: so a run can outlive the association that queued it. Its rows still have to
#: reach a terminal state here rather than waiting for the stall sweep.
ASSOCIATION_VANISHED = "ingest association was deleted during extraction"
ASSOCIATION_UNUSABLE = "ingest association was disabled or deleted during extraction"


@dataclass(frozen=True)
class ExtractFinalizeResult:
    itemized: int = 0
    failed: int = 0
    skipped: int = 0


def check_extract_output(draft: dict[str, Any], document: dict[str, Any]) -> str | None:
    """The §6.1 immutability rules, as the reason text or None when clean."""
    if document.get("id") != draft.get("id"):
        return (
            f"extractor changed the item id "
            f"({draft.get('id')!r} -> {document.get('id')!r})"
        )
    if document.get("collection") != draft.get("collection"):
        return "extractor changed the collection"
    draft_raw = draft.get("assets")
    draft_assets: dict[str, Any] = draft_raw if isinstance(draft_raw, dict) else {}
    out_assets = document.get("assets")
    # Shape first, always as a REASON: an extractor is arbitrary operator code,
    # so a document that parses as JSON can still be any shape. Raising here
    # would escape the caller's per-item loop and abandon the whole run's rows
    # in `extracting` — exactly what this branch exists to prevent.
    if not isinstance(out_assets, dict):
        return "extractor output assets is not an object"
    if set(out_assets) != set(draft_assets):
        return "extractor changed the asset set (assets may gain metadata, not members)"
    for key, entry in draft_assets.items():
        out_entry = out_assets.get(key)
        if not isinstance(out_entry, dict):
            return f"extractor output asset {key!r} is not an object"
        draft_href = entry.get("href") if isinstance(entry, dict) else None
        if out_entry.get("href") != draft_href:
            return f"extractor changed the href of asset {key!r}"
    if document.get("geometry") is None:
        return "extractor left geometry null"
    properties = document.get("properties")
    if not isinstance(properties, dict):
        return "extractor output properties is not an object"
    if not properties.get("datetime"):
        return "extractor left properties.datetime unset"
    return None


async def finalize_extract_run(
    run_id: str,
    *,
    process_repo: ProcessRepo,
    ingest_repo: IngestRepo,
    writer: PgstacWriter,
    store: ObjectStore,
    adapter_for: Callable[[IngestAssociation], StorageAdapter],
    now: dt.datetime | None = None,
) -> ExtractFinalizeResult:
    run = await process_repo.get_run(run_id)
    if run is None:
        logger.warning("extract finalize: no such extractor run", extra={"run_id": run_id})
        return ExtractFinalizeResult()

    association_id = run.association_id
    prefix = run_staging_prefix(run_id)
    keys = await asyncio.to_thread(store.list_keys, prefix)
    inputs_prefix = f"{prefix}{INPUTS_SEGMENT}/"
    outputs = {
        k
        for k in keys
        if not k.startswith(inputs_prefix) and k.endswith(ITEM_DOCUMENT_SUFFIX)
    }

    itemized = failed = skipped = 0

    async def fail_rows(rows: list[LedgerEntry], reason: str) -> None:
        nonlocal failed
        await ingest_repo.set_ledger_status_many(
            [r.id for r in rows], status=STATUS_FAILED, item_id=None, reason=reason[:500]
        )
        # Nothing to bump when the association itself is gone — the rollup
        # lives on the association row.
        if association_id is not None:
            await ingest_repo.bump_flow_stats(association_id, failed=1)
        metrics.INGEST_EVENTS.labels(stage="failed").inc()
        failed += 1

    association = (
        await ingest_repo.get_association(association_id)
        if association_id is not None
        else None
    )
    config = parse_ingest_config(association.config) if association else None
    adapter = adapter_for(association) if association else None

    for ref in run.input_items:
        item_id = str(ref.get("item_id") or "")
        ledger_ids = [str(x) for x in ref.get("ledger_ids", [])]
        rows = [
            r for r in await ingest_repo.get_ledger_entries(ledger_ids)
            if r.status == STATUS_EXTRACTING and r.extract_run_id == run_id
        ]
        if not rows:
            # The idempotent guard ITEMIZE applies by path, applied by id: the
            # rows moved on (a retry landed them, or the sweep failed them) —
            # or they are `extracting` again under a NEWER run, which owns them
            # now. Ownership is part of the guard, not just the status.
            skipped += 1
            continue
        if association_id is None:
            await fail_rows(rows, ASSOCIATION_VANISHED)
            continue
        if association is None or config is None or adapter is None:
            await fail_rows(rows, ASSOCIATION_UNUSABLE)
            continue
        key = f"{prefix}{item_id}{ITEM_DOCUMENT_SUFFIX}"
        if key not in outputs:
            await fail_rows(rows, f"extractor wrote no document for {item_id}")
            continue
        try:
            document = json.loads(await asyncio.to_thread(store.get, key))
        except Exception as err:
            await fail_rows(
                rows,
                f"extractor output for {item_id} is not readable JSON: {type(err).__name__}",
            )
            continue
        if not isinstance(document, dict):
            await fail_rows(rows, f"extractor output for {item_id} is not a JSON object")
            continue
        problem = check_extract_output(ref.get("draft") or {}, document)
        if problem:
            await fail_rows(rows, problem)
            continue

        outcome = await complete_item(
            ingest_repo, writer, adapter,
            association=association, config=config, item_id=item_id,
            members=rows, item_dict=document,
        )
        if outcome.status == "itemized":
            await ingest_repo.bump_flow_stats(
                association_id, items=1, bytes_added=outcome.bytes,
                latency_seconds=outcome.latency_seconds,
            )
            metrics.INGEST_EVENTS.labels(stage="itemized_item").inc()
            metrics.INGEST_BYTES.inc(outcome.bytes or 0)
            itemized += 1
        else:
            # complete_item already marked the rows failed with its reason.
            await ingest_repo.bump_flow_stats(association_id, failed=1)
            metrics.INGEST_EVENTS.labels(stage="failed").inc()
            failed += 1

    # The run prefix is spent either way (inputs, manifest, documents); a
    # failed delete is not fatal — the staging TTL sweep ages it out.
    for key in keys:
        try:
            await asyncio.to_thread(store.delete, key)
        except Exception:  # pragma: no cover - best effort
            logger.warning("extract finalize: could not delete", extra={"key": key})

    # Mirror the transform recorder (`finalize/process_run.py`): a run that
    # published nothing is downgraded so "succeeded" cannot mean "published
    # nothing", and a partially rejected run keeps its status but records WHAT
    # it lost on the ledger row.
    # `skipped` counts as landed here: on a finalize RETRY the items a first
    # pass published come back skipped, and a run that published must never be
    # downgraded to `dead`.
    if failed:
        await process_repo.finish_run(
            run_id,
            status="dead" if not itemized and not skipped else "succeeded",
            error=(
                f"all {failed} extracted items were rejected"
                if not itemized and not skipped
                else f"{failed} of {failed + itemized + skipped} extracted items"
                     " were rejected"
            ),
            log_ref=None,
            next_attempt_at=None,
        )
    logger.info(
        "extractor run finalized",
        extra={"run_id": run_id, "itemized": itemized, "failed": failed, "skipped": skipped},
    )
    return ExtractFinalizeResult(itemized=itemized, failed=failed, skipped=skipped)
