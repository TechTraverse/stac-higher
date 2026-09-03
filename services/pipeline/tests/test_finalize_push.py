"""Push finalize: happy path, §4.2 admission, §6.1 pre-flight, §6.3 tiers.

Everything runs through ``run_finalize`` with the real push resolver/recorder
over fakes — the same entry the job uses — so claim/idempotency/rejection
semantics are tested end-to-end, not per-helper.
"""

from __future__ import annotations

import hashlib

from _finalize_fake import (
    FakeFinalizeRepo,
    FakeObjectStore,
    FakeWriter,
    Session,
    valid_item,
)
from pipeline.finalize.push import PushRecorder, PushResolver, build_push_request
from pipeline.finalize.seam import PRODUCER_PUSH_INGEST, ProducerHooks
from pipeline.finalize.steps import etags_comparable, run_finalize
from pipeline.metrics import REGISTRY

UPLOAD = "0d9c2f64-8f3a-4a5e-9b7d-1c2e3f405060"
OTHER_UPLOAD = "11111111-2222-4333-8444-555555555555"
COLLECTION = "sentinel-pushed"
ITEM = "S2A_001"


def _staged_doc(*filenames: str, upload_id: str = UPLOAD, extra_assets: dict | None = None):
    assets = {
        f"a{i}": {"href": f"staging://{upload_id}/{name}", "type": "image/tiff"}
        for i, name in enumerate(filenames)
    }
    assets.update(extra_assets or {})
    return valid_item(ITEM, COLLECTION, assets=assets)


def _fixture(
    *,
    doc=None,
    session_status: str = "pending",
    session_collection: str = COLLECTION,
    bound_item: str | None = None,
    prior_item: dict | None = None,
    objects: dict[str, bytes] | None = None,
    with_session: bool = True,
):
    repo = FakeFinalizeRepo()
    if with_session:
        repo.sessions[UPLOAD] = Session(
            id=UPLOAD,
            collection_id=session_collection,
            status=session_status,
            item_id=bound_item,
            prior_item=prior_item,
        )
    if doc is not None:
        repo.items[(COLLECTION, ITEM)] = doc
    store = FakeObjectStore(objects=dict(objects or {}))
    writer = FakeWriter()
    hooks = {
        PRODUCER_PUSH_INGEST: ProducerHooks(
            resolver=PushResolver(repo), recorder=PushRecorder(repo, writer)
        )
    }
    return repo, store, writer, hooks


async def _run(repo, store, writer, hooks, *, event_op="insert"):
    req = build_push_request(UPLOAD, COLLECTION, ITEM, event_op)
    result = await run_finalize(
        req, hooks=hooks, preflight=repo, store=store, writer=writer
    )
    return result


def _metric(outcome: str) -> float:
    return (
        REGISTRY.get_sample_value(
            "pipeline_finalize_items_total",
            {"producer": PRODUCER_PUSH_INGEST, "outcome": outcome},
        )
        or 0.0
    )


# -- happy path -------------------------------------------------------------


async def test_finalize_moves_rewrites_upserts_and_records():
    payload = b"tiff-bytes"
    doc = _staged_doc(
        "B04.tif",
        extra_assets={"thumb": {"href": "https://example.com/t.png"}},
    )
    repo, store, writer, hooks = _fixture(
        doc=doc, objects={f"staging/{UPLOAD}/B04.tif": payload}
    )
    bytes_before = (
        REGISTRY.get_sample_value(
            "pipeline_finalize_bytes_total", {"producer": PRODUCER_PUSH_INGEST}
        )
        or 0.0
    )

    result = await _run(repo, store, writer, hooks)

    assert [(u.collection_id, u.item_id) for u in result.upserted] == [(COLLECTION, ITEM)]
    # move: canonical copy written, staged original deleted
    assert (f"staging/{UPLOAD}/B04.tif", f"assets/{COLLECTION}/{ITEM}/B04.tif") in store.copied
    assert store.deleted == [f"staging/{UPLOAD}/B04.tif"]
    # rewrite: platform href on the staged asset; external href untouched
    landed = writer.upserted[0]
    assert landed["assets"]["a0"]["href"] == f"/api/assets/{COLLECTION}/{ITEM}/B04.tif"
    assert landed["assets"]["thumb"]["href"] == "https://example.com/t.png"
    # ledger: finalized with checksums + upserted (tier 1)
    session = repo.sessions[UPLOAD]
    assert session.status == "finalized"
    assert session.result["upserted"] == [{"collection_id": COLLECTION, "item_id": ITEM}]
    digest = hashlib.sha256(payload).hexdigest()
    assert session.result["checksums"] == {"B04.tif": f"sha256:{digest}"}
    # §9: bytes counter moved by the payload size
    bytes_after = REGISTRY.get_sample_value(
        "pipeline_finalize_bytes_total", {"producer": PRODUCER_PUSH_INGEST}
    )
    assert bytes_after == bytes_before + len(payload)


