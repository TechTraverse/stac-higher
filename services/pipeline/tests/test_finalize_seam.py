"""The ADR 0014 check criterion, applied literally (Phase 7 spec §6.1, §15).

A ``FinalizeRequest`` naming ``process_run`` and a ``staging/runs/{id}/``
prefix must route through the SAME neutral steps with no changes to the step
code — all producer-specific logic lives in the resolver/recorder layers
registered against the producer value. These tests drive exactly that: a
process_run-shaped request with a minimal producer layer (a stand-in for
Phase 9's) finalizes through ``run_finalize`` — bytes moved, hrefs rewritten,
upsert restricted to the request's output collections — while the step module
itself is verified producer-blind at the source level.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field

import pytest

import pipeline.finalize.steps as steps_module
from _finalize_fake import FakeObjectStore, FakeWriter, valid_item
from pipeline.finalize.push import PushResolver
from pipeline.finalize.repo import FinalizeRepo
from pipeline.finalize.seam import (
    PRODUCER_PROCESS_RUN,
    REF_STAGED,
    FinalizeOutcome,
    FinalizeRequest,
    ItemRef,
    OutcomeRecorder,
    ProducerHooks,
    ProducerResolver,
    Resolution,
    ResolvedItem,
    StagedAsset,
)
from pipeline.finalize.steps import run_finalize


@dataclass
class StubPreflight:
    archived: set[str] = field(default_factory=set)
    marked: set[str] = field(default_factory=set)

    async def collection_archived(self, collection_id: str) -> bool:
        return collection_id in self.archived

    async def has_open_gc_mark(self, prefix: str) -> bool:
        return any(prefix.startswith(k) for k in self.marked)


@dataclass
class RunResolver(ProducerResolver):
    """A minimal process_run producer layer: resolves each ref to an item
    document whose staged assets live under the run's staging prefix. What
    Phase 9's real resolver will add (run-ledger claim, staged item-JSON
    loading) is producer detail — the seam only requires that the REQUEST
    shape and the steps stay neutral."""

    documents: dict[str, dict]
    staging_prefix: str

    async def resolve(self, req: FinalizeRequest) -> Resolution:
        resolved = []
        for ref in req.items:
            doc = self.documents[ref.item_id]
            staged = tuple(
                StagedAsset(
                    asset_key=key,
                    filename=entry["href"].rsplit("/", 1)[-1],
                    staging_key=f"{self.staging_prefix}{entry['href'].rsplit('/', 1)[-1]}",
                )
                for key, entry in doc.get("assets", {}).items()
                if entry["href"].startswith("staging://")
            )
            resolved.append(ResolvedItem(ref=ref, document=doc, staged_assets=staged))
        return Resolution(items=tuple(resolved), claimed=True)


@dataclass
class RunRecorder(OutcomeRecorder):
    recorded: list[FinalizeOutcome] = field(default_factory=list)

    async def record(self, req: FinalizeRequest, outcome: FinalizeOutcome) -> None:
        self.recorded.append(outcome)


def _process_run_request(run_id: str = "run-1") -> FinalizeRequest:
    return FinalizeRequest(
        producer=PRODUCER_PROCESS_RUN,
        staging_prefix=f"staging/runs/{run_id}/",
        output_collections=("derived-products",),
        items=(
            ItemRef(kind="pgstac", collection_id="derived-products", item_id="out-001"),
        ),
        provenance={"run_id": run_id},
    )


async def test_process_run_request_routes_through_the_same_steps():
    """The criterion itself: a process_run request with a different staging
    prefix finalizes through run_finalize unchanged."""
    doc = valid_item(
        "out-001",
        "derived-products",
        assets={"data": {"href": "staging://ignored/out.tif", "type": "image/tiff"}},
    )
    store = FakeObjectStore(objects={"staging/runs/run-1/out.tif": b"process-output"})
    writer = FakeWriter()
    recorder = RunRecorder()
    req = _process_run_request()
    hooks = {
        PRODUCER_PROCESS_RUN: ProducerHooks(
            resolver=RunResolver(documents={"out-001": doc}, staging_prefix=req.staging_prefix),
            recorder=recorder,
        )
    }

    result = await run_finalize(
        req, hooks=hooks, preflight=StubPreflight(), store=store, writer=writer
    )

    assert [(u.collection_id, u.item_id) for u in result.upserted] == [
        ("derived-products", "out-001")
    ]
    assert result.rejected == ()
    # the move ran against the RUN's staging prefix, not a push-shaped one
    assert store.copied == [
        ("staging/runs/run-1/out.tif", "assets/derived-products/out-001/out.tif")
    ]
    assert store.deleted == ["staging/runs/run-1/out.tif"]
    # rewrite + upsert: the landed document carries the canonical href
    assert writer.upserted[0]["assets"]["data"]["href"] == (
        "/api/assets/derived-products/out-001/out.tif"
    )
    # the producer's own recorder received the outcome
    assert len(recorder.recorded) == 1
    assert recorder.recorded[0].result == result


async def test_output_collections_restriction_is_neutral():
    """The upsert restriction comes from the request, not from any producer
    logic: a resolved item outside output_collections is rejected."""
    doc = valid_item("out-001", "some-other-collection")
    req = FinalizeRequest(
        producer=PRODUCER_PROCESS_RUN,
        staging_prefix="staging/runs/run-2/",
        output_collections=("derived-products",),
        items=(
            ItemRef(kind="pgstac", collection_id="some-other-collection", item_id="out-001"),
        ),
        provenance={"run_id": "run-2"},
    )
    writer = FakeWriter()
    recorder = RunRecorder()
    hooks = {
        PRODUCER_PROCESS_RUN: ProducerHooks(
            resolver=RunResolver(documents={"out-001": doc}, staging_prefix=req.staging_prefix),
            recorder=recorder,
        )
    }

    result = await run_finalize(
        req, hooks=hooks, preflight=StubPreflight(), store=FakeObjectStore(), writer=writer
    )

    assert result.upserted == ()
    assert result.rejected[0].reason == "wrong_collection"
    assert writer.upserted == []


async def test_unregistered_producer_is_a_clear_error():
    req = _process_run_request()
    with pytest.raises(ValueError, match="process_run"):
        await run_finalize(
            req, hooks={}, preflight=StubPreflight(), store=FakeObjectStore(), writer=FakeWriter()
        )


def test_steps_module_is_producer_blind():
    """The literal 'no producer branching in the steps' obligation: the step
    module never names a producer value and never imports a producer layer."""
    source = inspect.getsource(steps_module)
    assert "push_ingest" not in source
    assert "process_run" not in source
    assert "finalize.push" not in source
    assert "provenance" not in source  # producer-specific by design (R5)


async def test_staged_ref_kind_is_phase_9():
    """§6.1 honest scoping: the Phase 7 resolver refuses the ``staged`` ref
    kind with NotImplementedError — an interface gap, not a seam violation."""
    from _finalize_fake import FakeFinalizeRepo

    repo: FinalizeRepo = FakeFinalizeRepo()
    req = FinalizeRequest(
        producer="push_ingest",
        staging_prefix="staging/u1/",
        output_collections=("c",),
        items=(ItemRef(kind=REF_STAGED, collection_id="c", item_id="i", key="staging/u1/i.json"),),
        provenance={"upload_id": "u1", "event_op": "insert"},
    )
    with pytest.raises(NotImplementedError):
        await PushResolver(repo).resolve(req)
