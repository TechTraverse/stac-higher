"""The cube's collection asset: builders, merge and the publish decision (spec §6.3, §3.3)."""

from __future__ import annotations

import copy
import json

import numpy as np
import pytest

from _cube_sources import GOES_CONFIG, GOES_PROJECTION, as_ns, scan
from pipeline.cubes.collection import (
    CUBE_MEDIA_TYPE,
    DATACUBE_EXTENSION,
    MISSING_COLLECTION,
    PUBLISHED,
    SUPERSEDED,
    UNCHANGED,
    Recorded,
    crs_projjson,
    cube_href,
    merge_collection,
    plan_publish,
    time_strings,
)
from pipeline.cubes.config import parse_cube_sink_config
from pipeline.cubes.steps import StaticSpec, canonical_attrs
from pipeline.cubes.write import BatchResult

CONFIG = parse_cube_sink_config({**GOES_CONFIG, "window": {"max_steps": 288}})
H = GOES_PROJECTION["perspective_point_height"]
HREF = "s3://platform/assets/goes-cube/_cube/"
PREFIXES = ("s3://noaa-goes19/",)
#: a real GOES-19 scan start, with the sub-microsecond part the tiler needs
T_EXACT = np.datetime64("2026-10-03T17:02:36.714359936", "ns")


def statics(**projection) -> dict[str, StaticSpec]:
    gm = {**GOES_PROJECTION, **projection}
    return {
        "x": StaticSpec(np.array([-0.1, 0.0, 0.1], dtype="f4"), None),
        "y": StaticSpec(np.array([0.08, 0.04, 0.0], dtype="f4"), None),
        "goes_imager_projection": StaticSpec(np.array(-2147483647, "i4"), canonical_attrs(gm)),
    }


def result(values=None, *, snapshot="SNAP2", grid=True, **projection) -> BatchResult:
    values = np.array([T_EXACT, as_ns(scan(1))]) if values is None else values
    return BatchResult(
        outcomes={},
        snapshot_id=snapshot,
        committed=True,
        values=values,
        trimmed=0,
        initialised=True,
        statics=statics(**projection) if grid else {},
        spatial_dims=("y", "x") if grid else (),
    )


def collection(**over) -> dict:
    doc = {
        "type": "Collection",
        "id": "goes-cube",
        "stac_version": "1.0.0",
        "stac_extensions": ["https://stac-extensions.github.io/scientific/v1.0.0/schema.json"],
        "title": "GOES-19 C13 cube",
        "description": "a user's words",
        "license": "proprietary",
        "keywords": ["goes"],
        "links": [],
        "extent": {
            "spatial": {"bbox": [[-150.0, 10.0, -50.0, 60.0]]},
            "temporal": {"interval": [[None, None]]},
        },
        "assets": {"thumbnail": {"href": "https://example.com/t.png", "roles": ["thumbnail"]}},
    }
    return {**doc, **over}


def merged(content=None, res=None) -> dict:
    return merge_collection(
        collection() if content is None else content,
        config=CONFIG,
        result=result() if res is None else res,
        sink_id="sink-1",
        href=HREF,
        prefixes=PREFIXES,
    )


def test_the_href_is_the_repository_prefix():
    assert cube_href("platform", "goes-cube") == HREF


def test_time_values_are_exact_nanosecond_strings():
    assert time_strings(np.array([T_EXACT])) == ["2026-10-03T17:02:36.714359936Z"]
    assert time_strings(np.array([], dtype="datetime64[ns]")) == []
    assert time_strings(np.array([1.0, 2.0])) is None


def test_the_document_matches_the_spec_shape():
    doc = merged()
    projjson = crs_projjson(GOES_PROJECTION)
    first, last = "2026-10-03T17:02:36.714359936Z", "2026-10-03T17:05:00.000000000Z"
    assert doc["assets"]["cube"] == {
        "href": HREF,
        "type": CUBE_MEDIA_TYPE,
        "roles": ["data", "references", "virtual", "latest-version"],
        "title": "Virtual cube",
        "version": "SNAP2",
        "stac_higher:virtual_chunk_prefixes": ["s3://noaa-goes19/"],
        "stac_higher:cube_sink_id": "sink-1",
        "stac_higher:time_values": [first, last],
    }
    assert doc["extent"]["temporal"] == {"interval": [[first, last]]}
    dims = doc["cube:dimensions"]
    assert dims["t"] == {"type": "temporal", "extent": [first, last]}
    assert dims["x"]["type"] == "spatial" and dims["x"]["axis"] == "x"
    assert dims["y"]["axis"] == "y"
    # scan angles (radians) x perspective_point_height = the metres the cube-server serves
    assert dims["x"]["extent"] == pytest.approx([-0.1 * H, 0.1 * H], rel=1e-6)
    assert dims["y"]["extent"] == pytest.approx([0.0, 0.08 * H], rel=1e-6)
    assert dims["x"]["reference_system"] == projjson
    assert projjson["type"] == "ProjectedCRS"
    assert "Geostationary" in json.dumps(projjson["conversion"]["method"])
    assert DATACUBE_EXTENSION in doc["stac_extensions"]


