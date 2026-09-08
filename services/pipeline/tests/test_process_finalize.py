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
    assert req.provenance == {"run_id": RUN, "input_items": ()}


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
async def test_resolver_ignores_everything_under_inputs():
    """GOES spec §3.3: the platform stages a run's INPUTS under `inputs/`
    inside the same prefix — the manifest and any staged item documents are
    JSON, and none of them is an output."""
    from pipeline.storage.keys import run_inputs_prefix

    inputs = run_inputs_prefix(RUN)
    store = FakeStore(
        {
            f"{inputs}b1/manifest.json": b'{"version": 1, "items": []}',
            f"{inputs}b1/i1/src.json": item_doc(item_id="src", collection="cloud-masks"),
            f"{PREFIX}out.json": item_doc(item_id="out", assets={"data": {"href": "out.tif"}}),
            f"{PREFIX}out.tif": b"tif",
        }
    )
    resolution = await ProcessRunResolver(store=store).resolve(build_process_request(RUN, OUT))
    assert [i.ref.item_id for i in resolution.items] == ["out"]
    assert resolution.rejected == ()


@pytest.mark.asyncio
async def test_an_output_cannot_claim_an_input_file_as_its_asset():
    """`inputs/…` is a relative href with a separator: it is REFUSED (the
    item is rejected) rather than published as a dangling relative href —
    an output that needs the input bytes copies them (spec §3.3)."""
    from pipeline.storage.keys import run_inputs_prefix

    store = FakeStore(
        {
            f"{run_inputs_prefix(RUN)}b1/i1/x.nc": b"nc",
            f"{PREFIX}out.json": item_doc(
                item_id="out", assets={"data": {"href": "inputs/b1/i1/x.nc"}}
            ),
        }
    )
    resolution = await ProcessRunResolver(store=store).resolve(build_process_request(RUN, OUT))
    assert resolution.items == ()
    assert resolution.rejected[0].reason == REASON_MISSING_ASSET
    assert "inputs/b1/i1/x.nc" in resolution.rejected[0].detail


@pytest.mark.asyncio
@pytest.mark.parametrize("href", ["../../escape.tif", "nested/a.tif", "..\\win.tif"])
async def test_a_relative_href_that_is_not_a_plain_filename_rejects_the_item(href):
    """Publishing it verbatim would put a broken relative href in the catalog;
    root-relative and scheme'd hrefs (reference-style outputs) still pass."""
    doc = item_doc(assets={"data": {"href": href}})
    resolution = await resolve({f"{PREFIX}i1.json": doc, f"{PREFIX}escape.tif": b"x"})
    assert resolution.items == ()
    assert resolution.rejected[0].reason == REASON_MISSING_ASSET


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


# ---------------------------------------------------------------------------
# lineage (D-1): `derived_from` stamped from the run's input batch
# ---------------------------------------------------------------------------

SRC = "goes-abi-mcmipc"
REFS = (
    {"item_id": "scene-1", "collection_id": SRC, "op": "insert"},
    {"item_id": "scene-2", "collection_id": SRC, "op": "insert"},
)


def _manifest(items, skipped=None, batch="b1"):
    body = {
        "version": 1,
        "run_id": RUN,
        "batch_id": batch,
        "kind": "transform",
        "items": [
            {"collection": c, "op": "insert", "item": {"id": i}, "assets": {}}
            for c, i in items
        ],
    }
    if skipped:
        body["skipped"] = [
            {"item_id": i, "collection": c, "reason": "not_found"} for c, i in skipped
        ]
    return f"{PREFIX}inputs/{batch}/manifest.json", json.dumps(body).encode()


async def resolve_with_inputs(objects, refs=REFS, base="/"):
    resolver = ProcessRunResolver(store=FakeStore(objects), catalog_href_base=base)
    return await resolver.resolve(build_process_request(RUN, OUT, input_items=refs))


def derived_from(document):
    return [ln for ln in document.get("links", []) if ln.get("rel") == "derived_from"]


def test_the_request_carries_the_input_batch():
    req = build_process_request(RUN, OUT, input_items=REFS)
    assert tuple(req.provenance["input_items"]) == REFS
    # Absent by default — a request built without a batch stamps nothing.
    assert build_process_request(RUN, OUT).provenance["input_items"] == ()


async def test_every_output_is_stamped_once_per_input():
    res = await resolve_with_inputs(
        {f"{PREFIX}i1.json": item_doc(), f"{PREFIX}mask.tif": b"x"}
    )
    (item,) = res.items
    links = derived_from(item.document)
    assert [ln["href"] for ln in links] == [
        f"/collections/{SRC}/items/scene-1",
        f"/collections/{SRC}/items/scene-2",
    ]
    assert all(ln["type"] == "application/geo+json" for ln in links)


async def test_the_href_base_is_honoured_and_segments_are_encoded():
    refs = ({"item_id": "a b/c", "collection_id": SRC, "op": "insert"},)
    res = await resolve_with_inputs(
        {f"{PREFIX}i1.json": item_doc(), f"{PREFIX}mask.tif": b"x"},
        refs=refs,
        base="https://catalog.example/stac/",
    )
    (item,) = res.items
    assert [ln["href"] for ln in derived_from(item.document)] == [
        f"https://catalog.example/stac/collections/{SRC}/items/a%20b%2Fc"
    ]


