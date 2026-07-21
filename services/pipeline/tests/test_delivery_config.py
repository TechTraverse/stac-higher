from pipeline.delivery.config import (
    DeliveryConfig,
    DeliveryConfigError,
    parse_delivery_config,
)


def test_minimal_config_applies_defaults():
    cfg = parse_delivery_config({"path_template": "{collection}/{item_id}/{filename}"})
    assert isinstance(cfg, DeliveryConfig)
    assert cfg.item_filter is None
    assert cfg.asset_keys is None
    assert cfg.payload == {"item_json": False, "checksums": None, "completion_marker": False}
    assert cfg.on_update == "redeliver"
    assert cfg.overwrite == "if_newer"
    assert cfg.max_concurrent_transfers == 4


def test_missing_path_template_raises():
    import pytest

    with pytest.raises(DeliveryConfigError):
        parse_delivery_config({})


def test_carries_item_filter_and_asset_keys():
    cfg = parse_delivery_config(
        {
            "path_template": "{item_id}/{filename}",
            "item_filter": "eo:cloud_cover < 10",
            "asset_keys": ["data", "thumbnail"],
        }
    )
    assert cfg.item_filter == "eo:cloud_cover < 10"
    assert cfg.asset_keys == ("data", "thumbnail")
