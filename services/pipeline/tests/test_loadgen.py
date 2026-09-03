"""The M3 load harness's decidable parts (M3-S-A).

The harness itself is I/O — MinIO, Postgres, an HTTP scrape — and is exercised
by running it. What is unit-testable is the arithmetic and the documents, and
that is exactly where a silent mistake would poison a MEASUREMENT: a pacer that
drifts reports the wrong offered rate, a rate calculation off by the sample
interval reports the wrong throughput, and an ingest config the pipeline
refuses reports zero throughput for the wrong reason.
"""

from __future__ import annotations

import pytest

from pipeline.ingest.config import parse_ingest_config
from pipeline.loadgen.feed import emission_offsets, granule, raster_granule
from pipeline.loadgen.fixtures import deliver_config, ingest_config, metadata_config
from pipeline.loadgen.report import job_means
from pipeline.loadgen.sample import TABLE_QUERIES, Sample, parse_prometheus, rate_table

# ---------------------------------------------------------------------------
# Prometheus scrape parsing
# ---------------------------------------------------------------------------

SCRAPE = """
# HELP pipeline_job_runs_total Queue task / periodic tick executions
# TYPE pipeline_job_runs_total counter
pipeline_job_runs_total{job="pipeline.ingest_fetch",outcome="ok"} 12.0
pipeline_job_runs_total{job="pipeline.ingest_fetch",outcome="error"} 1.0
pipeline_ingest_events_total{stage="itemized_item"} 40.0
pipeline_ingest_bytes_total 2048.0
pipeline_job_seconds_sum{job="pipeline.ingest_fetch"} 6.0
pipeline_job_seconds_count{job="pipeline.ingest_fetch"} 12.0
"""


def test_labelled_series_keep_their_labels_so_two_outcomes_do_not_collapse():
    parsed = parse_prometheus(SCRAPE)
    assert parsed['pipeline_job_runs_total{job="pipeline.ingest_fetch",outcome="ok"}'] == 12.0
    assert parsed['pipeline_job_runs_total{job="pipeline.ingest_fetch",outcome="error"}'] == 1.0


def test_unlabelled_series_and_comments():
    parsed = parse_prometheus(SCRAPE)
    assert parsed["pipeline_ingest_bytes_total"] == 2048.0
    assert not any(k.startswith("#") for k in parsed)


def test_histogram_sum_and_count_survive_so_mean_duration_is_derivable():
    parsed = parse_prometheus(SCRAPE)
    mean = (
        parsed['pipeline_job_seconds_sum{job="pipeline.ingest_fetch"}']
        / parsed['pipeline_job_seconds_count{job="pipeline.ingest_fetch"}']
    )
    assert mean == 0.5


# ---------------------------------------------------------------------------
# rates
# ---------------------------------------------------------------------------


def test_a_rate_is_per_second_not_per_sample():
    # The whole report is read as items/s. Dividing by anything but the real
    # elapsed time silently rescales every number in it.
    before = Sample(at=100.0, counters={"x": 10.0}, tables={})
    after = Sample(at=110.0, counters={"x": 110.0}, tables={})
    assert rate_table(before, after)["x"] == pytest.approx(10.0)


def test_a_series_that_appeared_mid_window_counts_from_zero():
    before = Sample(at=0.0, counters={}, tables={})
    after = Sample(at=2.0, counters={"x": 8.0}, tables={})
    assert rate_table(before, after)["x"] == pytest.approx(4.0)


def test_a_counter_reset_reports_no_rate_rather_than_a_negative_one():
    """The pipeline restarting mid-window zeroes its counters. A negative
    'rate' in a load report is worse than an absent one."""
    before = Sample(at=0.0, counters={"x": 100.0}, tables={})
    after = Sample(at=1.0, counters={"x": 3.0}, tables={})
    assert rate_table(before, after)["x"] is None


def test_two_samples_at_the_same_instant_are_refused_rather_than_dividing_by_zero():
    same = Sample(at=5.0, counters={"x": 1.0}, tables={})
    with pytest.raises(ValueError):
        rate_table(same, same)