async def test_metadata_only_rerun_walks_through_idempotently():
    """Crash recovery (§6.4): a re-run after the move+upsert already landed
    finds the item canonical — nothing to move, verdict still recorded."""
    doc = valid_item(
        ITEM, COLLECTION, assets={"a0": {"href": f"/api/assets/{COLLECTION}/{ITEM}/B04.tif"}}
    )
    repo, store, writer, hooks = _fixture(doc=doc)

    result = await _run(repo, store, writer, hooks)

    assert len(result.upserted) == 1
    assert store.copied == []
    assert repo.sessions[UPLOAD].status == "finalized"
    assert repo.sessions[UPLOAD].result["checksums"] == {}
    assert writer.upserted[0] == doc


async def test_multi_asset_single_session_is_fine():
    repo, store, writer, hooks = _fixture(
        doc=_staged_doc("B04.tif", "B08.tif"),
        objects={
            f"staging/{UPLOAD}/B04.tif": b"one",
            f"staging/{UPLOAD}/B08.tif": b"two",
        },
    )

    result = await _run(repo, store, writer, hooks)

    assert len(result.upserted) == 1
    assert sorted(repo.sessions[UPLOAD].result["checksums"]) == ["B04.tif", "B08.tif"]
    assert len(store.deleted) == 2


# -- §4.2 admission ----------------------------------------------------------


async def test_unknown_session_rejects_and_leaves_the_document():
    """No ledger row means no mint-time anchor for the I-46 predates check —
    an op=insert here could be the insert leg of a replace pair on a
    PRE-EXISTING item (a direct PUT with a garbage staged href), so deleting
    is unsafe. Leave the document; the operator cleans up a true typo'd
    create by hand."""
    doc = _staged_doc("B04.tif")
    repo, store, writer, hooks = _fixture(doc=doc, with_session=False)

    result = await _run(repo, store, writer, hooks, event_op="insert")

    assert result.rejected[0].reason == "unknown_session"
    assert repo.deleted_items == []
    assert repo.items[(COLLECTION, ITEM)] == doc
    # there is no ledger row to stamp (and none was invented)
    assert repo.sessions == {}


async def test_bound_to_other_item_rejects_without_touching_the_owning_session():
    doc = _staged_doc("B04.tif")
    repo, store, writer, hooks = _fixture(doc=doc, bound_item="someone-elses-item")

    result = await _run(repo, store, writer, hooks, event_op="insert")

    assert result.rejected[0].reason == "bound_to_other_item"
    assert repo.deleted_items == [(COLLECTION, ITEM)]
    # the other push's session survives, still pending and still bound to it
    session = repo.sessions[UPLOAD]
    assert session.status == "pending"
    assert session.item_id == "someone-elses-item"


async def test_wrong_collection_rejects_unclaimed_so_the_owner_can_repush():
    doc = _staged_doc("B04.tif")
    repo, store, writer, hooks = _fixture(doc=doc, session_collection="another-collection")

    result = await _run(repo, store, writer, hooks, event_op="insert")

    assert result.rejected[0].reason == "wrong_collection"
    assert repo.deleted_items == [(COLLECTION, ITEM)]
    assert repo.sessions[UPLOAD].status == "pending"  # rightful owner keeps it


async def test_multi_session_rejects_with_the_claimed_row_stamped():
    doc = valid_item(
        ITEM,
        COLLECTION,
        assets={
            "a0": {"href": f"staging://{UPLOAD}/B04.tif"},
            "a1": {"href": f"staging://{OTHER_UPLOAD}/B08.tif"},
        },
    )
    repo, store, writer, hooks = _fixture(doc=doc)

    result = await _run(repo, store, writer, hooks, event_op="insert")

    assert result.rejected[0].reason == "multi_session"
    session = repo.sessions[UPLOAD]
    assert session.status == "rejected"
    assert session.result["rejected"] == [{"reason": "multi_session"}]
    assert repo.deleted_items == [(COLLECTION, ITEM)]


