"""Staging a run's remote inputs (GOES spec §3.2)."""

from __future__ import annotations

import json

import pytest

from pipeline.connections import http_fetch
from pipeline.connections.egress import EgressBlocked
from pipeline.process.inputs import InputPlan, RemoteFetch
from pipeline.process.staging import InputStagingError, stage_inputs

RUN = "11111111-1111-4111-8111-111111111111"


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.order: list[str] = []

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body
        self.order.append(Key)


def _plan(fetches):
    return InputPlan(
        manifest={"version": 1, "items": []},
        manifest_key=f"staging/runs/{RUN}/inputs/b/manifest.json",
        fetches=tuple(fetches),
        read_prefixes=(),
    )


async def test_stages_remote_bytes_then_writes_the_manifest_last():
    s3 = FakeS3()
    fetched: list[str] = []

    async def fetch(href: str) -> bytes:
        fetched.append(href)
        return b"nc-bytes"

    plan = _plan(
        [RemoteFetch("https://h/x.nc", f"staging/runs/{RUN}/inputs/b/i/x.nc", "i", "data")]
    )
    await stage_inputs(plan, storage_client=s3, bucket="stac-higher", fetch_remote=fetch)

    assert fetched == ["https://h/x.nc"]
    assert s3.objects[f"staging/runs/{RUN}/inputs/b/i/x.nc"] == b"nc-bytes"
    assert json.loads(s3.objects[plan.manifest_key]) == plan.manifest
    # The manifest is the "batch complete" marker — it must land LAST.
    assert s3.order[-1] == plan.manifest_key


async def test_a_failed_fetch_is_an_input_staging_error_naming_the_asset():
    s3 = FakeS3()

    async def fetch(href: str) -> bytes:
        raise OSError("boom")

    plan = _plan(
        [RemoteFetch("https://h/x.nc", f"staging/runs/{RUN}/inputs/b/i/x.nc", "i", "data")]
    )
    with pytest.raises(InputStagingError, match=r"item 'i' asset 'data'"):
        await stage_inputs(plan, storage_client=s3, bucket="b", fetch_remote=fetch)
    assert plan.manifest_key not in s3.objects


async def test_no_fetches_still_writes_the_manifest():
    s3 = FakeS3()

    async def fetch(href: str) -> bytes:  # pragma: no cover
        raise AssertionError("not called")

    plan = _plan([])
    await stage_inputs(plan, storage_client=s3, bucket="b", fetch_remote=fetch)
    assert plan.manifest_key in s3.objects


# ---------------------------------------------------------------------------
# the public-GET fallback
# ---------------------------------------------------------------------------


def test_fetch_public_url_refuses_plain_http_and_blocked_hosts(monkeypatch):
    with pytest.raises(http_fetch.PublicFetchError, match="https"):
        http_fetch.fetch_public_url("http://example.com/x")

    def blocked(host, allow_hosts=()):
        raise EgressBlocked("nope")

    monkeypatch.setattr(http_fetch, "resolve_pinned", blocked)
    with pytest.raises(EgressBlocked):
        http_fetch.fetch_public_url("https://169.254.169.254/latest")
