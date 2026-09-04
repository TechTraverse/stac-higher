"""One adapter per registry entry, against the package's own fixture (X-2,
X-queue spec §6/§10).

Needs the ``stactools`` extra (``uv sync --extra dev --extra stactools``);
skips without it. Every adapter is driven the way the wrapper drives it — a
flat directory of staged files under their original basenames plus a draft
whose hrefs carry the source path — and the result goes through the real
merge and the real finalize gate, so what is proved is not "the package
works" but "the platform's call into the package yields an item the platform
accepts". Fixture provenance: ``tests/data/stactools/README.md``.
"""

from __future__ import annotations

import json
import shutil
import warnings
from pathlib import Path

import pytest

pytest.importorskip("stactools")

from stac_higher_stactools import merge, smoke
from stac_higher_stactools.adapters import (
    _files,
    goes,
    goes_glm,
    landsat,
    modis,
    naip,
    noaa_cdr,
    noaa_hrrr,
    noaa_mrms_qpe,
    sentinel1,
    sentinel2,
    viirs,
)
from stac_higher_stactools.runner import to_dict

from pipeline.finalize.extract_run import check_extract_output

DATA = Path(__file__).resolve().parent / "data" / "stactools"
NOW = __import__("datetime").datetime(2026, 9, 4, tzinfo=__import__("datetime").UTC)

warnings.filterwarnings("ignore")


def _stage(tmp_path: Path, files: list[Path]) -> dict[str, Path]:
    """Copy fixture files flat into ``tmp_path`` — the wrapper's layout — and
    key them by stem, the way ITEMIZE keys a group's assets."""
    paths: dict[str, Path] = {}
    for source in files:
        target = tmp_path / source.name
        shutil.copy(source, target)
        stem = source.name.split(".", 1)[0]
        paths[stem if stem not in paths else f"{stem}_2"] = target
    return paths


def _draft(paths: dict[str, Path], href_base: str = "https://archive.example/bucket") -> dict:
    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": "draft",
        "collection": "col",
        "geometry": None,
        "bbox": None,
        "properties": {"datetime": None},
        "links": [],
        "assets": {key: {"href": f"{href_base}/{path.name}"} for key, path in paths.items()},
    }


def _through_platform(adapter, paths: dict[str, Path], draft: dict) -> merge.MergeResult:
    produced = to_dict(adapter.create(paths, draft))
    result = merge.merge_item(draft, produced, {k: str(p) for k, p in paths.items()})
    assert check_extract_output(draft, result.item) is None, check_extract_output(
        draft, result.item
    )
    item = result.item
    assert item["properties"]["datetime"] is not None
    assert item["geometry"] is not None
    staged_dir = str(paths[next(iter(paths))].parent)
    assert staged_dir not in json.dumps(item)
    return result


def test_smoke_passes_against_this_environment(capsys):
    # The same check the image build runs: every module and adapter imports,
    # and the installed versions equal the registry's pins.
    assert smoke.main() == 0, capsys.readouterr().out


def test_goes(tmp_path):
    paths = _stage(tmp_path, list((DATA / "goes").glob("*.nc")))
    result = _through_platform(goes, paths, _draft(paths))
    props = result.item["properties"]
    assert props["datetime"] == "2021-05-18T17:00:53.800000Z"
    assert props["platform"] == "GOES-16" and props["goes:mode"] == "6"
    assert "proj:wkt2" in props
    (asset,) = result.item["assets"].values()
    assert asset["type"] == "application/netcdf" and asset["roles"] == ["data"]
    assert result.dropped == ()


def test_goes_glm(tmp_path):
    paths = _stage(tmp_path, list((DATA / "goes_glm").glob("*.nc")))
    result = _through_platform(goes_glm, paths, _draft(paths))
    props = result.item["properties"]
    assert props["datetime"].startswith("2020-01-16T06:12:")
    assert props["goes:orbital_slot"] == "West"  # the package's enum, JSON-normalised
    (asset,) = result.item["assets"].values()
    assert "cube:variables" in asset
    # No geoparquet sidecar was written next to the file.
    assert sorted(p.name for p in tmp_path.iterdir()) == [next(iter(paths.values())).name]


def test_noaa_hrrr_from_the_idx_sidecar(tmp_path):
    paths = _stage(tmp_path, sorted((DATA / "noaa_hrrr").iterdir()))
    draft = _draft(paths, "https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.20220615/conus")
    result = _through_platform(noaa_hrrr, paths, draft)
    props = result.item["properties"]
    assert props["datetime"] == "2022-06-15T18:00:00Z"  # cycle 12z + 6 h
    assert props["forecast:reference_datetime"] == "2022-06-15T12:00:00Z"
    grib = next(a for a in result.item["assets"].values() if a["href"].endswith(".grib2"))
    idx = next(a for a in result.item["assets"].values() if a["href"].endswith(".idx"))
    assert "grib:messages" in grib and idx["roles"] == ["index"]
    assert result.dropped == ()
    assert noaa_hrrr.parse_parameters(
        "hrrr.t06z.wrfprsf12.ak.grib2", ["x/hrrr.20240101/alaska/y"]
    ) == {
        "region": "alaska",
        "product": "prs",
        "reference_datetime": __import__("datetime").datetime(2024, 1, 1, 6),
        "forecast_hour": 12,
    }
    with pytest.raises(_files.AdapterInputError, match=r"hrrr\.YYYYMMDD"):
        noaa_hrrr.parse_parameters("hrrr.t06z.wrfprsf12.grib2", ["/api/assets/c/i/x.grib2"])


