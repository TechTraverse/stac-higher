import hashlib
import io

import pytest

from _delivery_fake import FakeDeliveryRepo
from _ingest_fake import FakeAdapter
from pipeline.connections.repo import ConnectionRow
from pipeline.delivery.config import parse_delivery_config
from pipeline.delivery.repo import DeliverTarget, ReferenceSource
from pipeline.delivery.worker import deliver_item

pytestmark = pytest.mark.asyncio


class _FakeAdapter:
    def __init__(self, copy_error=None):
        self.puts: list[tuple[str, bytes]] = []
        self.copies: list[tuple[str, str, str]] = []
        self.copy_error = copy_error

    async def put_atomic(self, path, data):
        self.puts.append((path, data))

    async def copy_object_from(self, src_bucket, src_key, dst_path):
        if self.copy_error:
            raise self.copy_error
        self.copies.append((src_bucket, src_key, dst_path))


class _FakeS3:
    """get_object/head_object over a dict keyed by (bucket, key)."""

    def __init__(self, objects, etags=None):
        self.objects = objects
        self.etags = etags or {}
        self.heads: list[tuple[str, str]] = []

    def get_object(self, Bucket, Key):  # boto3 kwarg names (not enabled: N803)
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def head_object(self, Bucket, Key):  # boto3 kwarg names (not enabled: N803)
        self.heads.append((Bucket, Key))
        data = self.objects[(Bucket, Key)]
        etag = self.etags.get((Bucket, Key), hashlib.md5(data).hexdigest())
        return {"ETag": f'"{etag}"', "ContentLength": len(data)}


def _target():
    return DeliverTarget(id="a1", collection_id="col", config={}, connection=None)


def _item(assets):
    return {"id": "scene", "collection": "col", "properties": {}, "assets": assets}


def _config(**overrides):
    base = {"path_template": "{filename}"}
    base.update(overrides)
    return parse_delivery_config(base)


def _source_connection():
    return ConnectionRow(
        id="c9", name="src", protocol="s3", config={}, credentials=None, host_key=None
    )


async def _run(repo, adapter, s3, item, config, asset_keys=("data",), **kwargs):
    await deliver_item(
        repo, adapter, s3, "bucket",
        target=_target(), config=config, item=item,
        asset_keys=list(asset_keys), item_created_at=None,
        **kwargs,
    )


async def test_delivers_asset_bytes_and_records_row():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"IMGDATA"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(path_template="{collection}/{item_id}/{filename}")

    await _run(repo, adapter, s3, item, config)

    assert adapter.puts == [("col/scene/a.tif", b"IMGDATA")]
    (_rid, rec), = repo.rows.items()
    assert rec["status"] == "delivered"
    assert rec["bytes"] == len(b"IMGDATA")


async def test_multiple_assets_sum_bytes():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({
        ("bucket", "assets/col/scene/a.tif"): b"AAA",
        ("bucket", "assets/col/scene/b.tif"): b"BBBB",
    })
    item = _item({
        "a": {"href": "/api/assets/col/scene/a.tif"},
        "b": {"href": "/api/assets/col/scene/b.tif"},
    })

    await _run(repo, adapter, s3, item, _config(), asset_keys=("a", "b"))
    (rec,) = repo.rows.values()
    assert rec["bytes"] == 7
    assert {p for p, _ in adapter.puts} == {"a.tif", "b.tif"}


async def test_missing_asset_key_is_skipped():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"AAA"})
    item = _item({"a": {"href": "/api/assets/col/scene/a.tif"}})

    # "gone" isn't in the item's assets — skip it, deliver "a".
    await _run(repo, adapter, s3, item, _config(), asset_keys=("a", "gone"))
    assert [p for p, _ in adapter.puts] == ["a.tif"]
    (rec,) = repo.rows.values()
    assert rec["status"] == "delivered"


async def test_transfer_failure_marks_failed_without_raising():
    repo = FakeDeliveryRepo()

    class _BoomAdapter(_FakeAdapter):
        async def put_atomic(self, path, data):
            raise RuntimeError("dest down")

    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"AAA"})
    item = _item({"a": {"href": "/api/assets/col/scene/a.tif"}})

    await _run(repo, _BoomAdapter(), s3, item, _config(), asset_keys=("a",))
    (rec,) = repo.rows.values()
    assert rec["status"] == "failed"
    assert "dest down" in rec["error"]


async def test_on_update_ignore_skips_after_delivered():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"V1"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(on_update="ignore")

    await _run(repo, adapter, s3, item, config)
    assert len(adapter.puts) == 1
    # A second event for the already-delivered item is consumed without writes.
    await _run(repo, adapter, s3, item, config)
    assert len(adapter.puts) == 1
    (rec,) = repo.rows.values()
    assert rec["status"] == "delivered"
    assert rec["attempts"] == 1  # untouched by the ignored event


