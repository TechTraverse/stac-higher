import asyncio
import csv
import io
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from stac_higher_cube_server.app import create_app, warm_up
from stac_higher_cube_server.registry import CubeRegistry
from stac_higher_cube_server.settings import Settings
from stac_higher_cube_server.sinks import SinkRow
from tests.cubes import (
    KANSAS,
    FakeSource,
    LocalOpener,
    iso_ns,
    make_local_cube,
    step_values,
    t_ns,
)

SETTINGS = Settings(
    db_dsn="postgresql://unused",
    s3_endpoint="http://127.0.0.1:9",
    s3_access_key_id="k",
    s3_secret_access_key="s",
    platform_bucket="stac-higher",
    poll_seconds=0.05,
)
SINK = SinkRow(id="sink-c1", cube_collection_id="c1", append_dim="t", variables=("CMI",),
               source_prefixes=("s3://noaa-goes19/",))
#: z6 tile over Kansas, in the {z}/{y}/{x} order the TMS routes use
TILE = "/datasets/c1/tiles/WebMercatorQuad/6/24/14"
Q = {"variables": "CMI", "style": "raster/gray", "colorscalerange": "190,310",
     "width": "256", "height": "256", "f": "png"}
POSITION = "/datasets/c1/edr/position"
POINT = {"coords": f"POINT({KANSAS[0]} {KANSAS[1]})", "parameter-name": "CMI", "f": "csv"}


@pytest.fixture
def cube(tmp_path):
    return make_local_cube(tmp_path, steps=3)


@pytest.fixture
def registry(cube):
    registry = CubeRegistry(FakeSource(SINK), LocalOpener({"c1": cube}))
    registry.refresh()
    return registry


@pytest.fixture
def client(registry):
    return TestClient(create_app(registry, SETTINGS, poll=False))


def test_unregistered_id_is_404(client):
    r = client.get("/datasets/nope/tiles/WebMercatorQuad/6/24/14", params=Q)
    assert r.status_code == 404


def test_pinned_tile_is_immutable(client):
    r = client.get(TILE, params={**Q, "t": iso_ns(t_ns(0))})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert "immutable" in r.headers["cache-control"]


def test_unpinned_tile_is_short_lived(client):
    r = client.get(TILE, params=Q)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "public, max-age=60"


def test_unknown_exact_t_is_not_cached_immutably(client):
    r = client.get(TILE, params={**Q, "t": "2000-01-01T00:00:00.000000000"})
    assert 400 <= r.status_code < 500, (r.status_code, r.text)
    assert r.headers["cache-control"] == "no-store"


def writer_t(step: int) -> str:
    """``t`` exactly as pipeline/cubes/collection.py::time_strings writes
    ``stac_higher:time_values``: nanosecond ISO plus a trailing ``Z``."""
    ns = np.datetime_as_string(np.array([t_ns(step)], dtype="datetime64[ns]"), unit="ns")
    return f"{ns[0]}Z"


def test_tile_accepts_the_writers_exact_t_with_z(client):
    t = writer_t(1)
    assert t.endswith("Z") and "." in t
    r = client.get(TILE, params={**Q, "t": t})
    assert r.status_code == 200, r.text
    assert "immutable" in r.headers["cache-control"]


def test_position_accepts_the_writers_exact_t_with_z(client):
    r = client.get(POSITION, params={**POINT, "t": writer_t(1)})
    assert r.status_code == 200, r.text
    rows = _rows(r.text)
    assert len(rows) == 1
    assert float(rows[0]["CMI"]) == pytest.approx(float(step_values(1)[3, 4]))


def test_position_linear_is_a_422(client):
    r = client.get(POSITION, params={**POINT, "method": "linear"})
    assert r.status_code == 422, r.text
    assert "nearest" in r.json()["detail"]


def test_position_linear_on_a_missing_step_is_not_a_500(cube, client):
    cube.delete_source(1)
    assert client.get(POSITION, params={**POINT, "method": "linear"}).status_code == 422


def test_position_nearest_is_unchanged(client):
    assert client.get(POSITION, params={**POINT, "method": "nearest"}).status_code == 200


def test_warm_up_failure_lands_in_health_and_clears(cube, registry):
    app = create_app(registry, SETTINGS, poll=False)
    status = asyncio.run(warm_up(app, "nope", registry.get("c1")))
    assert status == 404
    registry.record_warm_up("nope", status)
    assert registry.health()["errors"] == {"nope": "WarmUpFailed(404)"}
    registry.record_warm_up("nope", None)
    assert registry.health()["errors"] == {"nope": "WarmUpFailed(None)"}
    registry.record_warm_up("nope", 200)
    assert registry.health()["errors"] == {}


