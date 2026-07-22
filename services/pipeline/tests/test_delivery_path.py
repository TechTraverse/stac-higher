import pytest

from pipeline.delivery.path import DeliveryPathError, render_path


def _item(**props):
    return {"id": "scene-1", "collection": "col", "properties": props}


def test_renders_simple_tokens():
    tmpl = "{collection}/{item_id}/{filename}"
    assert render_path(tmpl, _item(), "a.tif") == "col/scene-1/a.tif"


def test_renders_date_tokens_from_datetime():
    tmpl = "{collection}/{yyyy}/{mm}/{dd}/{item_id}/{filename}"
    item = _item(datetime="2026-03-05T12:00:00Z")
    assert render_path(tmpl, item, "a.tif") == "col/2026/03/05/scene-1/a.tif"


def test_date_tokens_fall_back_to_start_datetime():
    tmpl = "{yyyy}-{mm}-{dd}/{filename}"
    item = _item(datetime=None, start_datetime="2025-12-31T00:00:00+00:00")
    assert render_path(tmpl, item, "a.tif") == "2025-12-31/a.tif"


def test_missing_datetime_with_date_token_raises():
    with pytest.raises(DeliveryPathError):
        render_path("{yyyy}/{filename}", _item(), "a.tif")


def test_no_date_token_needs_no_datetime():
    # an item with no datetime is fine when the template uses none.
    assert render_path("{item_id}/{filename}", _item(), "a.tif") == "scene-1/a.tif"


def test_unknown_token_raises():
    with pytest.raises(DeliveryPathError):
        render_path("{collection}/{bogus}", _item(), "a.tif")
