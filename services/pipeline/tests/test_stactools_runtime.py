"""The stactools runtime wrapper (X-2, X-queue spec §6) — the platform half.

``stac_higher_stactools`` ships in the stactools runtime image, not in this
package; it is on the test path via ``pythonpath`` in pyproject. Nothing here
needs a stactools package installed: the merge rules, the run loop and the
registry reader are exercised with plain dicts and doubles, and the merged
output is checked against the REAL finalize gate (``check_extract_output``)
so the wrapper and the platform cannot drift apart on what "immutable" means.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from stac_higher_stactools import merge, registry, runner

from pipeline.finalize.extract_run import check_extract_output
from pipeline.process import builtin as pipeline_builtin

REPO = Path(__file__).resolve().parents[3]
FIXTURE = REPO / "tests" / "contract-fixtures" / "builtin-extractors.json"
RUNTIME = REPO / "services" / "process-runtime"


# ---------------------------------------------------------------------------
# Registry: both readers, one derivation
# ---------------------------------------------------------------------------


def test_registry_path_prefers_the_env_override_then_the_checkout(monkeypatch, tmp_path):
    monkeypatch.delenv(registry.ENV_VAR, raising=False)
    assert registry.registry_path() == FIXTURE
    copy = tmp_path / "r.json"
    copy.write_text(FIXTURE.read_text())
    monkeypatch.setenv(registry.ENV_VAR, str(copy))
    assert registry.registry_path() == copy
    assert len(registry.load_registry()) == 11


def test_pipeline_loader_follows_the_same_env_var(monkeypatch, tmp_path):
    monkeypatch.delenv(pipeline_builtin.REGISTRY_ENV_VAR, raising=False)
    assert pipeline_builtin.builtin_registry_path() == FIXTURE
    assert pipeline_builtin.REGISTRY_ENV_VAR == registry.ENV_VAR
    copy = tmp_path / "r.json"
    copy.write_text(FIXTURE.read_text())
    monkeypatch.setenv(pipeline_builtin.REGISTRY_ENV_VAR, str(copy))
    assert pipeline_builtin.builtin_registry_path() == copy
    assert len(pipeline_builtin.load_builtin_registry()) == 11
    copy.write_text("{")
    with pytest.raises(pipeline_builtin.BuiltinRegistryError, match="could not read"):
        pipeline_builtin.load_builtin_registry()


def test_both_readers_derive_the_same_modules_and_adapters():
    runtime_entries = {e.id: e for e in registry.load_registry(FIXTURE)}
    pipeline_entries = pipeline_builtin.parse_builtin_extractors(json.loads(FIXTURE.read_text()))
    assert set(runtime_entries) == {e.id for e in pipeline_entries}
    for entry in pipeline_entries:
        mirror = runtime_entries[entry.id]
        assert mirror.module == entry.module
        assert mirror.adapter == entry.adapter
        assert mirror.version == entry.version
        assert mirror.supports == entry.supports


def test_every_registry_entry_has_an_adapter_module():
    for entry in registry.load_registry(FIXTURE):
        assert importlib.util.find_spec(entry.adapter_module) is not None, entry.adapter_module


def test_unknown_builtin_id_names_the_known_ones():
    entries = registry.load_registry(FIXTURE)
    with pytest.raises(registry.RegistryError, match="stactools-goes"):
        registry.find_entry("stactools-nope", entries)


def test_registry_reader_rejects_the_unusable():
    with pytest.raises(registry.RegistryError, match="array"):
        registry.parse_registry({"extractors": "x"})
    with pytest.raises(registry.RegistryError, match="stactools-"):
        registry.parse_registry(
            {
                "extractors": [
                    {
                        "id": "x",
                        "package": "requests",
                        "version": "1",
                        "adapter": "x",
                        "supports": "single_file",
                    }
                ]
            }
        )


# ---------------------------------------------------------------------------
# Merge: the §6.1 rules
# ---------------------------------------------------------------------------


def _draft() -> dict:
    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": "draft-1",
        "collection": "goes19-abi",
        "geometry": None,
        "bbox": None,
        "properties": {"datetime": None, "platform:ledger": "kept"},
        "links": [],
        "assets": {
            "scene": {"href": "/api/assets/goes19-abi/draft-1/scene.nc", "roles": ["data"]},
            "sidecar": {"href": "/api/assets/goes19-abi/draft-1/scene.idx"},
        },
    }


STAGED = {"scene": "/tmp/run/draft-1/scene.nc", "sidecar": "/tmp/run/draft-1/scene.idx"}


def _produced() -> dict:
    return {
        "type": "Feature",
        "stac_version": "1.1.0",
        "id": "OR_ABI-package-minted",
        "collection": "somewhere-else",
        "geometry": {"type": "Point", "coordinates": [-75.0, 0.0]},
        "bbox": [-75.0, 0.0, -75.0, 0.0],
        "stac_extensions": ["https://stac-extensions.github.io/projection/v2.0.0/schema.json"],
        "properties": {
            "datetime": "2026-09-04T12:00:00Z",
            "platform": "goes-19",
            "goes:mode": "6",
        },
        "links": [{"rel": "self", "href": "/tmp/run/draft-1/item.json"}],
        "assets": {
            "nc": {
                "href": "/tmp/run/draft-1/scene.nc",
                "type": "application/netcdf",
                "roles": ["data", "source"],
                "raster:bands": [{"data_type": "int16"}],
            },
            "index": {"href": "https://archive.example/2026/scene.idx", "type": "text/plain"},
            "cog": {"href": "/tmp/run/draft-1/scene_C02.tif", "type": "image/tiff"},
            "thumb": {"href": "https://example.com/thumb.png", "roles": ["thumbnail"]},
        },
    }


def test_merge_keeps_identity_and_hrefs_and_copies_the_rest():
    draft = _draft()
    result = merge.merge_item(draft, _produced(), STAGED)
    item = result.item
    assert check_extract_output(draft, item) is None
    assert item["id"] == "draft-1" and item["collection"] == "goes19-abi"
    assert item["stac_version"] == "1.0.0" and item["links"] == []
    assert item["properties"]["datetime"] == "2026-09-04T12:00:00Z"
    assert item["properties"]["platform"] == "goes-19"
    assert item["properties"]["platform:ledger"] == "kept"  # overlay, not replace
    assert item["geometry"] == {"type": "Point", "coordinates": [-75.0, 0.0]}
    assert item["bbox"] == [-75.0, 0.0, -75.0, 0.0]
    assert item["stac_extensions"] == [
        "https://stac-extensions.github.io/projection/v2.0.0/schema.json"
    ]
    # Matched by full path: metadata gained, href kept.
    scene = item["assets"]["scene"]
    assert scene["href"] == "/api/assets/goes19-abi/draft-1/scene.nc"
    assert scene["type"] == "application/netcdf"
    assert scene["roles"] == ["data", "source"]
    assert scene["raster:bands"] == [{"data_type": "int16"}]
    # Matched by basename (the package emitted the archive URL).
    assert item["assets"]["sidecar"] == {
        "href": "/api/assets/goes19-abi/draft-1/scene.idx",
        "type": "text/plain",
    }
    # Added assets dropped and reported; no staged path anywhere in the output.
    assert set(item["assets"]) == {"scene", "sidecar"}
    assert len(result.dropped) == 2
    assert any(line.startswith("cog (") for line in result.dropped)
    assert any(line.startswith("thumb (") for line in result.dropped)
    assert "/tmp/run" not in json.dumps(item)


def test_merge_fills_datetime_from_start_datetime_and_keeps_draft_geometry():
    draft = _draft()
    draft["geometry"] = {"type": "Point", "coordinates": [1.0, 2.0]}
    draft["bbox"] = [1.0, 2.0, 1.0, 2.0]
    produced = _produced()
    produced["geometry"] = None
    produced["bbox"] = None
    produced["properties"] = {
        "datetime": None,
        "start_datetime": "2021-12-31T00:00:00Z",
        "end_datetime": "2022-01-01T00:00:00Z",
    }
    item = merge.merge_item(draft, produced, STAGED).item
    assert item["properties"]["datetime"] == "2021-12-31T00:00:00Z"
    assert item["geometry"] == {"type": "Point", "coordinates": [1.0, 2.0]}
    assert item["bbox"] == [1.0, 2.0, 1.0, 2.0]
    assert check_extract_output(draft, item) is None


def test_merge_leaves_a_null_datetime_null_when_the_package_gave_nothing():
    # The finalize gate refuses it — the wrapper must not invent one.
    produced = _produced()
    produced["properties"] = {"datetime": None}
    item = merge.merge_item(_draft(), produced, STAGED).item
    assert item["properties"]["datetime"] is None
    assert check_extract_output(_draft(), item) is not None


def test_merge_matches_each_draft_asset_at_most_once_and_refuses_junk():
    produced = _produced()
    produced["assets"]["nc_again"] = {"href": "file:///tmp/run/draft-1/scene.nc", "title": "dup"}
    result = merge.merge_item(_draft(), produced, STAGED)
    assert "title" not in result.item["assets"]["scene"]
    assert any("already matched" in line for line in result.dropped)
    with pytest.raises(merge.MergeError):
        merge.merge_item({"id": "x"}, produced, STAGED)
    with pytest.raises(merge.MergeError):
        merge.merge_item(_draft(), None, STAGED)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# run(): the extractor contract end to end, with doubles
# ---------------------------------------------------------------------------


class FakeS3:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = dict(objects)
        self.written: dict[str, bytes] = {}

    def get_object(self, Bucket, Key):
        import io

        return {"Body": io.BytesIO(self.objects[f"{Bucket}/{Key}"])}

    def download_file(self, bucket, key, filename):
        Path(filename).write_bytes(self.objects[f"{bucket}/{key}"])

    def put_object(self, Bucket, Key, Body):
        self.written[f"{Bucket}/{Key}"] = Body


def _manifest(items):
    return {
        "version": 1,
        "run_id": "r1",
        "process_id": "p1",
        "batch_id": "b1",
        "kind": "extract",
        "items": items,
    }


def _entry(item_id: str, filename: str):
    return {
        "collection": "c",
        "op": "insert",
        "item": {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": item_id,
            "collection": "c",
            "geometry": None,
            "bbox": None,
            "properties": {"datetime": None},
            "links": [],
            "assets": {"data": {"href": f"https://src.example/{filename}"}},
        },
        "assets": {
            "data": {
                "bucket": "stac-higher",
                "key": f"staging/runs/r1/inputs/b1/{item_id}/{filename}",
                "staged": True,
                "href": f"https://src.example/{filename}",
            }
        },
    }


@pytest.fixture
def run_env(monkeypatch):
    monkeypatch.setenv("STAC_HIGHER_OUTPUT_BUCKET", "stac-higher")
    monkeypatch.setenv("STAC_HIGHER_OUTPUT_PREFIX", "staging/runs/r1/")
    monkeypatch.setenv("STAC_HIGHER_INPUT_MANIFEST", "staging/runs/r1/inputs/b1/manifest.json")
    monkeypatch.delenv(registry.ENV_VAR, raising=False)


def _adapter_that(fn):
    def create(paths, draft):
        return fn(paths, draft)

    return create


def test_run_stages_by_basename_calls_the_adapter_and_writes_per_item(run_env, monkeypatch):
    seen: list[tuple[dict, str]] = []

    def build(paths, draft):
        path = paths["data"]
        seen.append(({k: p.name for k, p in paths.items()}, path.read_text()))
        return {
            "id": "minted",
            "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
            "bbox": [0.0, 0.0, 0.0, 0.0],
            "properties": {"datetime": "2026-01-01T00:00:00Z"},
            "assets": {"x": {"href": str(path), "type": "application/netcdf"}},
        }

    monkeypatch.setattr(runner, "load_adapter", lambda entry: _adapter_that(build))
    s3 = FakeS3(
        {
            "stac-higher/staging/runs/r1/inputs/b1/manifest.json": json.dumps(
                _manifest([_entry("a", "OR_a.nc"), _entry("b", "OR_b.nc")])
            ).encode(),
            "stac-higher/staging/runs/r1/inputs/b1/a/OR_a.nc": b"A",
            "stac-higher/staging/runs/r1/inputs/b1/b/OR_b.nc": b"B",
        }
    )
    assert runner.run("stactools-goes", s3=s3) == 2
    assert seen == [({"data": "OR_a.nc"}, "A"), ({"data": "OR_b.nc"}, "B")]
    assert set(s3.written) == {
        "stac-higher/staging/runs/r1/a.json",
        "stac-higher/staging/runs/r1/b.json",
    }
    a = json.loads(s3.written["stac-higher/staging/runs/r1/a.json"])
    assert a["id"] == "a" and a["properties"]["datetime"] == "2026-01-01T00:00:00Z"
    assert a["assets"]["data"] == {
        "href": "https://src.example/OR_a.nc",
        "type": "application/netcdf",
    }


def test_run_fails_per_item_and_dies_only_when_nothing_landed(run_env, monkeypatch, capsys):
    def build(paths, draft):
        if draft["id"] == "bad":
            raise ValueError("not a product file")
        return {
            "geometry": {"type": "Point", "coordinates": [0, 0]},
            "properties": {"datetime": "2026-01-01T00:00:00Z"},
            "assets": {},
        }

    monkeypatch.setattr(runner, "load_adapter", lambda entry: _adapter_that(build))
    objects = {
        "stac-higher/staging/runs/r1/inputs/b1/manifest.json": json.dumps(
            _manifest([_entry("bad", "x.nc"), _entry("good", "y.nc")])
        ).encode(),
        "stac-higher/staging/runs/r1/inputs/b1/bad/x.nc": b"",
        "stac-higher/staging/runs/r1/inputs/b1/good/y.nc": b"",
    }
    s3 = FakeS3(objects)
    assert runner.run("stactools-goes", s3=s3) == 1
    assert list(s3.written) == ["stac-higher/staging/runs/r1/good.json"]
    assert "bad: FAILED ValueError: not a product file" in capsys.readouterr().out

    only_bad = FakeS3(
        {
            **objects,
            "stac-higher/staging/runs/r1/inputs/b1/manifest.json": json.dumps(
                _manifest([_entry("bad", "x.nc")])
            ).encode(),
        }
    )
    with pytest.raises(SystemExit) as exc:
        runner.run("stactools-goes", s3=only_bad)
    assert exc.value.code == 1
    assert only_bad.written == {}


def test_run_refuses_a_transform_manifest_and_an_unknown_id(run_env, monkeypatch):
    monkeypatch.setattr(runner, "load_adapter", lambda entry: _adapter_that(lambda p, d: {}))
    s3 = FakeS3(
        {
            "stac-higher/staging/runs/r1/inputs/b1/manifest.json": json.dumps(
                {**_manifest([]), "kind": "transform"}
            ).encode()
        }
    )
    with pytest.raises(SystemExit, match="kind='transform'"):
        runner.run("stactools-goes", s3=s3)
    with pytest.raises(registry.RegistryError):
        runner.run("stactools-nope", s3=s3)


def test_run_with_an_empty_batch_writes_nothing_and_succeeds(run_env, monkeypatch):
    monkeypatch.setattr(runner, "load_adapter", lambda entry: _adapter_that(lambda p, d: {}))
    s3 = FakeS3(
        {"stac-higher/staging/runs/r1/inputs/b1/manifest.json": json.dumps(_manifest([])).encode()}
    )
    assert runner.run("stactools-goes", s3=s3) == 0


# ---------------------------------------------------------------------------
# Image and packaging lockstep (text pins, the test_process_runtime_image style)
# ---------------------------------------------------------------------------

STACTOOLS_DOCKERFILE = (RUNTIME / "Dockerfile.stactools").read_text()


def test_stactools_image_extends_the_base_and_keeps_its_posture():
    assert "FROM ${BASE_IMAGE}" in STACTOOLS_DOCKERFILE
    assert "USER runner" in STACTOOLS_DOCKERFILE.rsplit("RUN", 1)[1]
    assert "VOLUME" not in STACTOOLS_DOCKERFILE


def test_stactools_image_runs_the_registry_driven_smoke_before_dropping_root():
    build_steps = STACTOOLS_DOCKERFILE
    assert "python -m stac_higher_stactools.smoke" in build_steps
    assert build_steps.index("stac_higher_stactools.smoke") < build_steps.rindex("USER runner")


def test_stactools_image_pins_what_the_packages_need_beyond_the_registry():
    # pkg_resources left setuptools in 81; eight of the eleven still import it.
    assert '"setuptools<81"' in STACTOOLS_DOCKERFILE
    # Three packages cap pystac below the platform pin (I-109): the override,
    # not a second pystac. The same override the pipeline's uv config carries.
    assert "--override" in STACTOOLS_DOCKERFILE and "pystac==1.15.1" in STACTOOLS_DOCKERFILE
    pyproject = (REPO / "services" / "pipeline" / "pyproject.toml").read_text()
    assert 'override-dependencies = ["pystac==1.15.1"]' in pyproject
    assert '"setuptools<81"' in pyproject


def test_the_registry_reaches_every_image_through_the_fixtures_context():
    assert "COPY --from=fixtures builtin-extractors.json" in STACTOOLS_DOCKERFILE
    assert (
        "STAC_HIGHER_BUILTIN_REGISTRY=/opt/stac-higher/builtin-extractors.json"
        in STACTOOLS_DOCKERFILE
    )
    pipeline_dockerfile = (REPO / "services" / "pipeline" / "Dockerfile").read_text()
    assert "COPY --from=fixtures builtin-extractors.json" in pipeline_dockerfile
    assert "STAC_HIGHER_BUILTIN_REGISTRY=/app/share/builtin-extractors.json" in pipeline_dockerfile
    compose = (REPO / "docker-compose.yml").read_text()
    assert "fixtures: ./tests/contract-fixtures" in compose
    bake = (RUNTIME / "docker-bake.hcl").read_text()
    assert 'fixtures = "tests/contract-fixtures"' in bake
    assert 'base     = "target:runtime"' in bake
    containers = (REPO / ".github" / "workflows" / "containers.yml").read_text()
    assert "build-contexts: fixtures=tests/contract-fixtures" in containers
    assert "docker/bake-action" in containers