def test_a_failed_warm_up_survives_the_next_unchanged_poll(registry):
    registry.record_warm_up("c1", 500)
    registry.refresh()  # unchanged snapshot: nothing is reopened
    assert registry.health()["errors"] == {"c1": "WarmUpFailed(500)"}
    registry.record_warm_up("c1", 200)
    assert registry.health()["errors"] == {}


def test_poll_records_a_failing_warm_up(cube, monkeypatch):
    import stac_higher_cube_server.app as app_mod

    async def failing(app, cid, ds):
        return 500

    monkeypatch.setattr(app_mod, "warm_up", failing)
    registry = CubeRegistry(FakeSource(SINK), LocalOpener({"c1": cube}))
    with TestClient(create_app(registry, SETTINGS)) as client:
        deadline = time.monotonic() + 10
        while not registry.health()["errors"] and time.monotonic() < deadline:
            time.sleep(0.05)
        assert client.get("/health").json()["errors"] == {"c1": "WarmUpFailed(500)"}


def _rows(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def test_position_returns_null_for_missing_step(cube, client):
    cube.delete_source(1)
    r = client.get(POSITION, params=POINT)
    assert r.status_code == 200, r.text
    assert r.headers["x-cube-missing-steps"] == "1"
    rows = _rows(r.text)
    assert len(rows) == 3
    # Kansas is the fixture grid's centre: row 3, column 4 of each step
    assert float(rows[0]["CMI"]) == pytest.approx(float(step_values(0)[3, 4]))
    assert rows[1]["CMI"] == ""
    assert rows[2]["CMI"] != ""


def test_position_without_missing_steps_says_zero(client):
    r = client.get(POSITION, params=POINT)
    assert r.status_code == 200, r.text
    assert r.headers["x-cube-missing-steps"] == "0"
    rows = _rows(r.text)
    assert [float(row["CMI"]) for row in rows] == pytest.approx(
        [float(step_values(i)[3, 4]) for i in range(3)]
    )


def test_position_is_get_only(client):
    r = client.post(POSITION, params=POINT, content="x,y\n0,0\n",
                    headers={"content-type": "text/csv"})
    assert r.status_code == 405


def test_cors_exposes_missing_steps_header(client):
    r = client.get(POSITION, params=POINT, headers={"Origin": "http://localhost:4321"})
    assert r.headers["access-control-allow-origin"] in ("*", "http://localhost:4321")
    assert "x-cube-missing-steps" in r.headers["access-control-expose-headers"].lower()


def test_health_reports_datasets_and_errors(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["datasets"]["c1"]["steps"] == 3
    assert body["last_reload_error"] is None


def test_reload_is_served_without_restart(cube, registry, client):
    cube.append(1)
    registry.refresh()
    r = client.get(TILE, params={**Q, "t": iso_ns(t_ns(3))})
    assert r.status_code == 200, r.text


def test_warm_up_renders_a_tile_and_never_raises(registry):
    app = create_app(registry, SETTINGS, poll=False)
    assert asyncio.run(warm_up(app, "c1", registry.get("c1"))) == 200
    assert asyncio.run(warm_up(app, "nope", registry.get("c1"))) == 404


def test_warm_up_renders_a_variable_without_valid_min_max(registry):
    # The real GOES CMI carries neither (Z-7 live check): xpublish-tiles then
    # needs an explicit colorscalerange, else every warm-up is a 422.
    ds = registry.get("c1")
    for attr in ("valid_min", "valid_max"):
        ds["CMI"].attrs.pop(attr)
    app = create_app(registry, SETTINGS, poll=False)
    assert asyncio.run(warm_up(app, "c1", ds)) == 200


def test_lifespan_polls_and_serves(cube):
    registry = CubeRegistry(FakeSource(SINK), LocalOpener({"c1": cube}))
    with TestClient(create_app(registry, SETTINGS)) as client:
        deadline = time.monotonic() + 10
        while registry.get("c1") is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert client.get(TILE, params={**Q, "t": iso_ns(t_ns(0))}).status_code == 200


def test_log_lines_carry_their_extra_fields():
    import logging

    from stac_higher_cube_server.app import ExtraFormatter

    record = logging.LogRecord("x", logging.WARNING, __file__, 1, "cube not reloaded", None, None)
    record.cube_collection_id = "c1"
    record.error = "RuntimeError: store down"
    line = ExtraFormatter("%(levelname)s %(message)s").format(record)
    assert line.startswith("WARNING cube not reloaded")
    assert '"cube_collection_id": "c1"' in line
    assert '"error": "RuntimeError: store down"' in line