def test_noaa_mrms_qpe(tmp_path):
    paths = _stage(tmp_path, list((DATA / "noaa_mrms_qpe").glob("*.gz")))
    draft = _draft(
        paths,
        "https://noaa-mrms-pds.s3.amazonaws.com/GUAM/MultiSensor_QPE_01H_Pass1_00.00/20220601",
    )
    result = _through_platform(noaa_mrms_qpe, paths, draft)
    props = result.item["properties"]
    expected = json.loads((DATA / "noaa_mrms_qpe" / "expected.json").read_text())
    assert props["datetime"] == expected["properties"]["datetime"] == "2022-06-01T12:00:00Z"
    assert props["noaa_mrms_qpe:region"] == "GUAM"
    assert result.item["bbox"] == expected["bbox"]
    (asset,) = result.item["assets"].values()
    assert asset["href"].endswith(".grib2.gz") and "raster:bands" in asset
    assert result.dropped == ()
    assert noaa_mrms_qpe.aoi_of(["https://x/ALASKA/y.grib2"]) == "ALASKA"
    assert noaa_mrms_qpe.aoi_of(["/api/assets/c/i/y.grib2"]) == "CONUS"


def test_noaa_cdr_datetime_comes_from_start_datetime(tmp_path):
    paths = _stage(tmp_path, list((DATA / "noaa_cdr").glob("*.nc")))
    result = _through_platform(noaa_cdr, paths, _draft(paths))
    props = result.item["properties"]
    assert props["datetime"] == props["start_datetime"] == "2021-12-31T00:00:00Z"
    assert props["processing:level"] == "L3"
    (asset,) = result.item["assets"].values()
    assert asset["type"] == "application/netcdf" and "created" in asset


def test_modis_from_metadata_only(tmp_path):
    paths = _stage(tmp_path, list((DATA / "modis").glob("*.xml")))
    result = _through_platform(modis, paths, _draft(paths))
    props = result.item["properties"]
    assert props["datetime"] == props["start_datetime"] == "2022-01-17T00:00:00Z"
    assert props["modis:tile-id"] == "51012011"
    (asset,) = result.item["assets"].values()
    assert asset["roles"] == ["metadata"]
    # The .hdf the package also lists is not in the group: dropped, not invented.
    assert len(result.dropped) == 1 and "hdf" in result.dropped[0]


def test_landsat_from_mtl_and_angle_file(tmp_path):
    paths = _stage(tmp_path, sorted((DATA / "landsat").iterdir()))
    result = _through_platform(landsat, paths, _draft(paths))
    props = result.item["properties"]
    assert props["datetime"].startswith("2013-04-19T")
    assert props["platform"] == "landsat-8" and props["landsat:wrs_path"] == "017"
    assert result.item["geometry"]["type"] == "Polygon"
    mtl = next(a for a in result.item["assets"].values() if a["href"].endswith("_MTL.xml"))
    assert mtl["roles"] == ["metadata"]
    # The band TIFs the package lists are not in the group: dropped.
    assert any("_SR_B4.TIF" in line for line in result.dropped)


def test_naip_state_and_year_come_from_the_archive_layout(tmp_path):
    paths = _stage(tmp_path, sorted((DATA / "naip").iterdir()))
    draft = _draft(paths, "https://naip-analytic.s3.amazonaws.com/va/2021/60cm/rgbir_cog/39078")
    result = _through_platform(naip, paths, draft)
    props = result.item["properties"]
    assert props["naip:state"] == "va" and props["naip:year"] == "2021"
    assert props["datetime"] == "2021-09-12T16:00:00Z"
    image = next(a for a in result.item["assets"].values() if a["href"].endswith(".tif"))
    assert "eo:bands" in image and image["roles"] == ["data"]
    assert result.dropped == ()
    with pytest.raises(_files.AdapterInputError, match="state"):
        naip.parameters("m_3907864_sw_17_060_20210912.tif", ["/api/assets/c/i/m.tif"])


S2 = "S2A_MSIL2A_20230821T221941_N0509_R029_T01KAB_20230822T021825.SAFE"


