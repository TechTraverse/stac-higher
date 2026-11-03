"""R-2: platform-bucket clients fall back to the AWS default credential chain
when the staging S3 keys are unset, and pass them through when set."""

from __future__ import annotations

from typing import Any

import icechunk as ic
import pytest

from pipeline.config import Settings
from pipeline.cubes.icerepo import cube_storage
from pipeline.process.credentials import build_sts_client
from pipeline.storage.platform import (
    build_platform_client,
    default_chain_session,
    platform_s3_access,
    raster_location,
)

_BASE = {"STAGING_S3_ENDPOINT": "http://minio:9000", "EGRESS_ALLOW_HOSTS": "minio"}
KEYLESS = Settings.from_env(env=dict(_BASE))
KEYED = Settings.from_env(
    env={**_BASE, "STAGING_S3_ACCESS_KEY_ID": "AK", "STAGING_S3_SECRET_ACCESS_KEY": "SK"}
)


@pytest.fixture(autouse=True)
def _fresh_chain_session():
    default_chain_session.cache_clear()
    yield
    default_chain_session.cache_clear()


def _capture(monkeypatch: pytest.MonkeyPatch, target: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake(*args: Any, **kwargs: Any) -> object:
        calls.append({"args": args, **kwargs})
        return object()

    monkeypatch.setattr(target, fake)
    return calls


def test_platform_client_without_keys_passes_no_credentials(monkeypatch):
    calls = _capture(monkeypatch, "pipeline.storage.platform.boto3.client")
    build_platform_client(KEYLESS)
    kwargs = calls[0]
    assert "aws_access_key_id" not in kwargs and "aws_secret_access_key" not in kwargs
    assert kwargs["region_name"] == "us-east-1" and kwargs["endpoint_url"].endswith(":9000")


def test_platform_client_with_keys_passes_them(monkeypatch):
    calls = _capture(monkeypatch, "pipeline.storage.platform.boto3.client")
    build_platform_client(KEYED)
    assert (calls[0]["aws_access_key_id"], calls[0]["aws_secret_access_key"]) == ("AK", "SK")


def test_sts_client_without_keys_uses_the_default_chain(monkeypatch):
    calls = _capture(monkeypatch, "pipeline.process.credentials.boto3.client")
    build_sts_client(KEYLESS)
    assert calls[0]["args"] == ("sts",)
    assert "aws_access_key_id" not in calls[0] and "aws_secret_access_key" not in calls[0]


def test_raster_location_without_keys_signs_with_the_default_chain(monkeypatch, tmp_path):
    monkeypatch.delenv("AWS_PROFILE", raising=False)  # a developer's ambient profile
    for name, value in {
        "AWS_ACCESS_KEY_ID": "CHAIN-AK",
        "AWS_SECRET_ACCESS_KEY": "CHAIN-SK",
        "AWS_SESSION_TOKEN": "CHAIN-T",
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_SHARED_CREDENTIALS_FILE": str(tmp_path / "none"),
        "AWS_CONFIG_FILE": str(tmp_path / "none"),
    }.items():
        monkeypatch.setenv(name, value)
    access = platform_s3_access(KEYLESS)
    first = raster_location(access, "stac-higher", "assets/c/i/a.tif", gdal_cachemax_mb=64)
    second = raster_location(access, "stac-higher", "assets/c/i/b.tif", gdal_cachemax_mb=64)
    opts = first.session.get_credential_options()
    assert (opts["AWS_ACCESS_KEY_ID"], opts["AWS_SESSION_TOKEN"]) == ("CHAIN-AK", "CHAIN-T")
    assert first.session._session is second.session._session  # one chain resolution per process


def test_cube_storage_without_keys_selects_icechunk_from_env(monkeypatch):
    calls = _capture(monkeypatch, "pipeline.cubes.icerepo.ic.s3_storage")
    cube_storage(KEYLESS, "goes19-c13-cube")
    kw = calls[0]
    assert kw["access_key_id"] is None and kw["secret_access_key"] is None
    assert isinstance(
        ic.s3_credentials(
            access_key_id=kw["access_key_id"], secret_access_key=kw["secret_access_key"]
        ),
        ic.S3Credentials.FromEnv,
    )
