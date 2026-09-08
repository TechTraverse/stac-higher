"""The finalize seam (ADR 0014, Phase 7 spec §6.1) — request/result shapes and
the per-producer hook interfaces.

The neutral steps (``steps.py``) contain **no producer branching**: producer
differences live only in the :class:`FinalizeRequest` (where staging is, which
ledger records the outcome, which collections are writable) plus two thin
per-producer layers registered against the ``producer`` value, OUTSIDE the
steps — a :class:`ProducerResolver` (load the item for each :class:`ItemRef`
and perform producer-specific admission/claiming) and an
:class:`OutcomeRecorder` (stamp the producer's ledger and apply its rejection
outcome). Check criterion (ADR 0014 / P9-B, tested literally in
``tests/test_finalize_seam.py``): a request naming ``process_run`` and
``staging/runs/{id}/`` must be acceptable with no changes to the step code.

``ItemRef.kind`` is orthogonal to producer: ``pgstac`` = the item is already
in the catalog (push ingest); ``staged`` = the item JSON document lives in
staging (Phase 9). Only the ``pgstac`` path is implemented in Phase 7 — a
``staged`` ref raises ``NotImplementedError`` in the Phase 7 resolver, which
is Phase 9's first task, not a seam violation (§6.1 honest scoping).
"""

from __future__ import annotations

import abc
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

PRODUCER_PUSH_INGEST = "push_ingest"
PRODUCER_PROCESS_RUN = "process_run"

#: ItemRef kinds (orthogonal to producer).
REF_PGSTAC = "pgstac"
REF_STAGED = "staged"


@dataclass(frozen=True)
class ItemRef:
    """One item to finalize. ``kind=pgstac``: the item is already in the
    catalog under (collection_id, item_id). ``kind=staged``: ``key`` names the
    item JSON document in staging (Phase 9; unresolved in Phase 7)."""

    kind: str
    collection_id: str
    item_id: str
    key: str | None = None


@dataclass(frozen=True)
class FinalizeRequest:
    """The ADR 0014 request. ``provenance`` is producer-specific by design
    (§6.1 residual R5): ``{upload_id, event_op}`` for push ingest,
    ``{run_id, input_items}`` for process runs — the steps never read it; only the
    producer's resolver/recorder do."""

    producer: str
    staging_prefix: str
    output_collections: tuple[str, ...]
    items: tuple[ItemRef, ...]
    provenance: Mapping[str, Any]


@dataclass(frozen=True)
class UpsertedItem:
    collection_id: str
    item_id: str


@dataclass(frozen=True)
class RejectedItem:
    item_ref: ItemRef
    reason: str
    detail: str = ""


@dataclass(frozen=True)
class FinalizeResult:
    upserted: tuple[UpsertedItem, ...] = ()
    rejected: tuple[RejectedItem, ...] = ()


@dataclass(frozen=True)
class StagedAsset:
    """One staged object referenced by an item's asset: where it sits in
    staging and which asset entry / filename it belongs to. The resolver
    parses these from the producer's staging contract; the steps derive the
    canonical key/href from the ref + filename."""

    asset_key: str
    filename: str
    staging_key: str


@dataclass(frozen=True)
class ResolvedItem:
    """A loaded item ready for the neutral steps."""

    ref: ItemRef
    document: dict[str, Any]
    staged_assets: tuple[StagedAsset, ...] = ()


@dataclass(frozen=True)
class Resolution:
    """What a resolver hands the steps. ``skip`` short-circuits the whole run
    as a structured no-op (the §6.3 claim-fail paths) — the recorder is NOT
    invoked and no ledger verdict is (re)written. ``claimed`` tells the
    recorder whether the producer's ledger row is owned by this run — an
    admission rejection on a row that was never claimed (unknown session,
    a session bound to another item) must apply its item outcome without
    stamping someone else's ledger row."""

    items: tuple[ResolvedItem, ...] = ()
    rejected: tuple[RejectedItem, ...] = ()
    skip: str | None = None
    skip_detail: str = ""
    claimed: bool = False


@dataclass(frozen=True)
class ItemReport:
    """Per-item step outcome for the recorder: terminal outcome plus what the
    steps actually did (checksums recorded, bytes moved, whether any canonical
    object was already written — the recorder's cue to GC-mark before a
    rejection delete)."""

    ref: ItemRef
    outcome: str  # "upserted" | "rejected"
    reason: str | None = None
    detail: str = ""
    checksums: Mapping[str, str] = field(default_factory=dict)
    bytes_moved: int = 0
    moved_any: bool = False


@dataclass(frozen=True)
class FinalizeOutcome:
    """The bundle the recorder receives."""

    result: FinalizeResult
    reports: tuple[ItemReport, ...]
    claimed: bool


class ProducerResolver(abc.ABC):
    """Producer-specific admission + item loading (bracket BEFORE the steps)."""

    @abc.abstractmethod
    async def resolve(self, req: FinalizeRequest) -> Resolution:
        """Claim the producer's ledger, admit or reject each ref, and load the
        admitted items. Violations of the producer's contract come back as
        ``rejected`` entries; claim failures as ``skip``."""


class OutcomeRecorder(abc.ABC):
    """Producer-specific outcome recording (bracket AFTER the steps)."""

    @abc.abstractmethod
    async def record(self, req: FinalizeRequest, outcome: FinalizeOutcome) -> None:
        """Stamp the producer's ledger row(s) and apply the producer's
        rejection outcome (for push: the §6.3 op-discriminated tiers)."""


@dataclass(frozen=True)
class ProducerHooks:
    """The pair registered against one ``producer`` value."""

    resolver: ProducerResolver
    recorder: OutcomeRecorder


class PreflightChecks(abc.ABC):
    """Producer-neutral platform safety re-checks the steps run at execution
    time (§6.1 step 1, defending the ADR 0011 / I-59 races)."""

    @abc.abstractmethod
    async def collection_archived(self, collection_id: str) -> bool:
        """True when the target collection is archived (missing settings row
        = not archived)."""

    @abc.abstractmethod
    async def has_open_gc_mark(self, prefix: str) -> bool:
        """True when an open ``asset_gc`` mark covers ``prefix`` (the mark's
        key equals it or is an ancestor of it)."""
