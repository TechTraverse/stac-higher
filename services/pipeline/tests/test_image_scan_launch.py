"""One scan as a platform run (container-images spec §6.2): the scanner gets
exactly the process posture -- its own container, the policy's limits, the
scanner network, a credential for its own scan prefix, and nothing of the
platform's."""

from __future__ import annotations

import json

import pytest

from pipeline.config import Settings
from pipeline.images.policy import load_image_policy
from pipeline.images.repo import ImageRow
from pipeline.images.scan_launch import (
    RESULT_MAX_BYTES,
    SCANNER_RUN_KIND,
    build_scan_spec,
    execute_scan,
    read_scan_result,
    sbom_read_prefix,
)
from pipeline.images.scan_result import ScanResultError
from pipeline.process.credentials import RunCredentials
from pipeline.process.executor import ExitStatus, RegistryAuth
from pipeline.process.memory_executor import MemoryExecutor

IMG = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f"
SCAN = "0d9e8f7a-6b5c-4d3e-9f2a-1b0c9d8e7f6a"
OLD = "11111111-2222-4333-8444-555555555555"
DIGEST = "sha256:" + "a" * 64
POLICY = load_image_policy()

PENDING = ImageRow(id=IMG, reference="docker.io/library/python", tag_at_add="3.12-slim",
                   status="scanning")
APPROVED = ImageRow(
    id=IMG,
    reference="docker.io/library/python",
    tag_at_add="3.12-slim",
    status="approved",
    digest=DIGEST,
    platform_digest=DIGEST,
    platform={"os": "linux", "architecture": "amd64"},
    size_bytes=10,
    config={"user": "", "entrypoint": None, "cmd": None},
    sbom_ref=f"scans/{IMG}/{OLD}/sbom.syft.json",
)
CREDS = RunCredentials("AK", "SK", "TOK", "stac-higher", f"scans/{IMG}/{SCAN}/", None, "r")
SETTINGS = Settings.from_env(
    {"PROCESS_SCANNER_NETWORK": "stac-higher_scanner-egress", "GRYPE_DB_UPDATE_URL": "https://m/x"}
)


def test_an_admission_scan_spec_is_the_process_posture_and_nothing_more():
    spec = build_scan_spec(
        SETTINGS,
        POLICY,
        scan_id=SCAN,
        image=PENDING,
        kind="admission",
        credentials=CREDS,
        registry_auth=RegistryAuth("robot", "pat", "docker.io"),
    )
    assert spec.kind == SCANNER_RUN_KIND == "image_scan"
    assert (spec.run_id, spec.process_id) == (SCAN, IMG)
    assert spec.image == "stac-higher-image-scanner:local"
    assert spec.network == "stac-higher_scanner-egress"
    assert (spec.memory_mb, spec.timeout_seconds) == (4096, 900)
    assert spec.user_image is False and spec.registry_auth is None
    env = spec.env
    assert env["STAC_HIGHER_SCAN_KIND"] == "admission"
    assert env["STAC_HIGHER_IMAGE_REF"] == "docker.io/library/python"
    assert env["STAC_HIGHER_IMAGE_TAG"] == "3.12-slim"
    assert env["STAC_HIGHER_PLATFORM"] == "linux/amd64"
    assert env["STAC_HIGHER_MAX_IMAGE_BYTES"] == str(4096 * 1024 * 1024)
    assert env["STAC_HIGHER_ALLOWED_REGISTRIES"].split(",")[0] == "docker.io"
    assert env["STAC_HIGHER_DB_UPDATE"] == "1"
    assert env["GRYPE_DB_UPDATE_URL"] == "https://m/x"
    assert (env["REGISTRY_USERNAME"], env["REGISTRY_PASSWORD"]) == ("robot", "pat")
    assert env["STAC_HIGHER_OUTPUT_PREFIX"] == f"scans/{IMG}/{SCAN}/"
    for forbidden in (
        "DATABASE_URL",
        "CREDENTIALS_MASTER_KEY",
        "STAGING_S3_ACCESS_KEY_ID",
        "STAGING_S3_SECRET_ACCESS_KEY",
        "STAC_HIGHER_SBOM_KEY",
    ):
        assert forbidden not in env


def test_a_rescan_spec_carries_the_stored_sbom_and_identity_and_no_registry_secret():
    spec = build_scan_spec(
        SETTINGS,
        POLICY,
        scan_id=SCAN,
        image=APPROVED,
        kind="rescan",
        credentials=CREDS,
        registry_auth=RegistryAuth("robot", "pat", "docker.io"),
    )
    assert spec.env["STAC_HIGHER_SBOM_KEY"] == APPROVED.sbom_ref
    identity = json.loads(spec.env["STAC_HIGHER_IMAGE_IDENTITY"])
    assert identity["digest"] == DIGEST and identity["size_bytes"] == 10
    assert "REGISTRY_PASSWORD" not in spec.env


def test_the_sbom_read_prefix_must_be_the_images_own():
    assert sbom_read_prefix(APPROVED) == f"scans/{IMG}/{OLD}/"
    from dataclasses import replace

    with pytest.raises(ValueError, match="outside"):
        sbom_read_prefix(replace(APPROVED, sbom_ref="assets/other/sbom.syft.json"))