async def test_malformed_staged_href_rejects_as_invalid_item():
    doc = valid_item(
        ITEM, COLLECTION, assets={"a0": {"href": f"staging://{UPLOAD}/../escape.tif"}}
    )
    repo, store, writer, hooks = _fixture(doc=doc)

    result = await _run(repo, store, writer, hooks)

    assert result.rejected[0].reason == "invalid_item"
    assert repo.sessions[UPLOAD].status == "rejected"


# -- §6.3 claim no-ops --------------------------------------------------------


async def test_claim_on_terminal_row_is_a_noop_with_metric():
    """The no-zombie rule: a later event for a terminal session never
    re-stamps the verdict or touches the catalog."""
    doc = _staged_doc("B04.tif")
    repo, store, writer, hooks = _fixture(doc=doc, session_status="rejected")
    repo.sessions[UPLOAD].result = {"rejected": [{"reason": "missing_bytes"}]}
    before = _metric("stale_claim")

    result = await _run(repo, store, writer, hooks)

    assert result.upserted == () and result.rejected == ()
    assert repo.sessions[UPLOAD].status == "rejected"
    assert repo.sessions[UPLOAD].result == {"rejected": [{"reason": "missing_bytes"}]}
    assert repo.deleted_items == []
    assert _metric("stale_claim") == before + 1


async def test_concurrent_duplicate_claim_is_a_noop():
    doc = _staged_doc("B04.tif")
    repo, store, writer, hooks = _fixture(doc=doc, session_status="finalizing")

    result = await _run(repo, store, writer, hooks)

    assert result.upserted == () and result.rejected == ()
    assert repo.sessions[UPLOAD].status == "finalizing"


async def test_missing_item_releases_the_claim():
    repo, store, writer, hooks = _fixture(doc=None)
    before = _metric("item_missing")

    result = await _run(repo, store, writer, hooks)

    assert result.upserted == () and result.rejected == ()
    assert repo.released == [UPLOAD]
    assert repo.sessions[UPLOAD].status == "pending"
    assert _metric("item_missing") == before + 1


async def test_superseded_session_releases_the_claim():
    """A second PUT swapped the item to another session mid-flight: this
    session stands down instead of rejecting the newer session's item."""
    doc = _staged_doc("B08.tif", upload_id=OTHER_UPLOAD)
    repo, store, writer, hooks = _fixture(doc=doc)
    before = _metric("superseded")

    result = await _run(repo, store, writer, hooks)

    assert result.upserted == () and result.rejected == ()
    assert repo.sessions[UPLOAD].status == "pending"
    assert repo.deleted_items == []
    assert _metric("superseded") == before + 1


# -- §6.1 pre-flight ----------------------------------------------------------


async def test_archived_collection_is_refused_at_execution_time():
    doc = _staged_doc("B04.tif")
    repo, store, writer, hooks = _fixture(
        doc=doc, objects={f"staging/{UPLOAD}/B04.tif": b"x"}
    )
    repo.archived.add(COLLECTION)

    result = await _run(repo, store, writer, hooks)

    assert result.rejected[0].reason == "collection_archived"
    assert store.copied == []
    assert repo.sessions[UPLOAD].status == "rejected"


async def test_open_gc_mark_on_item_prefix_is_refused():
    doc = _staged_doc("B04.tif")
    repo, store, writer, hooks = _fixture(
        doc=doc, objects={f"staging/{UPLOAD}/B04.tif": b"x"}
    )
    repo.open_gc_marks.add(f"assets/{COLLECTION}/{ITEM}/")

    result = await _run(repo, store, writer, hooks)

    assert result.rejected[0].reason == "gc_pending"
    assert store.copied == []


async def test_collection_level_gc_mark_covers_the_item_prefix():
    doc = _staged_doc("B04.tif")
    repo, store, writer, hooks = _fixture(
        doc=doc, objects={f"staging/{UPLOAD}/B04.tif": b"x"}
    )
    repo.open_gc_marks.add(f"assets/{COLLECTION}/")

    result = await _run(repo, store, writer, hooks)

    assert result.rejected[0].reason == "gc_pending"


# -- async step failures ------------------------------------------------------


async def test_missing_bytes_rejects_before_any_move():
    doc = _staged_doc("B04.tif")
    repo, store, writer, hooks = _fixture(doc=doc)  # nothing PUT to staging

    result = await _run(repo, store, writer, hooks, event_op="insert")

    assert result.rejected[0].reason == "missing_bytes"
    assert store.copied == []
    session = repo.sessions[UPLOAD]
    assert session.status == "rejected"
    assert "never uploaded" in session.error
    # insert rejection with nothing moved: item deleted, NO gc mark needed
    assert repo.deleted_items == [(COLLECTION, ITEM)]
    assert repo.marks == []


