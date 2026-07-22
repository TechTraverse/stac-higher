import pytest

from _delivery_fake import FakeDeliveryRepo
from pipeline.delivery.config import parse_delivery_config
from pipeline.delivery.repo import DeliverTarget
from pipeline.delivery.worker import deliver_item

pytestmark = pytest.mark.asyncio


class _FakeAdapter:
    def __init__(self):
        self.puts: list[tuple[str, bytes]] = []

    async def put_atomic(self, path, data):
        self.puts.append((path, data))


class _FakeS3:
    """Only get_object is used; keyed by (bucket, key)."""

    def __init__(self, objects):
        self.objects = objects

    def get_object(self, Bucket, Key):  # boto3 kwarg names (not enabled: N803)
        import io

        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


def _target():
    return DeliverTarget(id="a1", collection_id="col", config={}, connection=None)


def _item(assets):
    return {"id": "scene", "collection": "col", "properties": {}, "assets": assets}


async def test_delivers_asset_bytes_and_records_row():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"IMGDATA"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = parse_delivery_config({"path_template": "{collection}/{item_id}/{filename}"})

    await deliver_item(
        repo, adapter, s3, "bucket",
        target=_target(), config=config, item=item,
        asset_keys=["data"], item_created_at=None,
    )

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
    config = parse_delivery_config({"path_template": "{filename}"})

    await deliver_item(
        repo, adapter, s3, "bucket",
        target=_target(), config=config, item=item,
        asset_keys=["a", "b"], item_created_at=None,
    )
    (rec,) = repo.rows.values()
    assert rec["bytes"] == 7
    assert {p for p, _ in adapter.puts} == {"a.tif", "b.tif"}


async def test_missing_asset_key_is_skipped():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"AAA"})
    item = _item({"a": {"href": "/api/assets/col/scene/a.tif"}})
    config = parse_delivery_config({"path_template": "{filename}"})

    # "gone" isn't in the item's assets — skip it, deliver "a".
    await deliver_item(
        repo, adapter, s3, "bucket",
        target=_target(), config=config, item=item,
        asset_keys=["a", "gone"], item_created_at=None,
    )
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
    config = parse_delivery_config({"path_template": "{filename}"})

    await deliver_item(
        repo, _BoomAdapter(), s3, "bucket",
        target=_target(), config=config, item=item,
        asset_keys=["a"], item_created_at=None,
    )
    (rec,) = repo.rows.values()
    assert rec["status"] == "failed"
    assert "dest down" in rec["error"]


def _config(**overrides):
    base = {"path_template": "{filename}"}
    base.update(overrides)
    return parse_delivery_config(base)


async def _run(repo, adapter, s3, item, config, asset_keys=("data",)):
    await deliver_item(
        repo, adapter, s3, "bucket",
        target=_target(), config=config, item=item,
        asset_keys=list(asset_keys), item_created_at=None,
    )


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
    import hashlib
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
    import hashlib
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


class _FakeSourceAdapter:
    def __init__(self, objects):
        self.objects = objects
        self.gets: list[str] = []

    async def get(self, path):
        self.gets.append(path)
        return self.objects[path]


async def test_reference_asset_reads_source_adapter_not_canonical():
    from pipeline.connections.repo import ConnectionRow
    from pipeline.delivery.repo import ReferenceSource

    conn = ConnectionRow(
        id="c9", name="src", protocol="s3", config={}, credentials=None, host_key=None
    )
    src = ReferenceSource(filename="a.tif", fetch_path="incoming/a.tif", connection=conn)
    repo = FakeDeliveryRepo(reference_sources={"scene": [src]})
    adapter = _FakeAdapter()
    s3 = _FakeS3({})  # canonical is EMPTY — a canonical read would raise
    source = _FakeSourceAdapter({"incoming/a.tif": b"REFBYTES"})
    built: list = []

    def _factory(connection):
        built.append(connection)
        return source

    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    await deliver_item(
        repo, adapter, s3, "bucket",
        target=_target(), config=_config(), item=item,
        asset_keys=["data"], item_created_at=None,
        build_source_adapter=_factory,
    )
    assert source.gets == ["incoming/a.tif"]
    assert adapter.puts == [("a.tif", b"REFBYTES")]
    assert built == [conn]
    (rec,) = repo.rows.values()
    assert rec["status"] == "delivered"


async def test_source_adapter_built_once_per_connection():
    from pipeline.connections.repo import ConnectionRow
    from pipeline.delivery.repo import ReferenceSource

    conn = ConnectionRow(
        id="c9", name="src", protocol="s3", config={}, credentials=None, host_key=None
    )
    sources = [
        ReferenceSource(filename="a.tif", fetch_path="in/a.tif", connection=conn),
        ReferenceSource(filename="b.tif", fetch_path="in/b.tif", connection=conn),
    ]
    repo = FakeDeliveryRepo(reference_sources={"scene": sources})
    adapter = _FakeAdapter()
    source = _FakeSourceAdapter({"in/a.tif": b"A", "in/b.tif": b"B"})
    built: list = []

    def _factory(connection):
        built.append(connection)
        return source

    item = _item({
        "a": {"href": "/api/assets/col/scene/a.tif"},
        "b": {"href": "/api/assets/col/scene/b.tif"},
    })
    await deliver_item(
        repo, adapter, _FakeS3({}), "bucket",
        target=_target(), config=_config(), item=item,
        asset_keys=["a", "b"], item_created_at=None,
        build_source_adapter=_factory,
    )
    assert len(built) == 1  # cached per connection id
    assert {p for p, _ in adapter.puts} == {"a.tif", "b.tif"}
