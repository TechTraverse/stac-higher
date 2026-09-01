"""Turn a series of samples into the text block that goes in the scoping notes.

Two windows are reported for every series, because they answer different
questions and a single average hides both:

- **whole-run**, first sample to last — the sustained rate, which is what the
  M3 gate is stated in.
- **per-interval**, sample to sample — where the rate CHANGED, which is how a
  bottleneck announces itself (a stage that starts fast and decays is queueing
  somewhere; a backlog series with a persistently positive rate is the
  bottleneck itself).
"""

from __future__ import annotations

from itertools import pairwise

from pipeline.loadgen.sample import Sample, rate_table

#: Series worth putting in front of a human, in reading order: what arrived,
#: what got through each stage, and what is piling up.
HEADLINE = (
    ("offered → settled", 'pipeline_ingest_events_total{stage="settled_file"}'),
    ("itemized", 'pipeline_ingest_events_total{stage="itemized_item"}'),
    ("ingest failed", 'pipeline_ingest_events_total{stage="failed"}'),
    ("ingest bytes", "pipeline_ingest_bytes_total"),
    ("catalog items", "pgstac_items"),
    ("delivered", 'pipeline_deliveries_total{outcome="delivered"}'),
    ("delivery dead", 'pipeline_deliveries_total{outcome="dead"}'),
    ("BACKLOG seen", "ingest_files_seen"),
    ("BACKLOG settled", "ingest_files_settled"),
    ("BACKLOG fetching", "ingest_files_fetching"),
    ("BACKLOG stored", "ingest_files_stored"),
    ("BACKLOG outbox", "item_events_pending"),
    ("BACKLOG delivery", "delivery_log_pending"),
    ("BACKLOG queue todo", "procrastinate_todo"),
    ("queue doing", "procrastinate_doing"),
)


def _fmt(value: float | None) -> str:
    if value is None:
        return "reset"
    if value == 0:
        return "0"
    if abs(value) >= 100:
        return f"{value:,.0f}"
    return f"{value:.2f}"


def job_means(before: Sample, after: Sample) -> list[tuple[str, float, float]]:
    """(job, calls, mean seconds) over the WINDOW, slowest first.

    The histogram is cumulative since the pipeline started, so reading one
    sample reports a lifetime mean — which during the S-A measurements made a
    7x improvement look like none, because the window's fast calls were
    averaged against thousands of slow ones from before the change.

    A job with no calls in the window is omitted rather than shown as zero, and
    so is one whose counters went backwards (a restart mid-window): both are
    "no measurement", and printing a number for them would invent one.
    """
    rows = []
    for key, total_after in after.counters.items():
        if not key.startswith("pipeline_job_seconds_sum{"):
            continue
        labels = key[len("pipeline_job_seconds_sum{") : -1]
        count_key = f"pipeline_job_seconds_count{{{labels}}}"
        calls = after.counters.get(count_key, 0.0) - before.counters.get(count_key, 0.0)
        seconds = total_after - before.counters.get(key, 0.0)
        if calls <= 0 or seconds < 0:
            continue
        job = labels.replace('job="', "").replace('"', "")
        rows.append((job, calls, seconds / calls))
    return sorted(rows, key=lambda row: row[2], reverse=True)


def render(samples: list[Sample], interval: float) -> str:
    if len(samples) < 2:
        return "not enough samples to compute a rate"
    first, last = samples[0], samples[-1]
    overall = rate_table(first, last)
    window = last.at - first.at

    lines = [
        f"window: {window:.1f}s over {len(samples)} samples "
        f"(nominal interval {interval}s)",
        "",
        f"{'series':<22}{'per-second':>12}   per-interval trend",
        "-" * 78,
    ]
    steps = [rate_table(a, b) for a, b in pairwise(samples)]
    for title, key in HEADLINE:
        if key not in overall:
            continue
        trend = " ".join(_fmt(step.get(key)) for step in steps[-12:])
        lines.append(f"{title:<22}{_fmt(overall[key]):>12}   {trend}")

    lines += ["", f"{'job':<34}{'calls':>10}{'mean s':>10}", "-" * 78]
    for job, calls, mean in job_means(first, last):
        lines.append(f"{job:<34}{calls:>10,.0f}{mean:>10.3f}")

    lines += ["", "absolute counts at the end of the window:"]
    for name, value in sorted(last.tables.items()):
        lines.append(f"  {name:<28}{value:>12,.0f}")
    return "\n".join(lines)
