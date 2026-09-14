"""FETCH transfer policy + the hashing stream (M3-C)."""

from __future__ import annotations

import hashlib
import io

import pytest

from pipeline.config import Settings
from pipeline.ingest.transfer import HashingStream, TransferPolicy, transfer_policy


class _S3Like:
    protocol = "s3"

    def __init__(self, endpoint):
        self._endpoint = endpoint

    @property
    def endpoint(self):
        return self._endpoint


class _Sftp:
    protocol = "sftp"
    endpoint = None


def _settings(endpoint: str | None) -> Settings:
    env = {"FETCH_CHUNK_BYTES": "1024", "FETCH_TRANSFER_CONCURRENCY": "2"}
    if endpoint is not None:
        env["STAGING_S3_ENDPOINT"] = endpoint
    else:
        env["STAGING_S3_ENDPOINT"] = ""
    return Settings.from_env(env=env)


def test_same_endpoint_s3_source_may_be_copied_server_side():
    policy = transfer_policy(_S3Like("http://minio:9000"), _settings("http://minio:9000"))
    assert policy == TransferPolicy(server_side_copy=True, chunk_bytes=1024, concurrency=2)


def test_a_different_endpoint_streams():
    policy = transfer_policy(_S3Like("http://other:9000"), _settings("http://minio:9000"))
    assert policy.server_side_copy is False


def test_both_on_real_aws_may_copy():
    assert transfer_policy(_S3Like(None), _settings(None)).server_side_copy is True


def test_sftp_never_copies():
    assert transfer_policy(_Sftp(), _settings("http://minio:9000")).server_side_copy is False


def test_hashing_stream_hashes_exactly_what_was_read():
    payload = b"x" * 5000
    stream = HashingStream(io.BytesIO(payload))
    out = b""
    while chunk := stream.read(1024):
        out += chunk
    assert out == payload
    assert stream.size == 5000
    assert stream.hexdigest() == hashlib.sha256(payload).hexdigest()


def test_hashing_stream_rejects_an_unbounded_read():
    stream = HashingStream(io.BytesIO(b"x" * 10))
    with pytest.raises(ValueError):
        stream.read(-1)
    with pytest.raises(ValueError):
        stream.read(None)  # type: ignore[arg-type]
