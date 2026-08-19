"""Pure ``flow_stats`` rollup math (M2-A, ROADMAP §6.6).

``collection_connections.flow_stats`` is PIPELINE-written telemetry (migration
005). The app reads it — ``listDeliveries`` serves its per-status counts from
the rollup, and the M2-B flow monitor will evaluate §5.1 expectations against
``last_activity_at`` / ``last_latency_seconds`` — and never writes it, with one
documented exception: the app's redeliver route flips its own dead→failed
count delta in the same statement that flips the row.

Shapes (all keys optional until first written):

- ingest associations::

    {"files": int, "bytes": int, "items": int, "failed": int,
     "last_activity_at": iso, "last_error_at": iso,
     "last_latency_seconds": float}

  ``files`` counts settled source files, ``items`` itemized items, ``failed``
  terminal itemize failures; ``bytes`` accumulates itemized asset bytes.

- deliver associations::

    {"counts": {"pending": int, "delivering": int, "delivered": int,
                "failed": int, "dead": int},
     "bytes": int, "last_activity_at": iso, "last_error_at": iso,
     "last_latency_seconds": float}

  ``counts`` is a live snapshot of ``delivery_log`` per-status row counts,
  maintained as deltas applied in the SAME transaction as each status
  transition (``delivery/repo.py``); ``bytes`` accumulates delivered bytes and
  ``last_latency_seconds`` is the most recent event→delivered latency.

These helpers are pure (dict in → new dict out) so the Pg repos and the test
fakes share one implementation of the math.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

DELIVERY_STATUSES = ("pending", "delivering", "delivered", "failed", "dead")


def zero_counts() -> dict[str, int]:
    return dict.fromkeys(DELIVERY_STATUSES, 0)


def _iso(now: dt.datetime | None) -> str:
    return (now or dt.datetime.now(dt.UTC)).isoformat()


def apply_count_delta(
    stats: dict[str, Any],
    prev: str | None,
    new: str | None,
    n: int = 1,
) -> dict[str, Any]:
    """Move ``n`` rows from status ``prev`` to ``new`` in ``counts``.

    ``prev=None`` is an insert, ``new=None`` a delete. Decrements clamp at 0 —
    a clamp firing means the snapshot drifted (see the repo's
    recompute-when-missing seeding), and staying at 0 beats going negative.
    """
    counts = {**zero_counts(), **(stats.get("counts") or {})}
    if prev is not None:
        counts[prev] = max(counts.get(prev, 0) - n, 0)
    if new is not None:
        counts[new] = counts.get(new, 0) + n
    return {**stats, "counts": counts}


def apply_delivery_event(
    stats: dict[str, Any],
    *,
    bytes_added: int = 0,
    latency_seconds: float | None = None,
    activity: bool = False,
    error: bool = False,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Fold one delivery outcome's scalar telemetry into the rollup."""
    out = dict(stats)
    if bytes_added:
        out["bytes"] = int(out.get("bytes") or 0) + bytes_added
    if latency_seconds is not None:
        out["last_latency_seconds"] = latency_seconds
    if activity:
        out["last_activity_at"] = _iso(now)
    if error:
        out["last_error_at"] = _iso(now)
    return out


def apply_ingest_activity(
    stats: dict[str, Any],
    *,
    files: int = 0,
    bytes_added: int = 0,
    items: int = 0,
    failed: int = 0,
    latency_seconds: float | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Fold one ingest settle/itemize outcome into the rollup."""
    out = dict(stats)
    additions = (("files", files), ("bytes", bytes_added), ("items", items), ("failed", failed))
    for key, added in additions:
        if added:
            out[key] = int(out.get(key) or 0) + added
    if latency_seconds is not None:
        out["last_latency_seconds"] = latency_seconds
    if files or items:
        out["last_activity_at"] = _iso(now)
    if failed:
        out["last_error_at"] = _iso(now)
    return out
