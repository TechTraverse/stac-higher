"""Stage a run's inputs before launch (GOES spec §3.2, ADR 0018).

Remote input bytes are copied into the run's own ``inputs/`` area so that an
``isolated`` run — no network — can still read them with its run-scoped
credentials. The manifest is written LAST: it is the "this batch is complete"
marker, so a partially staged batch never looks finished.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from pipeline.config import Settings
from pipeline.connections.http_fetch import fetch_public_url
from pipeline.connections.sources import association_for_href
from pipeline.ingest.repo import PgIngestRepo
from pipeline.process.inputs import InputPlan
from pipeline.storage import platform

logger = logging.getLogger(__name__)

#: href -> bytes. Injected into the runner so the run path stays testable
#: without a database, a connection envelope, or the network.
RemoteFetcher = Callable[[str], Awaitable[bytes]]


class InputStagingError(Exception):
    """An input could not be staged — the run must not start."""


async def stage_inputs(
    plan: InputPlan,
    *,
    storage_client,
    bucket: str,
    fetch_remote: RemoteFetcher,
    concurrency: int = 4,
) -> None:
    """Fetch every remote input into the run's inputs area, then write the
    manifest LAST so a partially staged batch never looks complete."""
    gate = asyncio.Semaphore(max(1, concurrency))

    async def one(fetch):
        async with gate:
            try:
                data = await fetch_remote(fetch.href)
                await asyncio.to_thread(
                    platform.put_object, storage_client, bucket, fetch.key, data
                )
            except Exception as err:
                raise InputStagingError(
                    f"could not stage item {fetch.item_id!r} asset {fetch.asset_key!r} "
                    f"from {fetch.href!r}: {type(err).__name__}: {err}"
                ) from err

    await asyncio.gather(*(one(f) for f in plan.fetches))
    try:
        await asyncio.to_thread(
            platform.put_object,
            storage_client,
            bucket,
            plan.manifest_key,
            json.dumps(plan.manifest, separators=(",", ":")).encode(),
            content_type="application/json",
        )
    except Exception as err:
        raise InputStagingError(
            f"could not write the input manifest: {type(err).__name__}"
        ) from err


def build_remote_fetcher(settings: Settings, master_key: bytes | None) -> RemoteFetcher:
    """Prefer the reference-mode association whose connection owns the href
    (its credentials/anonymity and the egress policy come with it); else an
    egress-checked public GET (ISSUES I-91). ``master_key is None`` means no
    adapter can be built, so every href takes the public path."""
    repo = PgIngestRepo(settings.database_url)

    async def fetch(href: str) -> bytes:
        if master_key is not None:
            match = association_for_href(
                href,
                await repo.list_enabled_ingest_associations(),
                master_key,
                settings.egress_allow_hosts,
            )
            if match is not None:
                return await match.adapter.get(match.key)
        return await asyncio.to_thread(fetch_public_url, href, settings.egress_allow_hosts)

    return fetch