def test_the_sbom_read_prefix_rejects_a_flat_key_without_a_scan_id_segment():
    """Ruling: only `scans/{image_id}/{uuid}/...` grants read on one scan's
    prefix -- a flat key directly under the image's whole scan history must
    not grant read on all of it."""
    from dataclasses import replace

    flat = replace(APPROVED, sbom_ref=f"scans/{IMG}/sbom.syft.json")
    with pytest.raises(ValueError, match="outside"):
        sbom_read_prefix(flat)
    not_a_uuid = replace(APPROVED, sbom_ref=f"scans/{IMG}/not-a-scan-id/sbom.syft.json")
    with pytest.raises(ValueError, match="outside"):
        sbom_read_prefix(not_a_uuid)


class FakeSts:
    def __init__(self):
        self.kwargs = None

    def assume_role(self, **kwargs):
        self.kwargs = kwargs
        return {"Credentials": {"AccessKeyId": "A", "SecretAccessKey": "S", "SessionToken": "T"}}


class FakeStore:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.written: list[str] = []

    def put_object(self, **kwargs):
        self.written.append(kwargs["Key"])
        self.objects[kwargs["Key"]] = kwargs["Body"]

    def head_object(self, **kwargs):
        if kwargs["Key"] not in self.objects:
            raise KeyError(kwargs["Key"])
        return {"ETag": '"e"', "ContentLength": len(self.objects[kwargs["Key"]])}

    def get_object(self, **kwargs):
        import io

        data = self.objects[kwargs["Key"]]
        byte_range = kwargs.get("Range")
        if byte_range:
            start, end = byte_range.removeprefix("bytes=").split("-")
            data = data[int(start) : int(end) + 1]
        return {"Body": io.BytesIO(data)}


def test_execute_scan_mints_for_the_scan_prefix_stores_the_log_and_always_reaps():
    sts = FakeSts()
    store = FakeStore()
    executor = MemoryExecutor(results=[ExitStatus(0)], log_output=b"scanned")
    run = execute_scan(
        executor, SETTINGS, POLICY, store,
        scan_id=SCAN, image=APPROVED, kind="rescan", registry_auth=None, sts_client=sts,
    )
    assert run.status.ok and run.handle_id == f"mem-{SCAN}"
    assert run.log_ref == f"scans/{IMG}/{SCAN}/log"
    policy = json.loads(sts.kwargs["Policy"])
    assert policy["Statement"][0]["Resource"] == [f"arn:aws:s3:::stac-higher/scans/{IMG}/{SCAN}/*"]
    assert policy["Statement"][2]["Resource"] == [f"arn:aws:s3:::stac-higher/scans/{IMG}/{OLD}/*"]
    assert sts.kwargs["RoleSessionName"] == f"stac-scan-{SCAN}"
    assert executor.reaped == [f"mem-{SCAN}"]


def test_read_scan_result_missing_oversized_and_present():
    key = f"scans/{IMG}/{SCAN}/result.json"
    assert read_scan_result(FakeStore(), "b", key) is None
    with pytest.raises(ScanResultError, match="cap"):
        read_scan_result(FakeStore({key: b"x" * (RESULT_MAX_BYTES + 1)}), "b", key)
    with pytest.raises(ScanResultError, match="not JSON"):
        read_scan_result(FakeStore({key: b"{nope"}), "b", key)
    assert read_scan_result(FakeStore({key: b'{"version": 1}'}), "b", key) == {"version": 1}


class SwappingStore:
    """M1: reports a small object via HEAD, but a compromised scanner has
    swapped it for an oversized one by the time the (ranged) GET runs. The
    cap must be enforced on what the GET itself returns, never on the HEAD's
    say-so, and the GET must never read the oversized object in full."""

    def __init__(self, small: bytes, big: bytes):
        self.small = small
        self.big = big
        self.get_calls: list[dict] = []

    def head_object(self, **kwargs):
        return {"ETag": '"e"', "ContentLength": len(self.small)}

    def get_object(self, **kwargs):
        import io

        self.get_calls.append(kwargs)
        data = self.big
        byte_range = kwargs.get("Range")
        if byte_range:
            start, end = byte_range.removeprefix("bytes=").split("-")
            data = data[int(start) : int(end) + 1]
        return {"Body": io.BytesIO(data)}


def test_read_scan_result_head_says_small_but_the_body_was_swapped_oversized():
    """M1: a HEAD pre-check must never be trusted over the GET's own length."""
    key = f"scans/{IMG}/{SCAN}/result.json"
    store = SwappingStore(small=b'{"version": 1}', big=b"x" * (RESULT_MAX_BYTES + 10))
    with pytest.raises(ScanResultError, match="cap"):
        read_scan_result(store, "b", key)
    # The GET was ranged, not a full unbounded read of the swapped object.
    assert store.get_calls[0]["Range"] == f"bytes=0-{RESULT_MAX_BYTES}"
