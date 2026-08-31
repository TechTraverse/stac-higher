"""The `process_run` producer hooks (M5-D, ADR 0014).

What is pinned here is the seam's promise — a process request takes the same
step path as a push — and the rejection semantics I-80 warns must NOT be
copied from push.
"""

from __future__ import annotations

import json

import pytest

from _process_fake import FakeProcessRepo
from pipeline.finalize.process_run import (
    REASON_INVALID_ITEM,
    REASON_MISSING_ASSET,
    REASON_WRONG_COLLECTION,
    ProcessRunRecorder,
    ProcessRunResolver,
    build_process_request,
)
from pipeline.finalize.seam import (
    PRODUCER_PROCESS_RUN,
    REF_STAGED,
    FinalizeOutcome,
    FinalizeResult,
    ItemRef,
    RejectedItem,
    UpsertedItem,
)
from pipeline.storage.keys import run_staging_prefix

RUN = "11111111-1111-4111-8111-111111111111"
PREFIX = run_staging_prefix(RUN)
OUT = ("cloud-masks",)


class FakeStore:
    """Just enough ObjectStore for the resolver."""

    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects

    def list_keys(self, prefix: str) -> list[str]:
        return [k for k in self.objects if k.startswith(prefix)]

    def get(self, key: str) -> bytes:
        return self.objects[key]

    def head(self, key):  # pragma: no cover - unused here
        return None

    def copy(self, src, dest):  # pragma: no cover
        return None

    def delete(self, key):  # pragma: no cover
        return None


def item_doc(item_id="i1", collection="cloud-masks", assets=None):
    from _finalize_fake import valid_item

    return json.dumps(
        valid_item(
            item_id=item_id,
            collection=collection,
            assets=assets if assets is not None else {"data": {"href": "mask.tif"}},
        )
    ).encode()


async def resolve(objects):
    resolver = ProcessRunResolver(store=FakeStore(objects))
    return await resolver.resolve(build_process_request(RUN, OUT))


# ---------------------------------------------------------------------------
# the request
# ---------------------------------------------------------------------------


def test_the_request_names_the_process_producer_and_the_run_prefix():
    req = build_process_request(RUN, OUT)
    assert req.producer == PRODUCER_PROCESS_RUN
    assert req.staging_prefix == f"staging/runs/{RUN}/"
    # Outputs are DISCOVERED, not declared: unlike a push the caller cannot
    # know what user code wrote.
    assert req.items == ()
    assert req.provenance == {"run_id": RUN}


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_item_document_and_its_sibling_asset_resolve():
    resolution = await resolve(
        {f"{PREFIX}i1.json": item_doc(), f"{PREFIX}mask.tif": b"bytes"}
    )
    assert len(resolution.items) == 1
    resolved = resolution.items[0]
    assert resolved.ref.kind == REF_STAGED
    assert resolved.ref.collection_id == "cloud-masks"
    assert resolved.ref.item_id == "i1"
    assert resolved.staged_assets[0].staging_key == f"{PREFIX}mask.tif"


@pytest.mark.asyncio
async def test_a_run_that_wrote_nothing_is_a_no_op_not_a_rejection():
    """A filter process legitimately emits nothing for a batch."""
    resolution = await resolve({})
    assert resolution.skip == "no_outputs"
    assert resolution.rejected == ()


@pytest.mark.asyncio
async def test_an_item_naming_a_collection_the_process_cannot_publish_to_is_refused():
    """Otherwise a run could write into any collection just by naming it."""
    resolution = await resolve({f"{PREFIX}i1.json": item_doc(collection="somebody-elses")})
    assert resolution.items == ()
    assert resolution.rejected[0].reason == REASON_WRONG_COLLECTION


@pytest.mark.asyncio
async def test_an_item_referencing_bytes_the_run_never_wrote_is_refused():
    """Publishing it would put a broken asset href in the catalog."""
    resolution = await resolve({f"{PREFIX}i1.json": item_doc()})
    assert resolution.rejected[0].reason == REASON_MISSING_ASSET


@pytest.mark.asyncio
async def test_absolute_hrefs_pass_through_untouched():
    """Reference-style outputs point at bytes we do not own and must not
    move."""
    doc = item_doc(assets={"data": {"href": "https://example.com/a.tif"}})
    resolution = await resolve({f"{PREFIX}i1.json": doc})
    assert len(resolution.items) == 1
    assert resolution.items[0].staged_assets == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "href", ["../../escape.tif", "nested/a.tif", "/etc/passwd", "..\\\\win.tif"]
)
async def test_an_href_reaching_outside_the_run_prefix_is_never_moved(href):
    """A process must not be able to name bytes outside its own prefix."""
    doc = item_doc(assets={"data": {"href": href}})
    resolution = await resolve({f"{PREFIX}i1.json": doc, f"{PREFIX}escape.tif": b"x"})
    # Either treated as external (not ours to move) — never resolved to a key
    # outside the prefix.
    for resolved in resolution.items:
        for asset in resolved.staged_assets:
            assert asset.staging_key.startswith(PREFIX)


@pytest.mark.asyncio
async def test_unreadable_or_shapeless_documents_are_rejected_individually():
    resolution = await resolve(
        {
            f"{PREFIX}bad.json": b"{not json",
            f"{PREFIX}noid.json": json.dumps({"collection": "cloud-masks"}).encode(),
            f"{PREFIX}ok.json": item_doc(assets={}),
        }
    )
    assert len(resolution.items) == 1
    assert {r.reason for r in resolution.rejected} == {REASON_INVALID_ITEM}


