"""The producer half of tests/contract-fixtures/cube-maintenance-summary.json.

``last_maintenance`` is written by ``run_cube_maintain`` + ``run_cube_measure``
and has no pipeline-side reader, so the only cross-runtime check that can fail
is on what the pipeline writes: each named scenario below is driven through the
real code (the ``Cube`` harness of ``test_cube_maintain.py``) and its summary
must have the same key tree, the same JSON types and the same enum values as
the fixture's document of that name. The app's reader (vitest) parses each
document back to itself.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from _cube_sources import scan
from pipeline.cubes.maintain import run_cube_maintain, run_cube_measure
from test_cube_maintain import Cube

_FIXTURE_PATH = (
    Path(__file__).resolve().parents[3] / "tests/contract-fixtures/cube-maintenance-summary.json"
)
MAINTENANCE_FIXTURE = json.loads(_FIXTURE_PATH.read_text())


async def _windowed(tmp: Path) -> dict:
    cube = Cube(tmp, window={"max_steps": 3})
    cube.append(0, 1, 2, 3, 4)
    return await cube.run()


async def _windowless(tmp: Path) -> dict:
    cube = Cube(tmp, window=None)
    cube.append(0, 1, 2, 3)
    return await cube.run()


async def _no_repository(tmp: Path) -> dict:
    cube = Cube(tmp, window={"max_steps": 3})
    return await cube.run()


async def _failed(tmp: Path) -> dict:
    cube = Cube(tmp, window=None)
    cube.append(0)
    deps = cube.deps()

    def boom(sink):
        raise ConnectionError("platform bucket unreachable")

    deps.storage_for = boom
    with pytest.raises(ConnectionError):
        await run_cube_maintain("s1", deps)
    return cube.sink.last_maintenance


async def _publish_failed(tmp: Path) -> dict:
    cube = Cube(tmp, window={"max_age": "30m"})
    cube.append(0, 1, 2, 3, now=scan(3))

    async def broken(sink, config, result):
        raise TimeoutError("canceling statement due to lock timeout")

    deps = cube.deps(now=scan(3) + dt.timedelta(minutes=25))
    deps.after_batch = broken
    await run_cube_maintain("s1", deps)
    return cube.sink.last_maintenance


async def _measure_failed(tmp: Path) -> dict:
    cube = Cube(tmp, window=None)
    cube.append(0)
    await run_cube_maintain("s1", cube.deps())

    def broken(sink):
        raise OSError("listing refused")

    deps = cube.measure_deps()
    deps.list_objects = broken
    await run_cube_measure(cube.measure_queue.pop(0), deps)
    return cube.sink.last_maintenance


MAINTENANCE_SCENARIOS = {
    "windowed": _windowed,
    "windowless": _windowless,
    "no_repository": _no_repository,
    "failed": _failed,
    "publish_failed": _publish_failed,
    "measure_failed": _measure_failed,
}
#: values compared exactly; everything else by JSON type only
EXACT_KEYS = (
    "status", "attention", "window", "repository", "healed", "published",
    "publish_outcome", "trimmed", "ledger_pruned", "warn_bytes",
)  # fmt: skip


def _shape(value):
    """The key tree with each leaf replaced by its JSON type (bool before int)."""
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in value.items()}
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    return {int: "int", float: "number", str: "str", list: "list"}[type(value)]


def test_every_maintenance_document_has_a_scenario():
    assert set(MAINTENANCE_SCENARIOS) == set(MAINTENANCE_FIXTURE["documents"])


@pytest.mark.parametrize("name", sorted(MAINTENANCE_SCENARIOS))
async def test_the_pipeline_writes_the_documented_maintenance_shape(tmp_path, name):
    produced = await MAINTENANCE_SCENARIOS[name](tmp_path)
    golden = MAINTENANCE_FIXTURE["documents"][name]
    assert _shape(produced) == _shape(golden)
    for key in EXACT_KEYS:
        assert produced.get(key, "<absent>") == golden.get(key, "<absent>"), key