async def test_checksum_mismatch_after_partial_move_marks_before_delete():
    """Mark-first (ADR 0011): an insert rejection AFTER bytes moved under the
    canonical prefix GC-marks the prefix before deleting the item."""
    doc = _staged_doc("B04.tif")
    repo, store, writer, hooks = _fixture(
        doc=doc, objects={f"staging/{UPLOAD}/B04.tif": b"x"}
    )
    store.corrupt_on_copy.add(f"staging/{UPLOAD}/B04.tif")

    result = await _run(repo, store, writer, hooks, event_op="insert")

    assert result.rejected[0].reason == "checksum_mismatch"
    prefix = f"assets/{COLLECTION}/{ITEM}/"
    assert [m[0] for m in repo.marks] == [prefix]
    assert repo.marks[0][3] == "item_delete"
    assert repo.deleted_items == [(COLLECTION, ITEM)]
    assert repo.sessions[UPLOAD].status == "rejected"
    # staged originals are left to the TTL sweep, never proactively deleted
    assert store.deleted == []


# -- copy verification: multipart ETags (G-7 live-gate finding 2) --------------


def test_etags_comparable_only_for_single_part_etags():
    assert etags_comparable("abc") is True
    assert etags_comparable('"abc"') is True
    assert etags_comparable("abc-3") is False
    assert etags_comparable('"abc-12"') is False


async def test_multipart_source_etag_verifies_on_size_alone(caplog):
    """A boto3 ``upload_file`` over the 8 MB threshold stages a MULTIPART
    object whose ETag is ``md5(concat(part md5s))-N``; the server-side copy is
    single-part, so its ETag is the plain content MD5 and can never match.
    Same size ⇒ the item lands (verified by size + the step-3 sha256)."""
    # The INFO log on this path passes `extra` keys; a reserved LogRecord
    # attribute there raises only when INFO is enabled — the 2026-09-03 live
    # gate found exactly that, so capture INFO to execute the log call.
    import logging

    caplog.set_level(logging.INFO, logger="pipeline.finalize.steps")
    doc = _staged_doc("big.tif")
    staging_key = f"staging/{UPLOAD}/big.tif"
    repo, store, writer, hooks = _fixture(doc=doc, objects={staging_key: b"large-cog-bytes"})
    store.etags[staging_key] = "9b2cf535f27731c974343645a3985328-3"

    result = await _run(repo, store, writer, hooks)

    assert result.rejected == ()
    assert [(u.collection_id, u.item_id) for u in result.upserted] == [(COLLECTION, ITEM)]
    assert (staging_key, f"assets/{COLLECTION}/{ITEM}/big.tif") in store.copied


async def test_single_part_etag_divergence_still_rejects():
    """The check is narrowed, not dropped: a single-part source whose copy has
    a different ETag at the same size is still a checksum mismatch."""
    doc = _staged_doc("B04.tif")
    staging_key = f"staging/{UPLOAD}/B04.tif"
    repo, store, writer, hooks = _fixture(doc=doc, objects={staging_key: b"x"})
    store.etags[staging_key] = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    store.etags[f"assets/{COLLECTION}/{ITEM}/B04.tif"] = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    result = await _run(repo, store, writer, hooks)

    assert result.rejected[0].reason == "checksum_mismatch"
    assert writer.upserted == []


async def test_multipart_source_with_size_change_still_rejects():
    """Size is checked unconditionally — a truncated/extended copy of a
    multipart source is rejected even though its ETag is not comparable."""
    doc = _staged_doc("big.tif")
    staging_key = f"staging/{UPLOAD}/big.tif"
    repo, store, writer, hooks = _fixture(doc=doc, objects={staging_key: b"large-cog-bytes"})
    store.etags[staging_key] = "9b2cf535f27731c974343645a3985328-3"
    store.corrupt_on_copy.add(staging_key)  # copy comes out one byte longer

    result = await _run(repo, store, writer, hooks)

    assert result.rejected[0].reason == "checksum_mismatch"
    assert writer.upserted == []


async def test_invalid_item_rejects_with_nothing_written():
    doc = _staged_doc("B04.tif")
    del doc["properties"]["datetime"]
    repo, store, writer, hooks = _fixture(
        doc=doc, objects={f"staging/{UPLOAD}/B04.tif": b"x"}
    )

    result = await _run(repo, store, writer, hooks)

    assert result.rejected[0].reason == "invalid_item"
    assert store.copied == []
    assert writer.upserted == []