def test_only_the_cube_keys_change():
    before = collection()
    doc = merged(copy.deepcopy(before))
    for key in ("title", "description", "keywords", "license", "links", "id"):
        assert doc[key] == before[key]
    assert doc["assets"]["thumbnail"] == before["assets"]["thumbnail"]
    assert doc["extent"]["spatial"] == before["extent"]["spatial"]
    assert doc["stac_extensions"][0] == before["stac_extensions"][0]
    assert before == collection()  # the input is not mutated


def test_a_removed_asset_and_dimensions_come_back():
    content = merged()
    del content["assets"]["cube"]
    del content["cube:dimensions"]
    content["stac_extensions"].remove(DATACUBE_EXTENSION)
    assert merged(content) == merged()


def test_the_datacube_extension_is_listed_once_at_the_current_version():
    old = "https://stac-extensions.github.io/datacube/v2.1.0/schema.json"
    doc = merged(collection(stac_extensions=[old, DATACUBE_EXTENSION, "x"]))
    assert doc["stac_extensions"] == ["x", DATACUBE_EXTENSION]


def test_malformed_user_keys_are_replaced_not_raised():
    doc = merged(collection(assets=["not", "a", "dict"], extent=None, stac_extensions="x"))
    assert set(doc["assets"]) == {"cube"}
    assert doc["extent"]["temporal"]["interval"][0][0].startswith("2026-10-03T17:02:36")
    assert doc["stac_extensions"] == [DATACUBE_EXTENSION]


def test_a_cube_trimmed_to_zero_steps():
    doc = merged(res=result(np.array([], dtype="datetime64[ns]")))
    assert doc["assets"]["cube"]["stac_higher:time_values"] == []
    assert doc["cube:dimensions"]["t"]["extent"] == [None, None]
    assert doc["extent"]["temporal"] == {"interval": [[None, None]]}
    assert doc["cube:dimensions"]["x"]["axis"] == "x"


def test_a_result_without_the_grid_keeps_the_documents_spatial_dimensions():
    published = merged()
    trimmed = merged(published, result(np.array([as_ns(scan(1))]), snapshot="SNAP3", grid=False))
    assert trimmed["cube:dimensions"]["x"] == published["cube:dimensions"]["x"]
    assert trimmed["cube:dimensions"]["y"] == published["cube:dimensions"]["y"]
    assert trimmed["cube:dimensions"]["t"]["extent"][0] == "2026-10-03T17:05:00.000000000Z"
    assert trimmed["assets"]["cube"]["version"] == "SNAP3"


@pytest.mark.parametrize(
    "projection",
    [
        {"sweep_angle_axis": "z"},
        {"longitude_of_projection_origin": "east"},
        {"semi_major_axis": float("nan")},
    ],
)
def test_an_unusable_projection_drops_only_the_reference_system(projection):
    dims = merged(res=result(**projection))["cube:dimensions"]
    assert "reference_system" not in dims["x"]
    assert dims["x"]["extent"] == pytest.approx([-0.1 * H, 0.1 * H], rel=1e-6)


def test_a_geostationary_grid_without_its_height_has_no_spatial_dimensions():
    res = result()
    gm = {k: v for k, v in GOES_PROJECTION.items() if k != "perspective_point_height"}
    res.statics["goes_imager_projection"] = StaticSpec(np.array(0, "i4"), canonical_attrs(gm))
    assert set(merged(res=res)["cube:dimensions"]) == {"t"}


