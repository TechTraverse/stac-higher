from pipeline.delivery.matcher import DeliverAssociation, match_item

ITEM = {
    "id": "scene-1",
    "collection": "sensor-a",
    "properties": {"eo:cloud_cover": 5},
    "assets": {"data": {"href": "..."}, "thumbnail": {"href": "..."}},
}


def _assoc(aid, config):
    return DeliverAssociation(id=aid, collection_id="sensor-a", config=config)


def test_no_filter_matches_all_assets():
    matches = match_item(ITEM, [_assoc("a1", {"path_template": "{filename}"})])
    assert len(matches) == 1
    assert matches[0].association_id == "a1"
    assert set(matches[0].asset_keys) == {"data", "thumbnail"}


def test_item_filter_pass_and_fail():
    passing = _assoc("pass", {"path_template": "{filename}", "item_filter": "eo:cloud_cover < 10"})
    failing = _assoc("fail", {"path_template": "{filename}", "item_filter": "eo:cloud_cover > 50"})
    matches = match_item(ITEM, [passing, failing])
    assert [m.association_id for m in matches] == ["pass"]


def test_asset_keys_intersection():
    a = _assoc("a", {"path_template": "{filename}", "asset_keys": ["data", "missing"]})
    matches = match_item(ITEM, [a])
    assert set(matches[0].asset_keys) == {"data"}


def test_empty_asset_intersection_skips():
    a = _assoc("a", {"path_template": "{filename}", "asset_keys": ["missing"]})
    assert match_item(ITEM, [a]) == []


def test_filter_on_missing_property_isolated_from_other_associations():
    # This item has no "eo:cloud_cover" property at all.
    item_missing_property = {
        "id": "scene-2",
        "collection": "sensor-a",
        "properties": {},
        "assets": {"data": {"href": "..."}},
    }
    references_missing = _assoc(
        "references-missing",
        {"path_template": "{filename}", "item_filter": "eo:cloud_cover < 10"},
    )
    no_filter = _assoc("no-filter", {"path_template": "{filename}"})
    matches = match_item(item_missing_property, [references_missing, no_filter])
    assert [m.association_id for m in matches] == ["no-filter"]


def test_malformed_filter_skipped_without_raising():
    a = _assoc("bad", {"path_template": "{filename}", "item_filter": "not a valid cql2 filter (("})
    b = _assoc("good", {"path_template": "{filename}"})
    matches = match_item(ITEM, [a, b])
    assert [m.association_id for m in matches] == ["good"]