@pytest.mark.asyncio
async def test_one_bad_item_does_not_sink_its_siblings():
    resolution = await resolve(
        {
            f"{PREFIX}good.json": item_doc(item_id="good", assets={}),
            f"{PREFIX}bad.json": item_doc(item_id="bad", collection="elsewhere"),
        }
    )
    assert [i.ref.item_id for i in resolution.items] == ["good"]
    assert len(resolution.rejected) == 1


# ---------------------------------------------------------------------------
# recording — the I-80 divergence from push
# ---------------------------------------------------------------------------


def outcome(upserted=(), rejected=()):
    return FinalizeOutcome(
        result=FinalizeResult(upserted=upserted, rejected=rejected),
        reports=(),
        claimed=True,
    )


@pytest.mark.asyncio
async def test_published_items_land_on_the_run_row():
    repo = FakeProcessRepo()
    await ProcessRunRecorder(repo=repo).record(
        build_process_request(RUN, OUT),
        outcome(upserted=(UpsertedItem("cloud-masks", "i1"),)),
    )
    finished = repo.finished[0]
    assert finished["status"] == "succeeded"


@pytest.mark.asyncio
async def test_a_run_whose_every_output_was_rejected_is_not_a_success():
    """The container exited 0, but nothing reached the catalog. Calling that
    success would make flow_stats report a healthy flow that publishes
    nothing."""
    repo = FakeProcessRepo()
    await ProcessRunRecorder(repo=repo).record(
        build_process_request(RUN, OUT),
        outcome(
            rejected=(
                RejectedItem(
                    item_ref=ItemRef(REF_STAGED, "cloud-masks", "i1"),
                    reason=REASON_INVALID_ITEM,
                ),
            )
        ),
    )
    assert repo.finished[0]["status"] == "dead"


@pytest.mark.asyncio
async def test_a_partial_success_stays_succeeded_but_says_what_was_lost():
    repo = FakeProcessRepo()
    await ProcessRunRecorder(repo=repo).record(
        build_process_request(RUN, OUT),
        outcome(
            upserted=(UpsertedItem("cloud-masks", "i1"),),
            rejected=(
                RejectedItem(
                    item_ref=ItemRef(REF_STAGED, "cloud-masks", "i2"),
                    reason=REASON_INVALID_ITEM,
                ),
            ),
        ),
    )
    finished = repo.finished[0]
    assert finished["status"] == "succeeded"
    assert "1 of 2" in finished["error"]


@pytest.mark.asyncio
async def test_the_recorder_never_deletes_or_restores_anything():
    """I-80: push's tiers delete a rejected item or restore a snapshot,
    because push finalizes something ALREADY in pgstac. A process output is
    not in the catalog until the steps upsert it, so a rejection is simply a
    non-publish — copying push's delete would let a bad run destroy a
    pre-existing item that merely shares an id."""
    repo = FakeProcessRepo()
    recorder = ProcessRunRecorder(repo=repo)
    await recorder.record(
        build_process_request(RUN, OUT),
        outcome(
            rejected=(
                RejectedItem(
                    item_ref=ItemRef(REF_STAGED, "cloud-masks", "i1"),
                    reason=REASON_INVALID_ITEM,
                ),
            )
        ),
    )
    # The recorder's ONLY write is the run row: no pgstac writer is even a
    # dependency of it, which is the structural version of this guarantee.
    assert len(repo.finished) == 1
    assert not hasattr(recorder, "writer")


# ---------------------------------------------------------------------------
# the ADR 0014 check criterion, closed with the REAL hooks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_real_process_hooks_route_through_the_unchanged_steps():
    """test_finalize_seam.py proved the criterion with a STAND-IN producer
    layer. This proves it with the shipped one: the same neutral steps move
    bytes, rewrite hrefs and upsert for a process run exactly as for a push,
    with no step-side branching on producer."""
    from _finalize_fake import FakeObjectStore, FakeWriter
    from pipeline.finalize.seam import ProducerHooks
    from pipeline.finalize.steps import run_finalize

    class StubPreflight:
        async def collection_archived(self, collection_id: str) -> bool:
            return False

        async def has_open_gc_mark(self, prefix: str) -> bool:
            return False

    store = FakeObjectStore(
        objects={
            f"{PREFIX}i1.json": item_doc(),
            f"{PREFIX}mask.tif": b"mask-bytes",
        }
    )
    writer = FakeWriter()
    repo = FakeProcessRepo()
    hooks = {
        PRODUCER_PROCESS_RUN: ProducerHooks(
            resolver=ProcessRunResolver(store=store),
            recorder=ProcessRunRecorder(repo=repo),
        )
    }

    result = await run_finalize(
        build_process_request(RUN, OUT),
        hooks=hooks,
        preflight=StubPreflight(),
        store=store,
        writer=writer,
        asset_href_base="/api/assets",
    )

    assert [(u.collection_id, u.item_id) for u in result.upserted] == [
        ("cloud-masks", "i1")
    ]
    # The bytes moved staging → canonical under the item's own prefix...
    assert any(
        dest == "assets/cloud-masks/i1/mask.tif" for _src, dest in store.copied
    )
    # ...and the published document points at the asset SERVICE, never at
    # storage (the §5.3 invariant, unchanged for this producer).
    published = writer.upserted[0]
    assert published["assets"]["data"]["href"] == "/api/assets/cloud-masks/i1/mask.tif"
    # And the run row records what was published.
    assert repo.finished[0]["status"] == "succeeded"