async def test_collection_missing_in_catalog_rejects_as_wrong_collection():
    doc = _staged_doc("B04.tif")
    repo, store, writer, hooks = _fixture(
        doc=doc, objects={f"staging/{UPLOAD}/B04.tif": b"x"}
    )
    writer.missing_collections.add(COLLECTION)

    result = await _run(repo, store, writer, hooks)

    assert result.rejected[0].reason == "wrong_collection"
    assert repo.sessions[UPLOAD].status == "rejected"


# -- §6.3 tier-2 op discrimination -------------------------------------------


async def test_insert_op_with_snapshot_restores_not_deletes():
    """I-46: the transaction-API path splits a brokered PUT into
    delete+insert events, so the op says "insert" for a genuine update. The
    §4.3 snapshot is the truth — restore it; never delete."""
    prior = valid_item(ITEM, COLLECTION)
    prior["properties"]["title"] = "the pre-push version"
    doc = _staged_doc("B04.tif")
    repo, store, writer, hooks = _fixture(doc=doc, prior_item=prior)

    result = await _run(repo, store, writer, hooks, event_op="insert")

    assert result.rejected[0].reason == "missing_bytes"
    assert writer.upserted == [prior]
    assert repo.deleted_items == []
    assert repo.sessions[UPLOAD].result["restored"] is True


async def test_insert_op_with_predating_history_leaves_the_document():
    """I-46, direct path: op=insert but item_events shows the item existed
    before the session was minted — a replace pair on a pre-existing item.
    Deleting would destroy data this push did not create; leave it broken."""
    doc = _staged_doc("B04.tif")
    repo, store, writer, hooks = _fixture(doc=doc, prior_item=None)
    repo.predating_items.add((COLLECTION, ITEM))

    result = await _run(repo, store, writer, hooks, event_op="insert")

    assert result.rejected[0].reason == "missing_bytes"
    assert repo.deleted_items == []
    assert repo.items[(COLLECTION, ITEM)] == doc
    assert repo.sessions[UPLOAD].status == "rejected"


async def test_update_rejection_with_snapshot_restores_it():
    prior = valid_item(ITEM, COLLECTION)
    prior["properties"]["title"] = "the good version"
    doc = _staged_doc("B04.tif")
    repo, store, writer, hooks = _fixture(doc=doc, prior_item=prior)

    result = await _run(repo, store, writer, hooks, event_op="update")

    assert result.rejected[0].reason == "missing_bytes"
    # restore: the snapshot went back through the writer; nothing deleted
    assert writer.upserted == [prior]
    assert repo.deleted_items == []
    session = repo.sessions[UPLOAD]
    assert session.status == "rejected"
    assert session.result["restored"] is True


async def test_direct_update_rejection_leaves_the_stored_document():
    doc = _staged_doc("B04.tif")
    repo, store, writer, hooks = _fixture(doc=doc, prior_item=None)

    result = await _run(repo, store, writer, hooks, event_op="update")

    assert result.rejected[0].reason == "missing_bytes"
    assert writer.upserted == []  # no restore possible
    assert repo.deleted_items == []  # never delete a pre-existing item
    session = repo.sessions[UPLOAD]
    assert session.status == "rejected"
    assert "restored" not in session.result
    # the broken item is still in the catalog for the operator to fix
    assert repo.items[(COLLECTION, ITEM)] == doc


async def test_recovery_run_without_op_never_deletes():
    """A sweep-requeued job carries event_op=None: the conservative branch
    restores if a snapshot exists, otherwise leaves — never deletes."""
    doc = _staged_doc("B04.tif")
    repo, store, writer, hooks = _fixture(doc=doc, prior_item=None)

    result = await _run(repo, store, writer, hooks, event_op=None)

    assert result.rejected[0].reason == "missing_bytes"
    assert repo.deleted_items == []
    assert repo.items[(COLLECTION, ITEM)] == doc


async def test_staged_delete_failure_is_nonfatal():
    doc = _staged_doc("B04.tif")
    repo, store, writer, hooks = _fixture(
        doc=doc, objects={f"staging/{UPLOAD}/B04.tif": b"x"}
    )
    store.fail_delete.add(f"staging/{UPLOAD}/B04.tif")

    result = await _run(repo, store, writer, hooks)

    assert len(result.upserted) == 1  # still finalized; TTL sweep cleans up
    assert repo.sessions[UPLOAD].status == "finalized"
