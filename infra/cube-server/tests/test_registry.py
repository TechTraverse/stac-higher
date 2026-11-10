import psycopg
import pytest

from stac_higher_cube_server.registry import CubeRegistry, S3RepoOpener, UnsupportedSourceError
from stac_higher_cube_server.settings import Settings
from stac_higher_cube_server.sinks import BadSink, SinkRow
from tests.cubes import FakeSource, LocalOpener, make_local_cube

SETTINGS = Settings(
    db_dsn="postgresql://unused",
    s3_endpoint="http://127.0.0.1:9",
    s3_access_key_id="k",
    s3_secret_access_key="s",
    platform_bucket="stac-higher",
)


def row(collection_id: str, *, variables=("CMI",), prefixes=("s3://noaa-goes19/",)) -> SinkRow:
    return SinkRow(
        id=f"sink-{collection_id}",
        cube_collection_id=collection_id,
        append_dim="t",
        variables=tuple(variables),
        source_prefixes=tuple(prefixes),
    )


@pytest.fixture
def c1(tmp_path):
    return make_local_cube(tmp_path / "c1", steps=2)


def test_opens_enabled_sinks_and_serves_by_collection_id(c1):
    registry = CubeRegistry(FakeSource(row("c1")), LocalOpener({"c1": c1}))
    assert registry.refresh() == ["c1"]
    assert registry.get("c1").sizes["t"] == 2
    assert registry.get("nope") is None
    assert registry.ids() == ["c1"]
    health = registry.health()
    assert health["datasets"]["c1"]["steps"] == 2
    assert health["datasets"]["c1"]["snapshot_id"] == c1.snapshot_id
    assert health["errors"] == {}
    assert health["last_reload_error"] is None
    assert health["last_reload_at"] is not None


def test_reopens_when_main_moves(c1):
    registry = CubeRegistry(FakeSource(row("c1")), LocalOpener({"c1": c1}))
    registry.refresh()
    before = registry.get("c1").attrs["_xpublish_id"]
    c1.append(1)
    assert registry.refresh() == ["c1"]
    assert registry.get("c1").sizes["t"] == 3
    assert registry.get("c1").attrs["_xpublish_id"] != before
    assert registry.get("c1").attrs["_xpublish_id"] == f"c1:{c1.snapshot_id}"


def test_unchanged_snapshot_is_not_reopened(c1):
    registry = CubeRegistry(FakeSource(row("c1")), LocalOpener({"c1": c1}))
    registry.refresh()
    first = registry.get("c1")
    assert registry.refresh() == []
    assert registry.get("c1") is first


def test_reopens_when_variables_change(c1):
    source = FakeSource(row("c1"))
    registry = CubeRegistry(source, LocalOpener({"c1": c1}))
    registry.refresh()
    source.rows = [row("c1", variables=("CMI", "DQF"))]
    assert registry.refresh() == ["c1"]


def test_disabled_or_deleted_sink_is_dropped(c1):
    source = FakeSource(row("c1"))
    registry = CubeRegistry(source, LocalOpener({"c1": c1}))
    registry.refresh()
    source.rows = []
    registry.refresh()
    assert registry.get("c1") is None
    assert registry.ids() == []
    assert "c1" not in registry.health()["datasets"]


def test_trimmed_to_zero_steps_is_empty_not_served(c1):
    registry = CubeRegistry(FakeSource(row("c1")), LocalOpener({"c1": c1}))
    registry.refresh()
    c1.trim_to(0)
    registry.refresh()
    assert registry.get("c1") is None
    health = registry.health()
    assert "c1" in health["empty"]
    assert health["errors"] == {}


def test_missing_repository_is_pending():
    registry = CubeRegistry(FakeSource(row("c1")), LocalOpener({}))
    assert registry.refresh() == []
    health = registry.health()
    assert health["pending"] == ["c1"]
    assert health["errors"] == {}
    assert registry.get("c1") is None


def test_s3_opener_refuses_a_non_s3_prefix():
    with pytest.raises(UnsupportedSourceError):
        S3RepoOpener(SETTINGS).open(row("a", prefixes=("file:///etc/",)))


def test_one_broken_sink_does_not_block_others(c1):
    s3 = S3RepoOpener(SETTINGS)
    local = LocalOpener({"c1": c1})

    class Both:
        def open(self, sink):
            return s3.open(sink) if sink.cube_collection_id == "a" else local.open(sink)

    source = FakeSource(row("a", prefixes=("file:///etc/",)), row("c1"))
    registry = CubeRegistry(source, Both())
    assert registry.refresh() == ["c1"]
    assert registry.get("c1") is not None
    assert "a" in registry.health()["errors"]
    assert registry.get("a") is None


