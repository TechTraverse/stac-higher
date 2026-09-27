"""The scanner's summary, result and entrypoint (container-images spec §6).

The decisive property is at the bottom of each test: whatever the scanner
writes, the pipeline's own C-1 parser (the drain's reader) accepts it."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from stac_higher_scanner import scan as scan_module
from stac_higher_scanner.registry import RegistryError, Resolved
from stac_higher_scanner.scan import (
    EXIT_OK,
    EXIT_PLATFORM_ERROR,
    EXIT_SCAN_FAILED,
    ScanConfig,
    ScanRefused,
    Tools,
    failure_result,
    main,
    run_scan,
    syft_auth_env,
    tool_env,
)
from stac_higher_scanner.summary import (
    MAX_TOP,
    build_summary,
    findings_from_grype,
    published_dates,
)

from pipeline.images.scan_result import parse_scan_result

REPO = Path(__file__).resolve().parents[3]
PREFIX = "scans/7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f/0d9e8f7a-6b5c-4d3e-9f2a-1b0c9d8e7f6a/"


def match(vid, severity="High", *, package="libxml2", version="2.12.7", fixed=None,
          risk=0.1, epss=None, kev=False):
    vuln = {
        "id": vid,
        "severity": severity,
        "fix": {"versions": [fixed] if fixed else [], "state": "fixed" if fixed else "not-fixed"},
        "risk": risk,
    }
    if epss is not None:
        vuln["epss"] = [{"cve": vid, "epss": epss, "percentile": 0.9, "date": "2026-09-20"}]
    if kev:
        vuln["knownExploited"] = [{"cve": vid, "knownRansomwareCampaignUse": "Unknown"}]
    return {"vulnerability": vuln, "artifact": {"name": package, "version": version}}


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


def test_duplicate_matches_count_once_and_severities_are_normalized():
    doc = {
        "matches": [match("CVE-1", "Critical"), match("CVE-1", "Critical"), match("CVE-2", "Weird")]
    }
    findings = findings_from_grype(doc)
    assert [(f.id, f.severity) for f in findings] == [("CVE-1", "critical"), ("CVE-2", "unknown")]


def test_a_fix_counts_only_when_grype_says_fixed():
    doc = {"matches": [match("CVE-1", fixed="2.12.9"), match("CVE-2")]}
    summary = build_summary(doc)
    assert summary["counts"]["high"] == 2
    assert summary["fixed_counts"]["high"] == 1
    assert {f["id"]: f["fixed_in"] for f in summary["top"]} == {"CVE-1": "2.12.9", "CVE-2": None}


def test_nan_and_infinity_never_leave_the_scanner():
    """C-1's parser rejects non-finite numbers; the scanner must never emit one."""
    doc = {
        "matches": [
            match("CVE-1", risk=float("nan"), epss=float("inf")),
            match("CVE-2", risk=float("inf"), epss=2.5),
        ]
    }
    summary = build_summary(doc)
    json.dumps(summary, allow_nan=False)  # raises on NaN/inf
    by_id = {f["id"]: f for f in summary["top"]}
    assert by_id["CVE-1"]["risk"] == 0.0 and by_id["CVE-1"]["epss"] is None
    assert by_id["CVE-2"]["epss"] == 1.0
    assert summary["max_risk"] == 0.0


def test_kev_is_the_complete_sorted_list():
    doc = {"matches": [*(match(f"CVE-{i}", "Low", kev=True) for i in (3, 1, 2)), match("CVE-9")]}
    assert build_summary(doc)["kev"] == ["CVE-1", "CVE-2", "CVE-3"]


def test_top_favours_what_the_policy_blocks_on_over_raw_risk():
    """C-1 plan decision 11: evaluate() can only NAME what is in `top`. Thirty
    risky MEDIUMs must not push out a low-risk fixed HIGH with a high EPSS or
    an unfixed CRITICAL."""
    doc = {
        "matches": [
            *(match(f"CVE-M{i}", "Medium", package=f"p{i}", risk=0.9) for i in range(30)),
            match("CVE-H", "High", package="h", fixed="1.1", risk=0.01, epss=0.5),
            match("CVE-C", "Critical", package="c", risk=0.02),
        ]
    }
    top = build_summary(doc)["top"]
    ids = [f["id"] for f in top]
    assert len(top) == MAX_TOP == 25
    assert "CVE-H" in ids and "CVE-C" in ids
    # Emitted by risk, descending (the fixture's documented order).
    assert [f["risk"] for f in top] == sorted((f["risk"] for f in top), reverse=True)


