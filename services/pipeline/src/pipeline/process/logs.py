"""Run-log capture (spec §9, I-62 decided).

The rules that make this more than "write a file", and why:

- **Capped during capture** (``PROCESS_LOG_MAX_BYTES``, default 10 MB), with
  a truncation marker. A process that loops printing must not be able to fill
  the platform bucket, and a silent truncation would make an operator debug a
  log that lies about where it ends.
- **Object BEFORE row** (I-62). The retention leg deletes in the opposite
  order — object first, then the row — so writing in this order means the
  only interleaving that can survive a crash is an object with no row (which
  the staging TTL sweep ages out), never a row pointing at bytes that do not
  exist.
- **Never interleaved into the pipeline's own logs** (ADR 0013). Run output
  is untrusted content authored by user code; it goes to object storage and
  is referenced by key, so nothing in it can forge a platform log line.
"""

from __future__ import annotations

import logging

from pipeline.storage.keys import run_log_key
from pipeline.storage.platform import put_object

logger = logging.getLogger(__name__)

TRUNCATION_MARKER = (
    b"\n--- log truncated: run exceeded PROCESS_LOG_MAX_BYTES ---\n"
)


def cap(payload: bytes, max_bytes: int) -> bytes:
    """Truncate to ``max_bytes`` INCLUDING the marker, so the cap is a real
    ceiling on the stored object rather than a target it overshoots."""
    if len(payload) <= max_bytes:
        return payload
    keep = max(0, max_bytes - len(TRUNCATION_MARKER))
    return payload[:keep] + TRUNCATION_MARKER


def store_run_log(
    client,
    bucket: str,
    process_id: str,
    run_id: str,
    payload: bytes,
    max_bytes: int,
) -> str | None:
    """Write the capped log and return its key for ``process_runs.log_ref``.

    Returns ``None`` when the write fails: a lost log must never lose the
    run's verdict, which is already known by the time this is called. The
    failure is logged as a platform event (the run id, not the content).
    """
    key = run_log_key(process_id, run_id)
    try:
        put_object(client, bucket, key, cap(payload, max_bytes), content_type="text/plain")
    except Exception as err:  # any store failure is non-fatal here (see below)
        logger.warning(
            "process run log could not be stored",
            extra={"run_id": run_id, "process_id": process_id, "error": str(err)},
        )
        return None
    return key