def test_failed_reopen_keeps_previous_dataset(c1):
    opener = LocalOpener({"c1": c1})
    registry = CubeRegistry(FakeSource(row("c1")), opener)
    registry.refresh()
    first = registry.get("c1")
    opener.error = RuntimeError("store down")
    assert registry.refresh() == []
    assert registry.get("c1") is first
    assert registry.health()["errors"]["c1"] == "RuntimeError"
    opener.error = None
    registry.refresh()
    assert registry.health()["errors"] == {}


def test_db_error_keeps_open_datasets_and_records_it(c1):
    source = FakeSource(row("c1"))
    registry = CubeRegistry(source, LocalOpener({"c1": c1}))
    registry.refresh()
    first = registry.get("c1")
    source.error = psycopg.errors.UndefinedTable('relation "stac_higher.cube_sinks" does not exist')
    assert registry.refresh() == []
    assert registry.get("c1") is first
    assert "UndefinedTable" in registry.health()["last_reload_error"]
    source.error = None
    registry.refresh()
    assert registry.health()["last_reload_error"] is None


def test_health_never_echoes_error_text(c1):
    """/health is anonymous: error text (DSNs, hosts, paths) stays in the logs."""
    source = FakeSource(row("c1"))
    opener = LocalOpener({"c1": c1})
    registry = CubeRegistry(source, opener)
    opener.error = RuntimeError("store at http://internal-host:9000/secret-path is down")
    registry.refresh()
    source.error = psycopg.OperationalError('connection to "database" (10.0.0.5) failed')
    registry.refresh()
    text = repr(registry.health())
    assert "internal-host" not in text
    assert "10.0.0.5" not in text
    assert registry.health()["last_reload_error"] == "OperationalError"


def test_served_geostationary_cube_is_in_metres(c1):
    """decode_coords="all" moves grid_mapping into .encoding: the rescale must
    still find it on the real open path (review finding, Critical #1)."""
    registry = CubeRegistry(FakeSource(row("c1")), LocalOpener({"c1": c1}))
    registry.refresh()
    ds = registry.get("c1")
    assert ds.x.attrs["units"] == "m"
    assert "goes_imager_projection" in ds.variables


def test_empty_source_prefixes_is_pending_not_served(c1):
    registry = CubeRegistry(FakeSource(row("c1", prefixes=())), LocalOpener({"c1": c1}))
    assert registry.refresh() == []
    assert registry.get("c1") is None
    health = registry.health()
    assert health["pending"] == ["c1"]
    assert health["errors"] == {}


def test_prefixes_recorded_later_open_the_cube(c1):
    source = FakeSource(row("c1", prefixes=()))
    registry = CubeRegistry(source, LocalOpener({"c1": c1}))
    registry.refresh()
    source.rows = [row("c1")]
    assert registry.refresh() == ["c1"]
    assert registry.health()["pending"] == []


def test_prefixes_lost_after_open_unserves_the_cube(c1):
    source = FakeSource(row("c1"))
    registry = CubeRegistry(source, LocalOpener({"c1": c1}))
    registry.refresh()
    source.rows = [row("c1", prefixes=())]
    registry.refresh()
    assert registry.get("c1") is None
    assert registry.health()["pending"] == ["c1"]


def test_s3_opener_returns_none_for_empty_prefixes():
    # no repository lookup at all: the store is unreachable in this test
    sink = row("c1", prefixes=())
    assert S3RepoOpener(SETTINGS).open(sink) is None


def test_rejected_config_keeps_the_open_dataset_and_reports_the_error(c1):
    source = FakeSource(row("c1"))
    registry = CubeRegistry(source, LocalOpener({"c1": c1}))
    registry.refresh()
    first = registry.get("c1")
    source.rows = [BadSink(id="sink-c1", cube_collection_id="c1", error="InvalidSinkConfig")]
    assert registry.refresh() == []
    assert registry.get("c1") is first
    assert registry.health()["errors"] == {"c1": "InvalidSinkConfig"}
    source.rows = [row("c1")]
    registry.refresh()
    assert registry.health()["errors"] == {}