def test_published_dates_come_from_the_grype_db(tmp_path):
    db = tmp_path / "vulnerability.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE vulnerability_handles (name TEXT, published_date DATETIME)")
        conn.executemany(
            "INSERT INTO vulnerability_handles VALUES (?, ?)",
            [("CVE-1", "2026-07-01 00:00:00+00:00"), ("cve-1", "2026-06-01 00:00:00+00:00")],
        )
    assert published_dates(db, ["CVE-1", "CVE-2"]) == {"cve-1": "2026-06-01"}
    assert published_dates(tmp_path / "missing.db", ["CVE-1"]) == {}
    summary = build_summary(
        {"matches": [match("CVE-1", "Critical")]}, published=lambda ids: {"cve-1": "2026-06-01"}
    )
    assert summary["top"][0]["published_at"] == "2026-06-01"


# ---------------------------------------------------------------------------
# run_scan end to end, with fake tools, store and registry
# ---------------------------------------------------------------------------


class FakeStore:
    def __init__(self, files=None):
        self.puts: dict[str, bytes] = {}
        self.json: dict[str, dict] = {}
        self.files = files or {}

    def put_file(self, key, path, content_type="application/json"):
        self.puts[key] = Path(path).read_bytes()

    def put_json(self, key, doc):
        self.json[key] = json.loads(json.dumps(doc, allow_nan=False))

    def get_file(self, key, path):
        Path(path).write_bytes(self.files[key])


class FakeTools:
    def __init__(self, grype_doc):
        self.grype_doc = grype_doc
        self.syft_calls: list[tuple[str, dict]] = []
        self.updated = False

    def syft(self, source, syft_json, cyclonedx, auth_env):
        self.syft_calls.append((source, auth_env))
        Path(syft_json).write_text("{}")
        Path(cyclonedx).write_text("{}")

    def grype(self, sbom, out):
        assert Path(sbom).exists()
        Path(out).write_text(json.dumps(self.grype_doc))

    def grype_db_update(self):
        self.updated = True
        return True

    def grype_db_status(self):
        return {"built": "2026-09-20T06:00:00Z", "path": "/nonexistent/6"}


RESOLVED = Resolved(
    digest="sha256:" + "a" * 64,
    platform_digest="sha256:" + "b" * 64,
    size_bytes=812,
    config={"user": "", "entrypoint": ["/entry.sh"], "cmd": None},
    platform={"os": "linux", "architecture": "amd64"},
)


class FakeClient:
    def __init__(self, reference, credentials=None):
        self.reference = reference
        self.credentials = credentials

    def resolve(self, tag, platform, max_bytes):
        return RESOLVED


def config(**overrides) -> ScanConfig:
    base = {
        "scan_id": "0d9e8f7a-6b5c-4d3e-9f2a-1b0c9d8e7f6a",
        "kind": "admission",
        "reference": "docker.io/library/python",
        "tag": "3.12-slim",
        "platform": "linux/amd64",
        "max_image_bytes": 4096 * 1024 * 1024,
        "allowed_registries": ("docker.io", "ghcr.io"),
        "bucket": "stac-higher",
        "prefix": PREFIX,
    }
    base.update(overrides)
    return ScanConfig(**base)


