"""Which reference-mode ingest association produced an href (ADR 0018).

A reference-mode item's source href is the source object's stable URL, built
by the producing connection's adapter (``public_object_url``). The enabled
``storage_mode: reference`` association whose connection's object-URL base
prefixes the href owns it, and its credentials, anonymity and egress policy
come with it. Shared by process-run staging (``process/staging.py``) and the
cube writer (``cubes/resolve.py``).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import unquote

from pipeline.connections.adapters.base import StorageAdapter
from pipeline.connections.build import AdapterBuildError, build_adapter
from pipeline.ingest.repo import IngestAssociation


@dataclass(frozen=True)
class SourceMatch:
    association: IngestAssociation
    adapter: StorageAdapter
    #: the object key under the adapter's base URL, percent-decoded
    key: str


def association_for_href(
    href: str,
    associations: Iterable[IngestAssociation],
    master_key: bytes,
    allow_hosts: frozenset[str],
) -> SourceMatch | None:
    """The first reference association whose object-URL base prefixes
    ``href``, or ``None``. An association whose adapter cannot be built is
    passed over, as is any protocol without stable object URLs."""
    for assoc in associations:
        if (assoc.config or {}).get("storage_mode") != "reference":
            continue
        try:
            adapter = build_adapter(assoc.connection, master_key, allow_hosts)
        except AdapterBuildError:
            continue
        try:
            base = adapter.public_object_url("")
        except NotImplementedError:
            # Only S3 publishes stable object URLs (reference mode is
            # s3-only); other protocols cannot have produced the href.
            continue
        if href.startswith(base):
            return SourceMatch(assoc, adapter, unquote(href[len(base) :]))
    return None
