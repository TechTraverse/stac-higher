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
