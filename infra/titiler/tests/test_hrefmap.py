import pytest

from stac_higher_titiler.hrefmap import map_canonical_href

B = "stac-higher"


def test_maps_a_canonical_href_to_the_platform_object():
    assert (
        map_canonical_href("/api/assets/goes/item-1/visual.tif", bucket=B)
        == "s3://stac-higher/assets/goes/item-1/visual.tif"
    )


def test_url_decodes_each_segment():
    assert (
        map_canonical_href("/api/assets/my%20coll/OR_ABI%2Bx/a%20b.tif", bucket=B)
        == "s3://stac-higher/assets/my coll/OR_ABI+x/a b.tif"
    )


def test_honours_a_custom_base():
    assert (
        map_canonical_href("/assets/c/i/f.tif", bucket=B, base="/assets")
        == "s3://stac-higher/assets/c/i/f.tif"
    )


def test_strips_a_file_scheme_prefix_pystac_may_add():
    # pystac's get_absolute_href() can turn a rooted path into file:///…
    assert (
        map_canonical_href("file:///api/assets/c/i/f.tif", bucket=B)
        == "s3://stac-higher/assets/c/i/f.tif"
    )


@pytest.mark.parametrize(
    "href",
    [
        "https://noaa-goes19.s3.amazonaws.com/ABI-L2-MCMIPC/x.nc",
        "s3://other/assets/c/i/f.tif",
        "/api/assets/c/i",  # too few segments
        "/api/assets/c/i/f/g.tif",  # too many
        "/api/assets/c//f.tif",  # empty segment
        "/api/assets/c/../f.tif",  # traversal
        "/api/assetsX/c/i/f.tif",  # base must end at a separator
        "vrt:///api/assets/c/i/f.tif?bands=1",  # vrt wrapper is left alone
        "",
        None,
        42,
    ],
)
def test_everything_else_passes_through_unchanged(href):
    assert map_canonical_href(href, bucket=B) is href


def test_query_string_is_dropped_only_when_mapping():
    assert (
        map_canonical_href("/api/assets/c/i/f.tif?x=1", bucket=B)
        == "s3://stac-higher/assets/c/i/f.tif"
    )