def test_an_admission_scan_writes_its_objects_and_a_result_the_pipeline_accepts(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SYFT_VERSION", "1.52.0")
    monkeypatch.setenv("GRYPE_VERSION", "0.119.0")
    store = FakeStore()
    tools = FakeTools({"matches": [match("CVE-1", "High", fixed="2.12.9", epss=0.3, risk=0.4)]})
    doc = run_scan(config(db_update=True), store, tools, tmp_path, client_factory=FakeClient)
    assert set(store.puts) == {
        f"{PREFIX}sbom.syft.json",
        f"{PREFIX}sbom.cdx.json",
        f"{PREFIX}findings.grype.json",
    }
    assert tools.syft_calls[0][0] == f"registry:docker.io/library/python@{RESOLVED.platform_digest}"
    assert tools.updated is True
    parsed = parse_scan_result(json.loads(json.dumps(doc, allow_nan=False)))
    assert parsed.error is None
    assert parsed.digest == RESOLVED.digest and parsed.size_bytes == 812
    assert parsed.sbom_ref == f"{PREFIX}sbom.syft.json"
    assert parsed.findings_ref == f"{PREFIX}findings.grype.json"
    assert parsed.scanner == {
        "syft": "1.52.0",
        "grype": "0.119.0",
        "db_built_at": "2026-09-20T06:00:00Z",
    }
    assert parsed.top[0].id == "CVE-1"


def test_a_rescan_reads_the_stored_sbom_and_echoes_the_rows_identity(tmp_path):
    sbom_key = "scans/7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f/older/sbom.syft.json"
    identity = {
        "digest": RESOLVED.digest,
        "platform_digest": RESOLVED.platform_digest,
        "platform": RESOLVED.platform,
        "size_bytes": 812,
        "config": RESOLVED.config,
    }
    store = FakeStore(files={sbom_key: b"{}"})
    tools = FakeTools({"matches": []})
    doc = run_scan(
        config(kind="rescan", sbom_key=sbom_key, identity=identity),
        store,
        tools,
        tmp_path,
        client_factory=FakeClient,
    )
    assert tools.syft_calls == []  # SBOM-only: nothing is pulled
    parsed = parse_scan_result(doc)
    assert parsed.kind == "rescan" and parsed.sbom_ref == sbom_key
    assert parsed.digest == RESOLVED.digest


def test_a_registry_outside_the_policy_is_refused_before_any_request(tmp_path):
    with pytest.raises(ScanRefused, match="registry_not_allowed"):
        run_scan(
            config(reference="quay.io/org/tool"),
            FakeStore(),
            FakeTools({"matches": []}),
            tmp_path,
            client_factory=lambda *a: pytest.fail("the registry must not be contacted"),
        )


def test_the_failure_result_is_the_c1_shape():
    doc = failure_result("admission", "docker.io/library/python", "3.12-slim", "x" * 5000)
    assert parse_scan_result(doc).error == "x" * 1000


def test_syft_gets_the_credential_for_the_right_authority():
    from stac_higher_scanner.registry import Credentials

    hub = config(credentials=Credentials("robot", "pat"))
    assert syft_auth_env(hub)["SYFT_REGISTRY_AUTH_AUTHORITY"] == "index.docker.io"
    ghcr = config(reference="ghcr.io/org/tool", credentials=Credentials("robot", "pat"))
    assert syft_auth_env(ghcr)["SYFT_REGISTRY_AUTH_AUTHORITY"] == "ghcr.io"
    assert syft_auth_env(config()) == {}


def test_the_tools_never_inherit_the_registry_or_storage_credentials():
    env = tool_env(
        {
            "PATH": "/usr/bin",
            "REGISTRY_PASSWORD": "pat",
            "AWS_SECRET_ACCESS_KEY": "s",
            "GRYPE_DB_CACHE_DIR": "/opt/grype-db",
        }
    )
    assert "REGISTRY_PASSWORD" not in env and "AWS_SECRET_ACCESS_KEY" not in env
    assert env["GRYPE_DB_VALIDATE_AGE"] == "false"
    assert env["GRYPE_DB_AUTO_UPDATE"] == "false"
    assert isinstance(Tools(env=env), Tools)


# ---------------------------------------------------------------------------
# main(): exit codes and the result it always tries to write
# ---------------------------------------------------------------------------


def _env(**overrides):
    base = {
        "STAC_HIGHER_SCAN_ID": "0d9e8f7a-6b5c-4d3e-9f2a-1b0c9d8e7f6a",
        "STAC_HIGHER_SCAN_KIND": "admission",
        "STAC_HIGHER_IMAGE_REF": "docker.io/library/python",
        "STAC_HIGHER_IMAGE_TAG": "3.12-slim",
        "STAC_HIGHER_PLATFORM": "linux/amd64",
        "STAC_HIGHER_MAX_IMAGE_BYTES": "4294967296",
        "STAC_HIGHER_ALLOWED_REGISTRIES": "docker.io,ghcr.io",
        "STAC_HIGHER_OUTPUT_BUCKET": "stac-higher",
        "STAC_HIGHER_OUTPUT_PREFIX": PREFIX,
        "REGISTRY_USERNAME": "robot",
        "REGISTRY_PASSWORD": "pat",
    }
    base.update(overrides)
    return base


def _use_store(monkeypatch, store) -> None:
    monkeypatch.setattr(
        scan_module.ObjectStore, "from_env", classmethod(lambda cls, env: store)
    )


def test_main_without_its_job_cannot_start(monkeypatch):
    _use_store(monkeypatch, FakeStore())
    assert main({"PATH": "/usr/bin"}) == EXIT_PLATFORM_ERROR


def test_main_writes_an_error_result_and_exits_1_when_the_scan_fails(monkeypatch):
    store = FakeStore()
    _use_store(monkeypatch, store)

    def boom(*args, **kwargs):
        raise RegistryError("HEAD manifests/3.12-slim on docker.io returned 404")

    monkeypatch.setattr(scan_module, "run_scan", boom)
    assert main(_env()) == EXIT_SCAN_FAILED
    result = store.json[f"{PREFIX}result.json"]
    parsed = parse_scan_result(result)
    assert parsed.error == "HEAD manifests/3.12-slim on docker.io returned 404"
    assert "pat" not in json.dumps(result)


def test_main_writes_the_result_and_exits_0(monkeypatch):
    store = FakeStore()
    _use_store(monkeypatch, store)
    written = {"version": 1, "error": None, "sentinel": 1}
    monkeypatch.setattr(scan_module, "run_scan", lambda *a, **k: written)
    assert main(_env()) == EXIT_OK
    assert store.json[f"{PREFIX}result.json"]["sentinel"] == 1


# ---------------------------------------------------------------------------
# the image: text pins (no test builds it -- CI's containers.yml does)
# ---------------------------------------------------------------------------

DOCKERFILE = (REPO / "services" / "image-scanner" / "Dockerfile").read_text()


def test_the_binaries_are_pinned_and_checksum_verified():
    for name in ("SYFT", "GRYPE"):
        assert re.search(rf"ARG {name}_VERSION=\d+\.\d+\.\d+", DOCKERFILE)
        for arch in ("AMD64", "ARM64"):
            assert re.search(rf"ARG {name}_SHA256_{arch}=[a-f0-9]{{64}}\b", DOCKERFILE)
    assert DOCKERFILE.count("sha256sum -c -") == 2


def test_the_db_is_baked_and_never_refused_for_its_age():
    assert "grype db update" in DOCKERFILE
    assert "GRYPE_DB_AUTO_UPDATE=false" in DOCKERFILE
    assert "GRYPE_DB_VALIDATE_AGE=false" in DOCKERFILE


def test_the_scanner_runs_as_a_non_root_user_with_no_volume():
    assert "USER scanner" in DOCKERFILE
    assert "VOLUME" not in DOCKERFILE


def test_boto3_is_the_pin_the_runtime_image_already_vouches_for():
    runtime = (REPO / "services" / "process-runtime" / "Dockerfile").read_text()
    pin = re.compile(r'"boto3==([0-9.]+)"')
    assert pin.search(DOCKERFILE).group(1) == pin.search(runtime).group(1)


def test_ci_and_bake_build_the_scanner():
    workflow = (REPO / ".github" / "workflows" / "containers.yml").read_text()
    assert "services/image-scanner/Dockerfile" in workflow
    bake = (REPO / "services" / "process-runtime" / "docker-bake.hcl").read_text()
    assert 'target "image-scanner"' in bake
    assert "stac-higher-image-scanner:local" in bake
