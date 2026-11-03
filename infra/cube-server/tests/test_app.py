import asyncio
import csv
import io
import time

import pytest
from fastapi.testclient import TestClient

from stac_higher_cube_server.app import create_app, warm_up
from stac_higher_cube_server.registry import CubeRegistry
from stac_higher_cube_server.settings import Settings
from stac_higher_cube_server.sinks import SinkRow
from tests.cubes import KANSAS, FakeSource, LocalOpener, iso_ns, make_local_cube, t_ns

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


def _rows(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def test_position_returns_null_for_missing_step(cube, client):
    cube.delete_source(1)
    r = client.get(POSITION, params=POINT)
    assert r.status_code == 200, r.text
    assert r.headers["x-cube-missing-steps"] == "1"
    rows = _rows(r.text)
    assert len(rows) == 3
    assert rows[0]["CMI"] != ""
    assert rows[1]["CMI"] == ""
    assert rows[2]["CMI"] != ""


def test_position_without_missing_steps_says_zero(client):
    r = client.get(POSITION, params=POINT)
    assert r.status_code == 200, r.text
    assert r.headers["x-cube-missing-steps"] == "0"
    assert all(row["CMI"] != "" for row in _rows(r.text))


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


def test_lifespan_polls_and_serves(cube):
    registry = CubeRegistry(FakeSource(SINK), LocalOpener({"c1": cube}))
    with TestClient(create_app(registry, SETTINGS)) as client:
        deadline = time.monotonic() + 10
        while registry.get("c1") is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert client.get(TILE, params={**Q, "t": iso_ns(t_ns(0))}).status_code == 200
