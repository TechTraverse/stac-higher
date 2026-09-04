# stactools adapter fixtures (X-2)

Sample product files the built-in extractor library's adapters
(`services/process-runtime/stac_higher_stactools/adapters/`) are unit-tested
against in `tests/test_stactools_adapters.py` — each one the package's OWN
test fixture, taken verbatim from its repository at the tag the registry pins
(`tests/contract-fixtures/builtin-extractors.json`), so a test failure means
the platform's call into the package is wrong, not the sample. Nothing here
is downloaded at test time; the adapter tests need the `stactools` extra
(`uv sync --extra dev --extra stactools`) and skip without it.

| Directory | Package · tag | Source path in the package repo | Notes |
|---|---|---|---|
| `goes/` | stactools-goes v0.1.8 | `tests/data-files/OR_ABI-L2-LSTM2-M6_G16_….nc` | 173 KB LST mesoscale scene |
| `goes_glm/` | stactools-goes-glm v0.2.4 | `tests/data-files/OR_GLM-L2-LCFA_G17_s20200160612000_….nc` | the smallest of its ten |
| `noaa_hrrr/` | — | — | **hand-written**: the package keeps no files in its repo (its tests fetch from the archive). `hrrr.t12z.wrfsfcf06.grib2.idx` is twelve lines in the archive's `.idx` format; the `.grib2` beside it is a 4-byte stand-in, since `create_item_from_idx_df` never opens it |
| `noaa_mrms_qpe/` | stactools-noaa-mrms-qpe v0.3.1 | `tests/data-files/GUAM/MRMS_MultiSensor_QPE_01H_Pass1_00.00_20220601-120000.grib2.gz` + `.json` | `expected.json` is the package's own expected item |
| `noaa_cdr/` | stactools-noaa-cdr v0.2.1 | `tests/data-files/seaice_conc_daily_sh_20211231_f17_v04r00.nc` | |
| `modis/` | stactools-modis v0.2.0 | `tests/data-files/MOD13Q1.A2022017.h12v11.061.2022034232400.hdf.xml` | metadata only — the 1 MB `.hdf` is not vendored, so the adapter's XML-only path is what is tested |
| `landsat/` | stactools-landsat v0.5.0 | `tests/data-files/assets4/…_MTL.xml` + `…_ANG.txt` | the angle file replaces the USGS STAC geometry lookup (network) |
| `naip/` | stactools-naip v0.5.0 | `tests/data-files/m_3907864_sw_17_060_20210912_downsampled.tif` + `…_20211220.xml` | |
| `sentinel2/` | stactools-sentinel2 v0.8.0 | `tests/data-files/S2A_MSIL2A_20230821T221941_N0509_R029_T01KAB_20230822T021825.SAFE/` (`manifest.safe`, `MTD_MSIL2A.xml`, `GRANULE/…/MTD_TL.xml`) + `expected_output.json` | the three metadata files the package reads; the JP2 bands are not part of the package's fixture either |

Not here: **viirs** (the package fetches its samples from LAADS) and
**sentinel1** (the smallest GRD `.SAFE` in its repo is 3 MB of annotation
XML) — their adapters are tested with the package's `create_item` replaced by
a recording double, so what IS covered is the platform's half: which file is
picked and how the flattened group is rebuilt into a `.SAFE` tree. Both are
`access: credentialed` entries whose live gate is owed anyway (I-105).

Provenance: NOAA (GOES, GLM, MRMS, CDR, HRRR) and NASA (MODIS) products are
US-government works; the Landsat scene is USGS public domain; NAIP is USDA
public domain; the Sentinel-2 metadata is Copernicus Sentinel data under the
ESA free-and-open terms. All were redistributed by their stactools package
under Apache-2.0.