def test_table_counts_are_rated_too_so_a_backlog_trend_is_visible():
    before = Sample(at=0.0, counters={}, tables={"item_events_pending": 10})
    after = Sample(at=5.0, counters={}, tables={"item_events_pending": 60})
    assert rate_table(before, after)["item_events_pending"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# the pacer
# ---------------------------------------------------------------------------


def test_offsets_are_evenly_spaced_at_the_target_rate():
    assert emission_offsets(count=4, rate=2.0) == [0.0, 0.5, 1.0, 1.5]


def test_offsets_do_not_drift_over_a_long_run():
    """Accumulating `+= 1/rate` drifts; the report's offered rate has to be
    the rate we claim, so the schedule is computed from the index."""
    offsets = emission_offsets(count=100_000, rate=30.0)
    assert offsets[-1] == pytest.approx((100_000 - 1) / 30.0)


def test_rate_zero_means_as_fast_as_possible_not_an_error():
    # The saturation probe: offer everything at once and watch where the
    # backlog forms. That is a different question from "can it hold 30/s".
    assert emission_offsets(count=3, rate=0) == [0.0, 0.0, 0.0]


def test_a_negative_rate_is_refused():
    with pytest.raises(ValueError):
        emission_offsets(count=1, rate=-1.0)


# ---------------------------------------------------------------------------
# generated granules
# ---------------------------------------------------------------------------


def test_a_granule_is_exactly_the_requested_size():
    # Byte volume is half of what S-A measures; a generator that rounds its
    # own payload would make the bytes/s column fiction.
    assert len(granule(0, asset_bytes=4096, prefix="load/").body) == 4096


def test_granule_keys_are_unique_stable_and_live_under_the_prefix():
    keys = {granule(i, asset_bytes=8, prefix="load/run-1/").key for i in range(500)}
    assert len(keys) == 500
    assert all(k.startswith("load/run-1/") for k in keys)
    assert granule(7, asset_bytes=8, prefix="load/").key == granule(
        7, asset_bytes=8, prefix="load/"
    ).key


def test_each_granule_is_its_own_item_under_the_no_grouping_rule():
    """One file per item is what makes offered files/s equal offered items/s.
    Two granules sharing a basename would silently halve the item count."""
    stems = {granule(i, asset_bytes=8, prefix="p/").key.rsplit(".", 1)[0] for i in range(50)}
    assert len(stems) == 50


def test_granule_bodies_are_not_all_identical():
    """Identical bodies let a store dedupe them, which would make the byte
    numbers a lie."""
    bodies = {granule(i, asset_bytes=64, prefix="p/").body for i in range(20)}
    assert len(bodies) == 20


# ---------------------------------------------------------------------------
# the documents the harness writes
# ---------------------------------------------------------------------------


def test_the_ingest_config_is_one_the_pipeline_actually_accepts():
    # The harness writes this row by hand, bypassing the app's Zod gate. If it
    # drifts, the measurement reads as "zero throughput" instead of "broken
    # config", which is the most expensive way to be wrong here.
    parsed = parse_ingest_config(ingest_config("load/run-1/", storage_mode="copy"))
    assert parsed.source_path == "load/run-1/"
    assert parsed.storage_mode == "copy"
    # One file, one item — the property the offered-rate arithmetic rests on.
    assert parsed.grouping.rule == "none"


def test_reference_mode_is_expressible_for_the_copy_vs_reference_comparison():
    assert parse_ingest_config(ingest_config("p/", storage_mode="reference")).storage_mode == (
        "reference"
    )


def test_an_unknown_storage_mode_fails_here_rather_than_in_the_database():
    with pytest.raises(ValueError):
        ingest_config("p/", storage_mode="teleport")


def test_the_ingest_config_polls_every_tick_so_the_feed_is_not_gated_by_the_scheduler():
    """The §5.1 default is 300s. A five-minute poll would measure the
    scheduler, not the pipeline."""
    assert parse_ingest_config(ingest_config("p/")).poll_frequency_seconds == 60


def test_the_deliver_config_carries_the_fields_the_worker_reads():
    config = deliver_config("m3/{collection}/{item_id}/{filename}")
    assert config["path_template"] == "m3/{collection}/{item_id}/{filename}"
    assert config["max_concurrent_transfers"] >= 1
    assert config["item_filter"] is None


# ---------------------------------------------------------------------------
# the two feed profiles
# ---------------------------------------------------------------------------


def test_a_raster_granule_is_a_real_geotiff_gdal_can_open():
    """The `raster_auto` profile exists to measure the EXTRACT cost (I-26).
    Random bytes named .tif measure the FAILURE path instead — which is what
    the first S-A run actually did, at 30/30 items failed."""
    rasterio = pytest.importorskip("rasterio")
    body = raster_granule(0, asset_bytes=64 * 1024, prefix="p/").body
    with rasterio.MemoryFile(body) as memfile, memfile.open() as dataset:
        assert dataset.count == 1
        # rio-stac needs both to produce a geometry; without them EXTRACT
        # fails for a reason that has nothing to do with throughput.
        assert dataset.crs is not None
        assert dataset.bounds.left != dataset.bounds.right


def test_a_raster_granule_is_close_to_the_requested_size():
    # Not exact — a GeoTIFF carries a header — but the byte column must not be
    # off by an order of magnitude.
    pytest.importorskip("rasterio")
    body = raster_granule(0, asset_bytes=256 * 1024, prefix="p/").body
    assert 0.5 * 256 * 1024 <= len(body) <= 2.0 * 256 * 1024


def test_raster_granules_are_distinct_items_with_distinct_bytes():
    pytest.importorskip("rasterio")
    granules = [raster_granule(i, asset_bytes=8 * 1024, prefix="p/") for i in range(8)]
    assert len({g.key for g in granules}) == 8
    assert len({g.body for g in granules}) == 8


def test_defaults_only_needs_no_gdal_and_still_resolves_a_datetime_and_geometry():
    """The plumbing-ceiling profile. Without both defaults, EXTRACT raises
    'no datetime could be resolved' and the run measures nothing."""
    cfg = metadata_config("defaults_only")
    assert cfg["strategy"] == "defaults_only"
    assert cfg["defaults"]["datetime"] == "file_mtime"
    assert cfg["defaults"]["geometry"] == "collection"


def test_the_metadata_block_reaches_the_pipeline_through_the_ingest_config():
    parsed = parse_ingest_config(
        ingest_config("p/", metadata=metadata_config("defaults_only"))
    )
    assert parsed.metadata["strategy"] == "defaults_only"


def test_the_default_profile_is_the_cheap_one():
    """A baseline should start by measuring the pipeline, not GDAL; the raster
    profile is the deliberate second run."""
    assert parse_ingest_config(ingest_config("p/")).metadata["strategy"] == "defaults_only"


def test_an_unknown_metadata_strategy_fails_here_rather_than_at_extract_time():
    with pytest.raises(ValueError):
        metadata_config("clairvoyance")


# ---------------------------------------------------------------------------
# what the sampler looks at
# ---------------------------------------------------------------------------


def test_every_ledger_status_has_a_backlog_query():
    """A status with no query is a backlog that cannot be seen. The first S-A
    run had 826 rows parked in `seen` and the report showed every ingest
    backlog at zero."""
    from pipeline.ingest import repo as ingest_repo

    statuses = {
        value
        for name, value in vars(ingest_repo).items()
        if name.startswith("STATUS_") and isinstance(value, str)
    }
    covered = {
        key.removeprefix("ingest_files_")
        for key in TABLE_QUERIES
        if key.startswith("ingest_files_")
    }
    assert statuses <= covered


def test_the_queue_backlog_query_names_the_schema_procrastinate_actually_uses():
    """Unqualified, it resolves against the search_path and silently returns
    nothing — which reads as an empty queue."""
    assert "procrastinate.procrastinate_jobs" in TABLE_QUERIES["procrastinate_todo"]


# ---------------------------------------------------------------------------
# per-job mean duration
# ---------------------------------------------------------------------------


def _hist(job: str, total: float, count: float) -> dict[str, float]:
    return {
        f'pipeline_job_seconds_sum{{job="{job}"}}': total,
        f'pipeline_job_seconds_count{{job="{job}"}}': count,
    }


def test_the_mean_is_over_the_WINDOW_not_the_process_lifetime():
    """The histogram is cumulative since the pipeline started. Reading the last
    sample alone reports a mean dominated by whatever happened before the run —
    which is exactly how a 10x improvement can be made to look like none."""
    before = Sample(at=0.0, counters=_hist("pipeline.ingest_itemize", 200.0, 1000.0))
    after = Sample(at=10.0, counters=_hist("pipeline.ingest_itemize", 202.0, 1100.0))

    (job, calls, mean), = job_means(before, after)

    assert job == "pipeline.ingest_itemize"
    assert calls == 100
    assert mean == pytest.approx(0.02)


def test_a_job_that_did_not_run_in_the_window_is_omitted_not_zero():
    before = Sample(at=0.0, counters=_hist("pipeline.heartbeat", 1.0, 10.0))
    after = Sample(at=5.0, counters=_hist("pipeline.heartbeat", 1.0, 10.0))
    assert job_means(before, after) == []


def test_a_restart_mid_window_drops_the_job_rather_than_reporting_a_negative_mean():
    before = Sample(at=0.0, counters=_hist("pipeline.ingest_fetch", 50.0, 500.0))
    after = Sample(at=5.0, counters=_hist("pipeline.ingest_fetch", 0.4, 4.0))
    assert job_means(before, after) == []


def test_the_slowest_job_is_listed_first():
    counters = {**_hist("fast", 0.0, 0.0), **_hist("slow", 0.0, 0.0)}
    after = {**_hist("fast", 1.0, 100.0), **_hist("slow", 10.0, 100.0)}
    rows = job_means(Sample(at=0.0, counters=counters), Sample(at=1.0, counters=after))
    assert [r[0] for r in rows] == ["slow", "fast"]


# ---------------------------------------------------------------------------
# extractor profile (G-6)
# ---------------------------------------------------------------------------


def test_extractor_profile_round_trips_and_names_the_process():
    from pipeline.ingest.config import parse_ingest_config
    from pipeline.ingest.extract import parse_metadata
    from pipeline.loadgen.fixtures import EXTRACTOR_PROCESS_ID, ingest_config, metadata_config

    cfg = ingest_config("load/x/", metadata=metadata_config("extractor"))
    parsed = parse_ingest_config(cfg)
    meta = parse_metadata(parsed.metadata)
    assert meta.strategy == "extractor"
    assert meta.extractor_process_id == EXTRACTOR_PROCESS_ID


def test_extractor_code_is_a_pass_through():
    """The loadgen extractor must be executable and must keep id/collection/
    hrefs — the same rules finalize enforces — so the harness measures the
    extractor PATH, not a rejection loop."""
    from pipeline.loadgen.fixtures import EXTRACTOR_CODE

    compile(EXTRACTOR_CODE, "<extractor>", "exec")
    assert "STAC_HIGHER_INPUT_MANIFEST" in EXTRACTOR_CODE
    assert '["id"]' in EXTRACTOR_CODE and "datetime" in EXTRACTOR_CODE