async def test_on_update_ignore_still_delivers_after_failure():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({})  # canonical object missing -> first attempt fails
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(on_update="ignore")

    await _run(repo, adapter, s3, item, config)
    (rec,) = repo.rows.values()
    assert rec["status"] == "failed"
    # ignore only applies to a *delivered* item — a failed one retries.
    s3.objects[("bucket", "assets/col/scene/a.tif")] = b"V1"
    await _run(repo, adapter, s3, item, config)
    (rec,) = repo.rows.values()
    assert rec["status"] == "delivered"


async def test_if_newer_skips_unchanged_and_redelivers_changed():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"V1"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config()  # defaults: on_update=redeliver, overwrite=if_newer

    await _run(repo, adapter, s3, item, config)
    await _run(repo, adapter, s3, item, config)  # unchanged -> no second write
    assert len(adapter.puts) == 1
    s3.objects[("bucket", "assets/col/scene/a.tif")] = b"V2-different"
    await _run(repo, adapter, s3, item, config)  # changed -> rewrite
    assert len(adapter.puts) == 2
    (rec,) = repo.rows.values()
    assert rec["delivered_assets"]["data"]["fingerprint"].startswith("sha256:")


async def test_overwrite_never_skips_previously_delivered():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"V1"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(overwrite="never")

    await _run(repo, adapter, s3, item, config)
    s3.objects[("bucket", "assets/col/scene/a.tif")] = b"V2-changed"
    await _run(repo, adapter, s3, item, config)
    assert len(adapter.puts) == 1  # changed bytes, but never overwrite
    (rec,) = repo.rows.values()
    # The prior fingerprint is kept — it reflects what is at the destination.
    expected = f"sha256:{hashlib.sha256(b'V1').hexdigest()}"
    assert rec["delivered_assets"]["data"]["fingerprint"] == expected


async def test_overwrite_always_rewrites_unchanged():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"V1"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(overwrite="always")

    await _run(repo, adapter, s3, item, config)
    await _run(repo, adapter, s3, item, config)
    assert len(adapter.puts) == 2


async def test_payload_sidecars_written_in_order_marker_last():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"IMG"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(payload={"item_json": True, "checksums": "sha256", "completion_marker": True})

    await _run(repo, adapter, s3, item, config)
    paths = [p for p, _ in adapter.puts]
    assert paths == ["a.tif", "a.tif.sha256", "scene.json", "scene.done"]
    body = dict(adapter.puts)["a.tif.sha256"]
    assert body == f"{hashlib.sha256(b'IMG').hexdigest()}  a.tif\n".encode()
    (rec,) = repo.rows.values()
    assert rec["bytes"] == sum(len(d) for _, d in adapter.puts)


async def test_metadata_only_update_rewrites_item_json_only():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"IMG"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(payload={"item_json": True, "checksums": None, "completion_marker": True})

    await _run(repo, adapter, s3, item, config)
    n = len(adapter.puts)
    item2 = dict(item, properties={"platform": "edited"})
    await _run(repo, adapter, s3, item2, config)
    new = [p for p, _ in adapter.puts[n:]]
    # asset unchanged -> only the item JSON refreshes, then the marker.
    assert new == ["scene.json", "scene.done"]


async def test_no_writes_when_nothing_changed_and_no_item_json():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"IMG"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(payload={"item_json": False, "checksums": None, "completion_marker": True})

    await _run(repo, adapter, s3, item, config)
    n = len(adapter.puts)
    await _run(repo, adapter, s3, item, config)
    # nothing written -> no marker either; row still flips back to delivered.
    assert len(adapter.puts) == n
    (rec,) = repo.rows.values()
    assert rec["status"] == "delivered"


async def test_reference_asset_reads_source_adapter_not_canonical():
    conn = _source_connection()
    src = ReferenceSource(filename="a.tif", fetch_path="incoming/a.tif", connection=conn)
    repo = FakeDeliveryRepo(reference_sources={"scene": [src]})
    adapter = _FakeAdapter()
    s3 = _FakeS3({})  # canonical is EMPTY — a canonical read would raise
    source = FakeAdapter(blobs={"incoming/a.tif": b"REFBYTES"})
    built: list = []

    def _factory(connection):
        built.append(connection)
        return source

    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    await _run(repo, adapter, s3, item, _config(), build_source_adapter=_factory)
    assert source.get_calls == ["incoming/a.tif"]
    assert adapter.puts == [("a.tif", b"REFBYTES")]
    assert built == [conn]
    (rec,) = repo.rows.values()
    assert rec["status"] == "delivered"


