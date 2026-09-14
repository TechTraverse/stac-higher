"""FETCH transfer policy + the hashing stream (M3-C)."""

from __future__ import annotations

import hashlib
import io

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


def test_hashing_stream_drains_an_unbounded_read_in_bounded_chunks():
    # Round 2 (Critical fix): s3transfer's non-multipart upload path
    # (bodies under `multipart_threshold`) calls `read()` with no argument
    # at all — this must drain and hash everything, not raise, and must not
    # do it via one `self._raw.read()` call.
    class _Raw:
        def __init__(self, data: bytes) -> None:
            self._data = data
            self.calls: list[int] = []

        def read(self, n: int = -1) -> bytes:
            self.calls.append(n)
            chunk, self._data = self._data[:n], self._data[n:]
            return chunk

    payload = b"a" * 2500
    raw = _Raw(payload)
    stream = HashingStream(raw, drain_chunk_bytes=1000)

    out = stream.read(-1)

    assert out == payload
    assert stream.size == 2500
    assert stream.hexdigest() == hashlib.sha256(payload).hexdigest()
    # every underlying read requests `drain_chunk_bytes` (1000), never a
    # single unbounded `self._raw.read()` call: three data-bearing reads
    # (1000 + 1000 + 500 remaining bytes) plus the final empty one that
    # ends the loop.
    assert raw.calls == [1000, 1000, 1000, 1000]

    # a further unbounded read on the exhausted stream is a clean no-op
    assert stream.read(None) == b""  # type: ignore[arg-type]


def test_hashing_stream_read_none_also_drains_and_does_not_raise():
    stream = HashingStream(io.BytesIO(b"x" * 10))
    assert stream.read(None) == b"x" * 10  # type: ignore[arg-type]
    assert stream.size == 10
