"""The enabled cube sinks, read through the read-only role (spec §8.1, migration 033).

``config`` is the cross-runtime shape pinned by
``tests/contract-fixtures/cube-sink-config.json``. The server is a third,
LENIENT reader like the pipeline's: it needs only ``append_dim`` and
``variables``, ignores every other key and de-duplicates names.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import psycopg

log = logging.getLogger(__name__)

#: exactly the columns migration 033 grants to stac_higher_cube_reader
ENABLED_SINKS_SQL = (
    "SELECT id::text, cube_collection_id, config, source_prefixes "
    "FROM stac_higher.cube_sinks WHERE enabled ORDER BY cube_collection_id"
)


@dataclass(frozen=True)
class SinkRow:
    id: str
    cube_collection_id: str
    append_dim: str
    variables: tuple[str, ...]
    source_prefixes: tuple[str, ...]


def parse_sink_config(config: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
    """``(append_dim, variables)``; ``ValueError`` when either is unusable."""
    if not isinstance(config, Mapping):
        raise ValueError("cube sink config must be an object")
    append_dim = config.get("append_dim")
    if not isinstance(append_dim, str) or not append_dim.strip():
        raise ValueError("append_dim is required")
    raw = config.get("variables")
    if not isinstance(raw, list) or not raw:
        raise ValueError("variables must be a non-empty list")
    names: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("variables entries must be non-blank strings")
        if value.strip() not in names:
            names.append(value.strip())
    return append_dim.strip(), tuple(names)


class PgSinkSource:
    """One short-lived autocommit connection per poll: 15 s apart, no pool needed."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def enabled_sinks(self) -> list[SinkRow]:
        with psycopg.connect(self._dsn, autocommit=True, connect_timeout=5) as conn:
            rows = conn.execute(ENABLED_SINKS_SQL).fetchall()
        sinks: list[SinkRow] = []
        for sink_id, collection_id, config, prefixes in rows:
            try:
                append_dim, variables = parse_sink_config(config)
            except ValueError as err:
                log.warning(
                    "cube sink config unusable; skipped",
                    extra={"cube_sink_id": sink_id, "cube_collection_id": collection_id,
                           "error": str(err)},
                )
                continue
            sinks.append(
                SinkRow(
                    id=sink_id,
                    cube_collection_id=collection_id,
                    append_dim=append_dim,
                    variables=variables,
                    source_prefixes=tuple(prefixes or ()),
                )
            )
        return sinks