async def test_source_adapter_built_once_per_connection():
    conn = _source_connection()
    sources = [
        ReferenceSource(filename="a.tif", fetch_path="in/a.tif", connection=conn),
        ReferenceSource(filename="b.tif", fetch_path="in/b.tif", connection=conn),
    ]
    repo = FakeDeliveryRepo(reference_sources={"scene": sources})
    adapter = _FakeAdapter()
    source = FakeAdapter(blobs={"in/a.tif": b"A", "in/b.tif": b"B"})
    built: list = []

    def _factory(connection):
        built.append(connection)
        return source

    item = _item({
        "a": {"href": "/api/assets/col/scene/a.tif"},
        "b": {"href": "/api/assets/col/scene/b.tif"},
    })
    await _run(
        repo, adapter, _FakeS3({}), item, _config(),
        asset_keys=("a", "b"), build_source_adapter=_factory,
    )
    assert len(built) == 1  # cached per connection id
    assert {p for p, _ in adapter.puts} == {"a.tif", "b.tif"}


async def test_server_side_copy_no_stream():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"IMGDATA"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})

    await _run(repo, adapter, s3, item, _config(), server_side_copy=True)
    assert adapter.copies == [("bucket", "assets/col/scene/a.tif", "a.tif")]
    assert adapter.puts == []  # no bytes through the worker
    (rec,) = repo.rows.values()
    assert rec["status"] == "delivered"
    assert rec["delivered_assets"]["data"]["fingerprint"].startswith("etag:")
    assert rec["bytes"] == len(b"IMGDATA")


async def test_copy_skipped_unchanged_on_redelivery():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"IMGDATA"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})

    for _ in range(2):
        await _run(repo, adapter, s3, item, _config(), server_side_copy=True)
    assert len(adapter.copies) == 1  # unchanged etag -> if_newer skips


async def test_sha256_checksums_force_streaming():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"IMGDATA"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(
        payload={"item_json": False, "checksums": "sha256", "completion_marker": False}
    )

    await _run(repo, adapter, s3, item, config, server_side_copy=True)
    assert adapter.copies == []  # honest checksum beats copy efficiency
    assert [p for p, _ in adapter.puts] == ["a.tif", "a.tif.sha256"]


async def test_md5_checksum_uses_single_part_etag_and_keeps_copy():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"IMGDATA"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(payload={"item_json": False, "checksums": "md5", "completion_marker": False})

    await _run(repo, adapter, s3, item, config, server_side_copy=True)
    assert len(adapter.copies) == 1
    body = dict(adapter.puts)["a.tif.md5"]
    assert body == f"{hashlib.md5(b'IMGDATA').hexdigest()}  a.tif\n".encode()


async def test_md5_checksum_multipart_etag_falls_back_to_stream():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3(
        {("bucket", "assets/col/scene/a.tif"): b"IMGDATA"},
        etags={("bucket", "assets/col/scene/a.tif"): "abc123-4"},  # multipart
    )
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(payload={"item_json": False, "checksums": "md5", "completion_marker": False})

    await _run(repo, adapter, s3, item, config, server_side_copy=True)
    assert adapter.copies == []  # "abc123-4" is not an md5 — stream instead
    body = dict(adapter.puts)["a.tif.md5"]
    assert body == f"{hashlib.md5(b'IMGDATA').hexdigest()}  a.tif\n".encode()


async def test_copy_failure_falls_back_to_streaming():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter(copy_error=RuntimeError("AccessDenied"))
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"IMGDATA"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})

    await _run(repo, adapter, s3, item, _config(), server_side_copy=True)
    assert adapter.puts == [("a.tif", b"IMGDATA")]  # fell back, delivery succeeded
    (rec,) = repo.rows.values()
    assert rec["status"] == "delivered"
    assert rec["delivered_assets"]["data"]["fingerprint"].startswith("sha256:")


async def test_reference_asset_never_server_side_copies():
    src = ReferenceSource(
        filename="a.tif", fetch_path="in/a.tif", connection=_source_connection()
    )
    repo = FakeDeliveryRepo(reference_sources={"scene": [src]})
    adapter = _FakeAdapter()
    source = FakeAdapter(blobs={"in/a.tif": b"REF"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})

    await _run(
        repo, adapter, _FakeS3({}), item, _config(),
        build_source_adapter=lambda c: source, server_side_copy=True,
    )
    assert adapter.copies == []
    assert adapter.puts == [("a.tif", b"REF")]
