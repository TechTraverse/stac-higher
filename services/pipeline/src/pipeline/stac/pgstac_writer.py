"""pgstac data-access seam (ROADMAP §6.1 ITEMIZE).

`PgstacWriter` is the ABC ITEMIZE depends on (so it unit-tests against a fake).
`PgPgstacWriter` implements both operations ITEMIZE needs from pgstac: the item
upsert, and the collection-extent read backing the ISSUE I-27 geometry
fallback. Upsert wraps pypgstac's synchronous `Loader.load_items(...,
Methods.upsert)` in `asyncio.to_thread`. ADR 0001: upsert writes item DATA only
(temp `ON COMMIT DROP` staging tables + pgstac's own `upsert_item` functions —
no DDL, no migrations). A missing collection is a permanent error surfaced as
`CollectionMissing` (→ group failed); anything else propagates so the job retries.
"""

from __future__ import annotations

import abc
import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class CollectionMissing(Exception):
    """The item's `collection` does not exist in pgstac (create it first)."""


class PgstacWriter(abc.ABC):
    """The pgstac data-access seam ITEMIZE depends on: item upsert plus the
    collection-extent read used by the ISSUE I-27 geometry fallback."""

    @abc.abstractmethod
    async def upsert_items(self, items: Sequence[Mapping[str, Any]]) -> None:
        """Upsert STAC item dicts into pgstac, replacing by id."""

    @abc.abstractmethod
    async def get_collection_bbox(self, collection_id: str) -> list[float] | None:
        """The collection's overall extent bbox (``extent.spatial.bbox[0]``),
        or ``None`` if the collection/extent is absent. Backs the ISSUE I-27
        opt-in collection-extent geometry fallback (Slice B4a)."""


@dataclass
class PgPgstacWriter(PgstacWriter):
    dsn: str

    async def upsert_items(self, items: Sequence[Mapping[str, Any]]) -> None:
        try:
            await asyncio.to_thread(self._upsert_sync, list(items))
        except CollectionMissing:
            raise
        except Exception as exc:
            if "is not present in the database" in str(exc):
                raise CollectionMissing(str(exc)) from exc
            raise

    def _upsert_sync(self, items: list[Mapping[str, Any]]) -> None:  # pragma: no cover
        from pypgstac.db import PgstacDB
        from pypgstac.load import Loader, Methods

        with PgstacDB(dsn=self.dsn) as db:
            Loader(db=db).load_items(items, insert_mode=Methods.upsert)

    async def get_collection_bbox(  # pragma: no cover - thin psycopg wrapper
        self, collection_id: str
    ) -> list[float] | None:
        import psycopg

        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            cur = await conn.execute(
                "SELECT content->'extent'->'spatial'->'bbox'->0"
                " FROM pgstac.collections WHERE id = %s",
                (collection_id,),
            )
            row = await cur.fetchone()
        if row is None or row[0] is None:
            return None
        return [float(v) for v in row[0]]
