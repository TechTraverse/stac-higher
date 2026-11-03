from stac_higher_cube_server.caching import IMMUTABLE, SHORT, cache_control

TILE = "/datasets/c1/tiles/WebMercatorQuad/4/5/3"
T = "2026-10-03T17:02:36.714359936"


def test_exact_t_tile_is_immutable():
    assert cache_control(TILE, {"t": T}, 200) == IMMUTABLE
    assert IMMUTABLE == "public, max-age=31536000, immutable"


def test_nearest_t_tile_is_short_lived():
    assert cache_control(TILE, {"t": f"nearest::{T}"}, 200) == SHORT
    assert SHORT == "public, max-age=60"


def test_unpinned_tile_is_short_lived():
    assert cache_control(TILE, {}, 200) == SHORT


def test_failed_tile_is_never_stored():
    assert cache_control(TILE, {"t": T}, 404) == "no-store"
    assert cache_control(TILE, {"t": T}, 500) == "no-store"


def test_non_tile_routes_are_left_alone():
    assert cache_control("/datasets/c1/edr/position", {"t": T}, 200) is None
    assert cache_control("/datasets/c1/tiles/WebMercatorQuad", {}, 200) is None
    assert cache_control("/datasets/c1/tiles/WebMercatorQuad/tilejson.json", {}, 200) is None
    assert cache_control("/health", {}, 200) is None


def test_rendered_error_tile_is_never_stored():
    """render_errors=true draws an error as a 200 PNG: never cache it (review, Important #1)."""
    assert cache_control(TILE, {"t": T, "render_errors": "true"}, 200) == "no-store"
    assert cache_control(TILE, {"t": T, "render_errors": "True"}, 200) == "no-store"
    assert cache_control(TILE, {"t": T, "render_errors": "false"}, 200) == IMMUTABLE