def test_the_inverse_flattening_stands_in_for_the_semi_minor_axis():
    gm = {k: v for k, v in GOES_PROJECTION.items() if k != "semi_minor_axis"}
    assert crs_projjson(gm)["type"] == "ProjectedCRS"


def test_a_non_time_append_dim_leaves_the_temporal_extent_alone():
    doc = merged(res=result(np.array([1.0, 2.0])))
    assert "stac_higher:time_values" not in doc["assets"]["cube"]
    assert doc["extent"]["temporal"] == {"interval": [[None, None]]}
    assert "t" not in doc["cube:dimensions"]


def plan(content, recorded, res=None):
    return plan_publish(
        content,
        recorded,
        sink_id="sink-1",
        config=CONFIG,
        result=result() if res is None else res,
        href=HREF,
    )


def test_only_the_recorded_tip_is_published():
    outcome, doc = plan(collection(), Recorded("SNAP2", PREFIXES))
    assert outcome == PUBLISHED and doc == merged()
    assert plan(collection(), Recorded("SNAP3", PREFIXES)) == (SUPERSEDED, None)
    assert plan(collection(), Recorded(None, ())) == (SUPERSEDED, None)
    assert plan(collection(), None) == (SUPERSEDED, None)  # the sink is gone


def test_the_recorded_prefixes_are_published():
    _, doc = plan(collection(), Recorded("SNAP2", ("s3://b/", "s3://a/")))
    assert doc["assets"]["cube"]["stac_higher:virtual_chunk_prefixes"] == ["s3://a/", "s3://b/"]


def test_a_missing_collection_and_an_unchanged_document_write_nothing():
    assert plan(None, Recorded("SNAP2", PREFIXES)) == (MISSING_COLLECTION, None)
    assert plan(merged(), Recorded("SNAP2", PREFIXES)) == (UNCHANGED, None)
    # through a JSON round trip, as pgstac hands the document back
    assert plan(json.loads(json.dumps(merged())), Recorded("SNAP2", PREFIXES)) == (UNCHANGED, None)


def test_hdf5_attribute_types_read_like_plain_values():
    # h5py hands real GOES attributes back as one-element arrays and bytes
    raw = {
        k: (np.array([v]) if isinstance(v, float) else v.encode() if isinstance(v, str) else v)
        for k, v in GOES_PROJECTION.items()
    }
    res = result()
    res.statics["goes_imager_projection"] = StaticSpec(np.array(0, "i4"), canonical_attrs(raw))
    assert merged(res=res)["cube:dimensions"] == merged()["cube:dimensions"]


def test_a_renamed_asset_key_or_a_recreated_sink_leaves_no_stale_cube_asset():
    # asset_key is not layout (app lib/cubes/schemas.ts layoutChanged): it can
    # change after a publish. A deleted sink's asset stays on the collection
    # (spec §7), and cube_collection_id is UNIQUE, so any cube asset another
    # sink id wrote here is stale. Neither may stay behind frozen.
    before = merged()
    before["assets"]["old-sink"] = {"href": "x", "stac_higher:cube_sink_id": "sink-0"}
    renamed = parse_cube_sink_config({**GOES_CONFIG, "asset_key": "c13"})
    doc = merge_collection(
        before, config=renamed, result=result(), sink_id="sink-1", href=HREF, prefixes=PREFIXES
    )
    assert set(doc["assets"]) == {"thumbnail", "c13"}
    assert doc["assets"]["c13"]["stac_higher:cube_sink_id"] == "sink-1"


def test_a_nat_step_never_reaches_the_document():
    # pgstac's generated datetime columns cannot cast "NaTZ": a deterministic
    # DB error would hold the batch's rows pending for good
    values = np.array([T_EXACT, np.datetime64("NaT", "ns")])
    assert time_strings(values) == ["2026-10-03T17:02:36.714359936Z"]
    doc = merged(res=result(values))
    assert doc["extent"]["temporal"]["interval"] == [[time_strings(values)[0]] * 2]


def test_a_result_without_the_grid_on_a_document_without_it_warns(caplog):
    no_dims = {k: v for k, v in merged().items() if k != "cube:dimensions"}
    res = result(np.array([as_ns(scan(1))]), snapshot="SNAP3", grid=False)
    with caplog.at_level("WARNING", logger="pipeline.cubes.collection"):
        doc = merged(no_dims, res)
    assert set(doc["cube:dimensions"]) == {"t"}
    assert "no spatial dimensions" in caplog.text