def _safe_group(tmp_path: Path, files: list[Path], root: Path, base: str) -> tuple[dict, dict]:
    """A SAFE product as the ingest hands it over: files flat, the tree only
    in the hrefs."""
    paths: dict[str, Path] = {}
    hrefs: dict[str, str] = {}
    for source in files:
        target = tmp_path / source.name
        shutil.copy(source, target)
        key = source.name
        paths[key] = target
        hrefs[key] = f"{base}/{source.relative_to(root.parent).as_posix()}"
    draft = _draft(paths)
    for key, href in hrefs.items():
        draft["assets"][key]["href"] = href
    return paths, draft


def test_sentinel2_rebuilds_the_safe_tree_from_hrefs(tmp_path):
    root = DATA / "sentinel2" / S2
    files = [p for p in root.rglob("*") if p.is_file()]
    paths, draft = _safe_group(tmp_path, files, root, "https://sentinel-s2-l2a.example/tiles")
    result = _through_platform(sentinel2, paths, draft)
    expected = json.loads((DATA / "sentinel2" / "expected_output.json").read_text())
    props = result.item["properties"]
    assert props["datetime"] == expected["properties"]["datetime"]
    assert props["eo:cloud_cover"] == expected["properties"]["eo:cloud_cover"]
    assert result.item["geometry"]["type"] == expected["geometry"]["type"]
    matched = {k: a for k, a in result.item["assets"].items() if a.get("roles")}
    assert set(matched) == {"manifest.safe", "MTD_MSIL2A.xml", "MTD_TL.xml"}
    assert all(a["roles"] == ["metadata"] for a in matched.values())
    # The band JP2s and the rest of the SAFE the package lists are not in the
    # group: dropped, and none of the tree's staged paths leaked.
    assert len(result.dropped) > 30
    assert all(
        a["href"].startswith("https://sentinel-s2-l2a.example/")
        for a in result.item["assets"].values()
    )


def test_safe_rebuild_refuses_hrefs_without_the_product_directory(tmp_path):
    root = DATA / "sentinel2" / S2
    paths, draft = _safe_group(tmp_path, [root / "manifest.safe"], root, "https://x")
    draft["assets"]["manifest.safe"]["href"] = "/api/assets/col/draft/manifest.safe"
    with pytest.raises(_files.AdapterInputError, match="reference-mode"):
        sentinel2.create(paths, draft)


def test_sentinel1_names_grd_and_hands_it_the_rebuilt_tree(tmp_path, monkeypatch):
    import pystac
    from stactools.sentinel1.grd import stac as grd_stac

    seen: list[str] = []

    def fake_create_item(granule_href, **kwargs):
        seen.append(granule_href)
        return pystac.Item(id="S1", geometry=None, bbox=None, datetime=NOW, properties={})

    monkeypatch.setattr(grd_stac, "create_item", fake_create_item)
    files = {
        "manifest.safe": "S1A_IW_GRDH_1SDV_X.SAFE/manifest.safe",
        "s1a-iw-grd-vv-001.xml": "S1A_IW_GRDH_1SDV_X.SAFE/annotation/s1a-iw-grd-vv-001.xml",
    }
    paths: dict[str, Path] = {}
    for name in files:
        (tmp_path / name).write_text("<x/>")
        paths[name] = tmp_path / name
    draft = _draft(paths, "https://sentinel-s1-l1c.example/GRD/2021")
    for name, rel in files.items():
        draft["assets"][name]["href"] = f"https://sentinel-s1-l1c.example/GRD/2021/{rel}"
    sentinel1.create(paths, draft)
    (root,) = seen
    assert root.endswith("S1A_IW_GRDH_1SDV_X.SAFE")
    assert (Path(root) / "annotation" / "s1a-iw-grd-vv-001.xml").read_text() == "<x/>"
    assert (Path(root) / "manifest.safe").is_symlink()


def test_viirs_hands_the_h5_to_the_package(tmp_path, monkeypatch):
    import pystac
    from stactools.viirs import stac as viirs_stac

    seen: list[str] = []

    def fake_create_item(h5_href, **kwargs):
        seen.append(h5_href)
        return pystac.Item(id="V", geometry=None, bbox=None, datetime=NOW, properties={})

    monkeypatch.setattr(viirs_stac, "create_item", fake_create_item)
    (tmp_path / "VNP09H1.A2022.h5").write_bytes(b"")
    (tmp_path / "VNP09H1.A2022.h5.xml").write_bytes(b"")
    paths = {"h5": tmp_path / "VNP09H1.A2022.h5", "xml": tmp_path / "VNP09H1.A2022.h5.xml"}
    viirs.create(paths, _draft(paths))
    assert seen == [str(tmp_path / "VNP09H1.A2022.h5")]


def test_pick_refuses_an_ambiguous_group(tmp_path):
    a, b = tmp_path / "a.nc", tmp_path / "b.nc"
    a.write_bytes(b"")
    b.write_bytes(b"")
    with pytest.raises(_files.AdapterInputError, match="found 2"):
        goes.create({"a": a, "b": b}, _draft({"a": a, "b": b}))
