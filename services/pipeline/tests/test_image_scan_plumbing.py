"""C-2 plumbing: the scan key layout, prefix-scoped STS credentials and the
generic log writer (container-images spec §6.2, §8.1)."""

from __future__ import annotations

import json

import pytest

from pipeline.config import Settings
from pipeline.process.credentials import (
    RunCredentialsError,
    mint_prefix_credentials,
    mint_run_credentials,
)
from pipeline.process.logs import TRUNCATION_MARKER, store_log
from pipeline.storage.keys import (
    InvalidKeySegment,
    image_scan_log_key,
    image_scan_prefix,
)

IMAGE = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f"
SCAN = "0d9e8f7a-6b5c-4d3e-9f2a-1b0c9d8e7f6a"


class FakeSts:
    def __init__(self):
        self.kwargs = None

    def assume_role(self, **kwargs):
        self.kwargs = kwargs
        return {"Credentials": {"AccessKeyId": "A", "SecretAccessKey": "S", "SessionToken": "T"}}


class FakeStore:
    def __init__(self, fail=False):
        self.fail = fail
        self.written: list[tuple[str, bytes]] = []

    def put_object(self, **kwargs):
        if self.fail:
            raise RuntimeError("bucket gone")
        self.written.append((kwargs["Key"], kwargs["Body"]))


def test_the_scan_prefix_and_log_key():
    assert image_scan_prefix(IMAGE, SCAN) == f"scans/{IMAGE}/{SCAN}/"
    assert image_scan_log_key(IMAGE, SCAN) == f"scans/{IMAGE}/{SCAN}/log"


@pytest.mark.parametrize("bad", ["", "..", "a/b", ".hidden"])
def test_scan_keys_refuse_traversal(bad):
    with pytest.raises(InvalidKeySegment):
        image_scan_prefix(bad, SCAN)
    with pytest.raises(InvalidKeySegment):
        image_scan_prefix(IMAGE, bad)


def test_prefix_credentials_bound_the_session_to_the_scan_prefix():
    sts = FakeSts()
    creds = mint_prefix_credentials(
        Settings.from_env({}),
        session_name=f"stac-scan-{SCAN}",
        prefix=image_scan_prefix(IMAGE, SCAN),
        timeout_seconds=900,
        sts_client=sts,
        read_prefixes=[f"scans/{IMAGE}/older-scan/"],
    )
    assert creds.prefix == f"scans/{IMAGE}/{SCAN}/"
    assert sts.kwargs["RoleSessionName"] == f"stac-scan-{SCAN}"[:64]
    policy = json.loads(sts.kwargs["Policy"])
    write = policy["Statement"][0]["Resource"]
    assert write == [f"arn:aws:s3:::stac-higher/scans/{IMAGE}/{SCAN}/*"]
    read = policy["Statement"][2]["Resource"]
    assert read == [f"arn:aws:s3:::stac-higher/scans/{IMAGE}/older-scan/*"]
    assert creds.as_env()["STAC_HIGHER_OUTPUT_PREFIX"] == f"scans/{IMAGE}/{SCAN}/"


@pytest.mark.parametrize("bad", ["", "scans/x", "scans/*/"])
def test_a_malformed_prefix_refuses_to_mint(bad):
    sts = FakeSts()
    with pytest.raises(RunCredentialsError):
        mint_prefix_credentials(
            Settings.from_env({}),
            session_name="stac-scan-x",
            prefix=bad,
            timeout_seconds=900,
            sts_client=sts,
        )
    assert sts.kwargs is None


@pytest.mark.parametrize("bad", ["", "scans/x", "scans/*/"])
def test_a_malformed_read_prefix_refuses_to_mint(bad):
    sts = FakeSts()
    with pytest.raises(RunCredentialsError):
        mint_prefix_credentials(
            Settings.from_env({}),
            session_name="stac-scan-x",
            prefix=image_scan_prefix(IMAGE, SCAN),
            timeout_seconds=900,
            sts_client=sts,
            read_prefixes=[bad],
        )
    assert sts.kwargs is None


def test_valid_prefixes_still_mint_run_scan_and_rescan_credentials():
    """The three real shapes callers pass (spec §5/§6.2): a run's own
    staging prefix, a scan's own prefix, and a rescan's stored-SBOM read
    prefix -- none of them must be refused by the new validation."""
    sts = FakeSts()
    mint_run_credentials(
        Settings.from_env({}), "run-1", 60, sts_client=sts, read_prefixes=["assets/goes/"]
    )
    mint_prefix_credentials(
        Settings.from_env({}),
        session_name=f"stac-scan-{SCAN}",
        prefix=image_scan_prefix(IMAGE, SCAN),
        timeout_seconds=900,
        sts_client=sts,
        read_prefixes=[f"scans/{IMAGE}/older-scan/"],
    )


def test_run_credentials_are_unchanged_by_the_refactor():
    sts = FakeSts()
    creds = mint_run_credentials(Settings.from_env({}), "run-1", 60, sts_client=sts)
    assert creds.prefix == "staging/runs/run-1/"
    assert sts.kwargs["RoleSessionName"] == "stac-run-run-1"
    assert sts.kwargs["DurationSeconds"] == 900


def test_store_log_caps_and_returns_the_key():
    store = FakeStore()
    key = image_scan_log_key(IMAGE, SCAN)
    assert store_log(store, "b", key, b"x" * 500, 200, context={"scan_id": SCAN}) == key
    written = store.written[0][1]
    assert len(written) == 200 and written.endswith(TRUNCATION_MARKER)


def test_store_log_failure_returns_none():
    assert store_log(FakeStore(fail=True), "b", "k", b"x", 10, context={}) is None
