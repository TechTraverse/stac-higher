"""post-ingest source action (§5.1 `post_ingest`, §6.1 tail).

Runs once after a group is successfully itemized. `leave` (default) no-ops;
`delete` removes the source files; `move:<path>` copies each to `<path>/<name>`
then deletes the original. Non-fatal by design — the item is already catalogued,
so a failed source cleanup is logged (Phase 6 can alert) but never fails the job
or reverts the ledger. DISCOVER won't re-see moved/deleted files, so this can't
re-trigger.
"""

from __future__ import annotations

import logging
import posixpath
from collections.abc import Sequence

from pipeline.connections.adapters.base import StorageAdapter
from pipeline.ingest.config import IngestConfig
from pipeline.ingest.discover import source_fetch_path

logger = logging.getLogger(__name__)


async def apply_post_ingest(
    adapter: StorageAdapter, config: IngestConfig, *, source_paths: Sequence[str]
) -> None:
    action = config.post_ingest
    if action == "leave":
        return
    if config.storage_mode == "reference":
        # Reference mode never owns the bytes — delete/move would orphan the
        # item's asset. The app guard rejects this at config time; this is
        # defense-in-depth for configs written before the guard / directly to DB.
        logger.info(
            "post-ingest %r skipped: reference mode keeps source bytes", action,
            extra={"action": action},
        )
        return
    for relpath in source_paths:
        src = source_fetch_path(config.source_path, relpath)
        try:
            if action == "delete":
                await adapter.delete(src)
            elif action.startswith("move:"):
                target_dir = action[len("move:") :]
                dest = posixpath.join(target_dir, posixpath.basename(relpath))
                data = await adapter.get(src)
                await adapter.put(dest, data)
                await adapter.delete(src)
        except Exception:
            logger.exception(
                "post-ingest action failed (non-fatal)",
                extra={"action": action, "source_path": src},
            )
