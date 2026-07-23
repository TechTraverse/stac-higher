import hashlib

import pytest

from pipeline.delivery.transfer import (
    can_server_side_copy,
    etag_fingerprint,
    is_multipart_etag,
    sha256_fingerprint,
)


def test_sha256_fingerprint_format():
    data = b"bytes"
    assert sha256_fingerprint(data) == f"sha256:{hashlib.sha256(data).hexdigest()}"


def test_etag_fingerprint_format():
    assert etag_fingerprint("abc123", 42) == "etag:abc123/42"


def test_is_multipart_etag():
    assert is_multipart_etag("abc123-4")
    assert not is_multipart_etag("d41d8cd98f00b204e9800998ecf8427e")


@pytest.mark.parametrize(
    ("protocol", "connection_endpoint", "platform_endpoint", "expected"),
    [
        # not s3 -> never copy
        ("sftp", "http://minio:9000", "http://minio:9000", False),
        # same custom endpoint -> copy
        ("s3", "http://minio:9000", "http://minio:9000", True),
        # different host -> stream
        ("s3", "http://other:9000", "http://minio:9000", False),
        # default-port equivalence (http = 80)
        ("s3", "http://minio:80", "http://minio", True),
        ("s3", "http://minio:9000", "http://minio", False),
        # no custom endpoint on either side: both real AWS, CopyObject spans buckets
        ("s3", None, None, True),
        # only one side custom -> stream
        ("s3", None, "http://minio:9000", False),
        ("s3", "http://minio:9000", None, False),
        # host comparison is case-insensitive
        ("s3", "http://MinIO:9000", "http://minio:9000", True),
        # malformed endpoint (non-numeric port) degrades to streaming, never raises
        ("s3", "http://minio:abc", "http://minio:9000", False),
        ("s3", "http://minio:9000", "http://minio:abc", False),
    ],
)
def test_copy_gate(protocol, connection_endpoint, platform_endpoint, expected):
    assert can_server_side_copy(protocol, connection_endpoint, platform_endpoint) is expected
