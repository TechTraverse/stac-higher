import psycopg
import pytest

from stac_higher_cube_server.registry import CubeRegistry, S3RepoOpener, UnsupportedSourceError
from stac_higher_cube_server.settings import Settings
from stac_higher_cube_server.sinks import SinkRow
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
