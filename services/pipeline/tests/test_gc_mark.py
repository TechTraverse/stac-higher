"""The one asset_gc mark insert both Pg repositories share (Z-2, ADR 0022).

Every per-item mark (retention, the dispatcher's delete handler, the push
rollback) goes through ``insert_asset_gc_mark``, so the cube repository's
prefix is refused in one place.
"""

from __future__ import annotations

import pytest

from pipeline.gc.repo import insert_asset_gc_mark


class _Cursor:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _Conn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self.commits = 0

    async def execute(self, sql: str, params: tuple) -> _Cursor:
        self.executed.append((sql, params))
        return _Cursor(1)

    async def commit(self) -> None:
        self.commits += 1


async def test_inserts_an_ordinary_item_mark():
    conn = _Conn()
    created = await insert_asset_gc_mark(
        conn, "assets/c/item-1/", "c", "item-1", "item_delete", 7
    )
    assert created is True
    assert conn.executed[0][1] == ("assets/c/item-1/", "c", "item-1", "item_delete", 7)
    assert "ON CONFLICT (object_key) WHERE collected_at IS NULL DO NOTHING" in conn.executed[0][0]
    assert conn.commits == 1


async def test_inserts_a_whole_collection_mark():
    conn = _Conn()
    assert await insert_asset_gc_mark(conn, "assets/c/", "c", None, "collection_delete", 0)
    assert conn.executed[0][1][0] == "assets/c/"


@pytest.mark.parametrize(
    ("prefix", "item_id"),
    [
        ("assets/c/_cube/", "_cube"),  # the item id
        ("assets/c/_cube/", None),  # the prefix alone, whatever the caller says
        ("assets/c/_cube", "_cube"),  # no trailing slash
    ],
)
async def test_never_marks_the_cube_repository_prefix(prefix, item_id):
    conn = _Conn()
    assert await insert_asset_gc_mark(conn, prefix, "c", item_id, "retention", 0) is False
    assert conn.executed == []
