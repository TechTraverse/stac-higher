"""Z-2: the lenient cube sink config reader beyond the fixture cases."""

import pytest

from pipeline.cubes.config import CubeSinkConfigError, parse_cube_sink_config, parse_duration

BASE = {"append_dim": "t", "variables": ["CMI"], "loadable_variables": ["t"]}


@pytest.mark.parametrize(("text", "seconds"), [("90m", 5400), ("24h", 86400), ("30d", 2592000)])
def test_parse_duration(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "24", "1w", "1.5h", " 1h", "-1h", "0m", "31d"])
def test_parse_duration_rejects(text):
    with pytest.raises(CubeSinkConfigError):
        parse_duration(text)


def test_window_converts_to_seconds():
    cfg = parse_cube_sink_config({**BASE, "window": {"max_steps": 72, "max_age": "6h"}})
    assert cfg.window is not None
    assert cfg.window.max_steps == 72
    assert cfg.window.max_age_seconds == 21600


def test_duplicates_are_dropped_in_order():
    cfg = parse_cube_sink_config({**BASE, "variables": ["CMI", "DQF", "CMI"]})
    assert cfg.variables == ("CMI", "DQF")


def test_sixty_five_variables_rejected():
    with pytest.raises(CubeSinkConfigError):
        parse_cube_sink_config({**BASE, "variables": [f"v{i}" for i in range(65)]})


def test_bool_is_not_an_integer_step_count():
    with pytest.raises(CubeSinkConfigError):
        parse_cube_sink_config({**BASE, "window": {"max_steps": True}})


def test_not_an_object():
    with pytest.raises(CubeSinkConfigError):
        parse_cube_sink_config(["t"])


def test_dispatcher_skip_reason_is_in_the_contract():
    from pipeline.cubes.config import SKIP_REASONS
    from pipeline.cubes.repo import REASON_NO_DATETIME

    assert REASON_NO_DATETIME in SKIP_REASONS