def test_bare_s3_prefix_is_refused():
    from stac_higher_cube_server.registry import check_source_prefixes

    for bad in ("s3://", "s3:///", "s3://noaa-goes19", "gs://b/", "https://x/"):
        with pytest.raises(UnsupportedSourceError):
            check_source_prefixes((bad,))
    check_source_prefixes(("s3://noaa-goes19/", "s3://noaa-goes19/ABI-L2-CMIPC/"))


def test_a_cube_with_none_of_its_variables_is_an_error_not_healthy(c1):
    source = FakeSource(row("c1", variables=("NOPE",)))
    registry = CubeRegistry(source, LocalOpener({"c1": c1}))
    assert registry.refresh() == []
    assert registry.get("c1") is None
    assert registry.health()["errors"] == {"c1": "ValueError"}
    assert registry.health()["datasets"] == {}


def test_a_removed_sink_is_forgotten_by_the_opener(c1):
    class Opener(LocalOpener):
        def __init__(self, cubes):
            super().__init__(cubes)
            self.forgotten: list[str] = []

        def forget(self, cid):
            self.forgotten.append(cid)

    source = FakeSource(row("c1"))
    opener = Opener({"c1": c1})
    registry = CubeRegistry(source, opener)
    registry.refresh()
    source.rows = []
    registry.refresh()
    assert opener.forgotten == ["c1"]


def test_s3_opener_forget_prunes_its_cache():
    opener = S3RepoOpener(SETTINGS)
    opener._cache["c1"] = (("s3://b/",), object())
    opener.forget("c1")
    opener.forget("c1")
    assert opener._cache == {}


def test_s3_opener_checks_exists_on_a_cache_hit(monkeypatch):
    """A re-created (or deleted) cube must not reuse a stale cached handle."""
    import icechunk as ic

    opener = S3RepoOpener(SETTINGS)
    stale = object()
    opener._cache["c1"] = (("s3://noaa-goes19/",), stale)
    monkeypatch.setattr(ic.Repository, "exists", staticmethod(lambda storage: False))
    assert opener.open(row("c1")) is None
    assert opener._cache == {}


def test_refresh_swaps_the_entries_in_one_step(c1, tmp_path):
    """While a poll is still opening c2, readers see c1's old dataset: the new
    entries become visible together at the end of the poll (#123, item 12)."""
    c2 = make_local_cube(tmp_path / "c2", steps=1)
    source = FakeSource(row("c1"))
    cubes = {"c1": c1, "c2": c2}
    registry = CubeRegistry(source, LocalOpener(cubes))
    registry.refresh()
    old = registry.get("c1")
    c1.append(1)
    seen = []

    class Opener(LocalOpener):
        def open(self, sink):
            if sink.cube_collection_id == "c2":
                seen.append((registry.get("c1"), registry.ids()))
            return super().open(sink)

    registry._opener = Opener(cubes)
    source.rows = [row("c1"), row("c2")]
    registry.refresh()
    assert len(seen) == 1
    assert seen[0][0] is old and seen[0][1] == ["c1"]
    assert registry.get("c1") is not old
    assert registry.ids() == ["c1", "c2"]


def test_warm_up_result_racing_a_refresh_rebuild_is_neither_lost_nor_a_crash(c1):
    """record_warm_up runs on the event loop while refresh() rebuilds the
    warm-up errors in its worker thread. The hooked dict lets a warm-up result
    land in the middle of that rebuild; unguarded the rebuilt dict overwrites it
    (a stale or lost WarmUpFailed), and a size change would raise 'dictionary
    changed size during iteration' and lose the opened list."""
    import threading

    registry = CubeRegistry(FakeSource(row("c1")), LocalOpener({"c1": c1}))
    registry.refresh()
    writers: list[threading.Thread] = []

    class Hooked(dict):
        def items(self):
            snapshot = list(super().items())
            yield snapshot[0]
            writer = threading.Thread(target=registry.record_warm_up, args=("c1", 500))
            writers.append(writer)
            writer.start()
            writer.join(timeout=0.3)  # unguarded it finishes here; guarded it is blocked
            yield from snapshot[1:]

    registry._warm_up_errors = Hooked({"c1": "WarmUpFailed(None)", "gone": "WarmUpFailed(500)"})
    registry.refresh()  # must not raise
    writers[0].join(timeout=5)
    assert not writers[0].is_alive()
    assert registry.health()["errors"] == {"c1": "WarmUpFailed(500)"}
