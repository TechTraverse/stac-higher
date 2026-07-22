import hashlib

from pipeline.delivery.transfer import (
    can_server_side_copy,
    etag_fingerprint,
    sha256_fingerprint,
)


def test_sha256_fingerprint_format():
    data = b"bytes"
    assert sha256_fingerprint(data) == f"sha256:{hashlib.sha256(data).hexdigest()}"


def test_etag_fingerprint_format():
    assert etag_fingerprint("abc123", 42) == "etag:abc123/42"


def test_copy_gate_requires_s3():
    assert not can_server_side_copy("sftp", "http://minio:9000", "http://minio:9000")


def test_copy_gate_same_custom_endpoint():
    assert can_server_side_copy("s3", "http://minio:9000", "http://minio:9000")


def test_copy_gate_different_host():
    assert not can_server_side_copy("s3", "http://other:9000", "http://minio:9000")


def test_copy_gate_default_port_equivalence():
    assert can_server_side_copy("s3", "http://minio:80", "http://minio")
    assert not can_server_side_copy("s3", "http://minio:9000", "http://minio")


def test_copy_gate_both_aws():
    # No custom endpoint on either side: both are real AWS S3, where
    # CopyObject spans buckets.
    assert can_server_side_copy("s3", None, None)


def test_copy_gate_mixed_none():
    assert not can_server_side_copy("s3", None, "http://minio:9000")
    assert not can_server_side_copy("s3", "http://minio:9000", None)


def test_copy_gate_host_case_insensitive():
    assert can_server_side_copy("s3", "http://MinIO:9000", "http://minio:9000")