async def test_a_document_that_already_carries_derived_from_is_left_alone():
    from _finalize_fake import valid_item

    doc = valid_item(item_id="i1", collection="cloud-masks", assets={"data": {"href": "mask.tif"}})
    doc["links"] = [
        {"rel": "derived_from", "href": f"/collections/{SRC}/items/scene-1"},
        {"rel": "license", "href": "https://example.com/license"},
    ]
    res = await resolve_with_inputs(
        {f"{PREFIX}i1.json": json.dumps(doc).encode(), f"{PREFIX}mask.tif": b"x"}
    )
    (item,) = res.items
    assert item.document["links"] == doc["links"]


async def test_other_links_are_kept_and_derived_from_is_appended():
    from _finalize_fake import valid_item

    doc = valid_item(item_id="i1", collection="cloud-masks", assets={"data": {"href": "mask.tif"}})
    doc["links"] = [{"rel": "license", "href": "https://example.com/license"}]
    res = await resolve_with_inputs(
        {f"{PREFIX}i1.json": json.dumps(doc).encode(), f"{PREFIX}mask.tif": b"x"}
    )
    (item,) = res.items
    assert item.document["links"][0] == {"rel": "license", "href": "https://example.com/license"}
    assert len(derived_from(item.document)) == 2


async def test_an_input_the_planner_skipped_is_not_linked():
    key, body = _manifest(items=[(SRC, "scene-1")], skipped=[(SRC, "scene-2")])
    res = await resolve_with_inputs(
        {f"{PREFIX}i1.json": item_doc(), f"{PREFIX}mask.tif": b"x", key: body}
    )
    (item,) = res.items
    assert [ln["href"] for ln in derived_from(item.document)] == [
        f"/collections/{SRC}/items/scene-1"
    ]


async def test_skips_are_collected_across_every_batch_manifest():
    k1, b1 = _manifest(items=[(SRC, "scene-1")], batch="b1")
    k2, b2 = _manifest(items=[], skipped=[(SRC, "scene-2")], batch="b2")
    res = await resolve_with_inputs(
        {f"{PREFIX}i1.json": item_doc(), f"{PREFIX}mask.tif": b"x", k1: b1, k2: b2}
    )
    (item,) = res.items
    assert [ln["href"] for ln in derived_from(item.document)] == [
        f"/collections/{SRC}/items/scene-1"
    ]


async def test_an_unreadable_manifest_does_not_sink_the_stamp():
    res = await resolve_with_inputs(
        {
            f"{PREFIX}i1.json": item_doc(),
            f"{PREFIX}mask.tif": b"x",
            f"{PREFIX}inputs/b1/manifest.json": b"not json",
        }
    )
    (item,) = res.items
    assert len(derived_from(item.document)) == 2


async def test_an_output_that_is_also_an_input_does_not_link_to_itself():
    # An in-place update: the process republishes the item it was handed.
    refs = (
        {"item_id": "i1", "collection_id": "cloud-masks", "op": "update"},
        {"item_id": "scene-1", "collection_id": SRC, "op": "insert"},
    )
    res = await resolve_with_inputs(
        {f"{PREFIX}i1.json": item_doc(), f"{PREFIX}mask.tif": b"x"}, refs=refs
    )
    (item,) = res.items
    assert [ln["href"] for ln in derived_from(item.document)] == [
        f"/collections/{SRC}/items/scene-1"
    ]


async def test_duplicate_and_shapeless_refs_stamp_once_or_never():
    refs = (
        {"item_id": "scene-1", "collection_id": SRC, "op": "insert"},
        {"item_id": "scene-1", "collection_id": SRC, "op": "update"},
        {"item_id": "", "collection_id": SRC},
        {"collection_id": SRC},
        {"item_id": "no-collection"},
    )
    res = await resolve_with_inputs(
        {f"{PREFIX}i1.json": item_doc(), f"{PREFIX}mask.tif": b"x"}, refs=refs
    )
    (item,) = res.items
    assert [ln["href"] for ln in derived_from(item.document)] == [
        f"/collections/{SRC}/items/scene-1"
    ]


async def test_a_run_without_inputs_stamps_nothing():
    # A cron-triggered run has no input batch by construction.
    res = await resolve({f"{PREFIX}i1.json": item_doc(), f"{PREFIX}mask.tif": b"x"})
    (item,) = res.items
    assert derived_from(item.document) == []
    assert item.document["links"] == []


async def test_the_stamped_link_survives_the_neutral_steps():
    """The document the steps validate and upsert is the stamped one — the
    stamp happens at resolve, before `_rewrite_document`, so no step needs
    to know about lineage (ADR 0014)."""
    from _finalize_fake import FakeObjectStore, FakeWriter
    from pipeline.finalize.seam import ProducerHooks
    from pipeline.finalize.steps import run_finalize

    class StubPreflight:
        async def collection_archived(self, collection_id: str) -> bool:
            return False

        async def has_open_gc_mark(self, prefix: str) -> bool:
            return False

    store = FakeObjectStore(
        objects={f"{PREFIX}i1.json": item_doc(), f"{PREFIX}mask.tif": b"mask-bytes"}
    )
    writer = FakeWriter()
    hooks = {
        PRODUCER_PROCESS_RUN: ProducerHooks(
            resolver=ProcessRunResolver(store=store),
            recorder=ProcessRunRecorder(repo=FakeProcessRepo()),
        )
    }
    await run_finalize(
        build_process_request(RUN, OUT, input_items=REFS),
        hooks=hooks,
        preflight=StubPreflight(),
        store=store,
        writer=writer,
        asset_href_base="/api/assets",
    )
    (published,) = writer.upserted
    assert [ln["href"] for ln in derived_from(published)] == [
        f"/collections/{SRC}/items/scene-1",
        f"/collections/{SRC}/items/scene-2",
    ]
