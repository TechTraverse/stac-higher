"""Scrape `/metrics`, count the ledger tables, and turn two samples into rates.

A load report is only as trustworthy as its arithmetic, so two decisions are
made here rather than at the call site:

- **Rates are per SECOND of measured wall clock**, never per sample. The
  sampler's interval slips under load — which is exactly when the numbers
  matter — so dividing by the nominal interval would inflate every figure at
  the moment the system is struggling.
- **A counter that went backwards yields None, not a negative rate.** The
  pipeline restarting mid-window zeroes its counters; reporting "-97 items/s"
  would be worse than reporting a hole.
"""

from __future__ import annotations

import time
import urllib.request
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Sample:
    """One instant: the scrape, the table counts, and when it was taken."""

    at: float
    counters: dict[str, float] = field(default_factory=dict)
    #: Absolute row counts (backlogs, catalog size) — rated the same way, so a
    #: growing queue shows up as a positive rows/s.
    tables: dict[str, float] = field(default_factory=dict)


def parse_prometheus(text: str) -> dict[str, float]:
    """Prometheus text exposition → ``{series: value}``.

    Labels are kept in the key verbatim, because collapsing
    ``outcome="ok"`` and ``outcome="error"`` into one number is precisely the
    distinction a load report exists to show.
    """
    out: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        series, _, value = line.rpartition(" ")
        if not series:
            continue
        try:
            out[series] = float(value)
        except ValueError:
            continue
    return out


def rate_table(before: Sample, after: Sample) -> dict[str, float | None]:
    """Per-second change for every series in either sample."""
    elapsed = after.at - before.at
    if elapsed <= 0:
        raise ValueError(f"samples are not ordered in time ({before.at} → {after.at})")
    rates: dict[str, float | None] = {}
    for key in set(before.counters) | set(after.counters) | set(before.tables) | set(after.tables):
        start = before.counters.get(key, before.tables.get(key, 0.0))
        end = after.counters.get(key, after.tables.get(key, 0.0))
        rates[key] = None if end < start else (end - start) / elapsed
    return rates


def scrape(metrics_url: str, timeout: float = 5.0) -> dict[str, float]:
    with urllib.request.urlopen(metrics_url, timeout=timeout) as response:
        return parse_prometheus(response.read().decode("utf-8", "replace"))


#: The ledger counts worth watching per tick. Deliberately cheap aggregate
#: queries: the sampler must not become part of the load it is measuring.
TABLE_QUERIES: dict[str, str] = {
    "pgstac_items": "SELECT count(*) FROM pgstac.items",
    "pgstac_query_queue": (
        # M3-A: partition-stats statements deferred by the writer's use_queue
        # GUC and waiting for the drain tick. Bounded by partitions written,
        # not by items; a steadily rising count means the drainer stopped.
        "SELECT count(*) FROM pgstac.query_queue"
    ),
    "pgstac_partitions": (
        # The drain's cost (two REFRESH MATERIALIZED VIEWs) scales with this,
        # not with write rate — spec §4.5. `partitions` itself is a
        # materialized view only `update_partition_stats` refreshes — the
        # very statement the drain defers — so counting it would report a
        # stale, last-drain snapshot and could queue behind the drain's
        # ACCESS EXCLUSIVE refresh. `partitions_view`, the live view over
        # `pg_partition_tree`, has neither problem.
        "SELECT count(*) FROM pgstac.partitions_view"
    ),
    "ingest_files_seen": (
        # `seen` is where the two-poll settle check parks a file, so a growing
        # count here means DISCOVER is behind, not that nothing arrived.
        "SELECT count(*) FROM stac_higher.ingest_files WHERE status = 'seen'"
    ),
    "ingest_files_settled": (
        "SELECT count(*) FROM stac_higher.ingest_files WHERE status = 'settled'"
    ),
    "ingest_files_fetching": (
        "SELECT count(*) FROM stac_higher.ingest_files WHERE status = 'fetching'"
    ),
    "ingest_files_stored": (
        "SELECT count(*) FROM stac_higher.ingest_files WHERE status = 'stored'"
    ),
    "ingest_files_extracting": (
        # G-6: files parked while their extractor run executes.
        "SELECT count(*) FROM stac_higher.ingest_files WHERE status = 'extracting'"
    ),
    "ingest_files_itemized": (
        "SELECT count(*) FROM stac_higher.ingest_files WHERE status = 'itemized'"
    ),
    "ingest_files_failed": (
        "SELECT count(*) FROM stac_higher.ingest_files WHERE status = 'failed'"
    ),
    "item_events_pending": (
        "SELECT count(*) FROM stac_higher.item_events WHERE processed_at IS NULL"
    ),
    "item_events_total": "SELECT count(*) FROM stac_higher.item_events",
    "delivery_log_pending": (
        "SELECT count(*) FROM stac_higher.delivery_log WHERE status = 'pending'"
    ),
    "delivery_log_delivered": (
        "SELECT count(*) FROM stac_higher.delivery_log WHERE status = 'delivered'"
    ),
    "delivery_log_dead": (
        "SELECT count(*) FROM stac_higher.delivery_log WHERE status = 'dead'"
    ),
    "procrastinate_todo": (
        "SELECT count(*) FROM procrastinate.procrastinate_jobs WHERE status = 'todo'"
    ),
    "procrastinate_doing": (
        "SELECT count(*) FROM procrastinate.procrastinate_jobs WHERE status = 'doing'"
    ),
}


def count_tables(conn) -> dict[str, float]:  # pragma: no cover - needs a DB
    """Run TABLE_QUERIES on an open psycopg connection.

    A query against a table that does not exist yet (Procrastinate's, before
    the worker has ever started) is recorded as absent rather than fatal — a
    sampler that dies on a missing table takes the whole measurement with it.
    """
    counts: dict[str, float] = {}
    for name, sql in TABLE_QUERIES.items():
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
            counts[name] = float(row[0]) if row else 0.0
        except Exception:
            conn.rollback()
    return counts


def take(metrics_url: str, conn=None) -> Sample:  # pragma: no cover - I/O
    return Sample(
        at=time.monotonic(),
        counters=scrape(metrics_url),
        tables=count_tables(conn) if conn is not None else {},
    )
