"""Ingest job wiring: the poll scheduler + the DISCOVER/GROUP/FETCH task chain.

The periodic ``ingest_poll`` task enqueues one DISCOVER job per due association;
each stage enqueues the next through the queue (DISCOVER → GROUP → FETCH), so a
single association's flow stays a handful of batch jobs regardless of file count
(ROADMAP §6.1). Handlers build a fresh :class:`PgIngestRepo` per run (mirrors the
connection drain) and reuse ``build_adapter`` / ``build_platform_client``. A
handler that arrives after the association is disabled/deleted no-ops.
"""

from __future__ import annotations

import datetime as dt
import logging

from pipeline.config import Settings
from pipeline.connections.build import build_adapter
from pipeline.ingest.config import IngestConfig, parse_ingest_config
from pipeline.ingest.discover import discover_stage
from pipeline.ingest.fetch import fetch_stage
from pipeline.ingest.group import group_stage
from pipeline.ingest.itemize import run_itemize
from pipeline.ingest.repo import IngestAssociation, PgIngestRepo
from pipeline.ingest.scheduler import due_associations
from pipeline.jobs._common import load_key_or_skip
from pipeline.queue.interface import QueueBackend
from pipeline.stac.pgstac_writer import PgPgstacWriter
from pipeline.storage.platform import build_platform_client

logger = logging.getLogger(__name__)

JOB_POLL = "pipeline.ingest_poll"
JOB_DISCOVER = "pipeline.ingest_discover"
JOB_GROUP = "pipeline.ingest_group"
JOB_FETCH = "pipeline.ingest_fetch"
JOB_ITEMIZE = "pipeline.ingest_itemize"
CRON = "* * * * *"


async def _load_association(
    settings: Settings, association_id: str
) -> tuple[PgIngestRepo, IngestAssociation, IngestConfig] | None:
    """Open a repo and load the enabled association + parsed config, or ``None``
    when the stage arrives after the association was disabled/deleted."""
    repo = PgIngestRepo(settings.database_url)
    association = await repo.get_association(association_id)
    if association is None:
        return None
    return repo, association, parse_ingest_config(association.config)


def register(queue: QueueBackend, settings: Settings) -> None:
    async def poll(timestamp: int) -> None:
        repo = PgIngestRepo(settings.database_url)
        due = await due_associations(repo, timestamp)
        if not due:
            return
        await queue.enqueue_batch(
            JOB_DISCOVER, [{"association_id": a.id} for a in due]
        )
        logger.info(
            "ingest poll enqueued discover jobs",
            extra={"due": len(due), "scheduled_timestamp": timestamp},
        )

    async def discover(association_id: str) -> None:
        master_key = load_key_or_skip(settings, JOB_DISCOVER)
        if master_key is None:
            return
        loaded = await _load_association(settings, association_id)
        if loaded is None:
            return
        repo, association, config = loaded
        adapter = build_adapter(
            association.connection, master_key, settings.egress_allow_hosts
        )
        await discover_stage(repo, association, config, adapter)
        # Chain to GROUP regardless of counts: settled files may be carried over
        # from an earlier tick (a group waiting on a late sibling).
        await queue.enqueue(JOB_GROUP, {"association_id": association_id})

    async def group(association_id: str) -> None:
        loaded = await _load_association(settings, association_id)
        if loaded is None:
            return
        repo, association, config = loaded
        result = await group_stage(repo, association.id, config, dt.datetime.now(dt.UTC))
        if not result.ready:
            return
        await queue.enqueue_batch(
            JOB_FETCH,
            [
                {
                    "association_id": association_id,
                    "item_id": g.item_id,
                    "source_paths": [m.source_path for m in g.members],
                }
                for g in result.ready
            ],
        )

    async def fetch(association_id: str, item_id: str, source_paths: list[str]) -> None:
        master_key = load_key_or_skip(settings, JOB_FETCH)
        if master_key is None:
            return
        loaded = await _load_association(settings, association_id)
        if loaded is None:
            return
        repo, association, config = loaded
        adapter = build_adapter(
            association.connection, master_key, settings.egress_allow_hosts
        )
        s3_client = build_platform_client(settings)
        stored = await fetch_stage(
            repo,
            association,
            config,
            adapter,
            s3_client,
            settings.staging_bucket,
            item_id,
            source_paths,
        )
        if stored:
            await queue.enqueue(
                JOB_ITEMIZE,
                {
                    "association_id": association_id,
                    "item_id": item_id,
                    "source_paths": source_paths,
                },
            )

    async def itemize(association_id: str, item_id: str, source_paths: list[str]) -> None:
        master_key = load_key_or_skip(settings, JOB_ITEMIZE)
        if master_key is None:
            return
        loaded = await _load_association(settings, association_id)
        if loaded is None:
            return
        repo, association, config = loaded
        adapter = build_adapter(
            association.connection, master_key, settings.egress_allow_hosts
        )
        s3_client = build_platform_client(settings)
        writer = PgPgstacWriter(settings.database_url)
        await run_itemize(
            repo,
            writer,
            adapter,
            s3_client,
            association=association,
            config=config,
            item_id=item_id,
            source_paths=source_paths,
            bucket=settings.staging_bucket,
            asset_href_base=settings.asset_href_base,
        )

    queue.register_periodic(poll, name=JOB_POLL, cron=CRON)
    queue.register_task(discover, name=JOB_DISCOVER)
    queue.register_task(group, name=JOB_GROUP)
    queue.register_task(fetch, name=JOB_FETCH)
    queue.register_task(itemize, name=JOB_ITEMIZE)
