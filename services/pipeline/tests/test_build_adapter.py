"""build_adapter: decrypt credentials + construct the protocol adapter."""

from __future__ import annotations

import json

import pytest

from pipeline.connections.adapters import S3Adapter
from pipeline.connections.build import AdapterBuildError, build_adapter
from pipeline.connections.envelope import load_master_key, seal
from pipeline.connections.repo import ConnectionRow

KEY = load_master_key({"CREDENTIALS_MASTER_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="})
ALLOW: frozenset[str] = frozenset()


def _conn(
    *,
    protocol="s3",
    config=None,
    credentials=b"__seal__",
    creds=None,
) -> ConnectionRow:
    if credentials == b"__seal__":
        creds = creds or {"access_key_id": "x", "secret_access_key": "y"}
        credentials = seal(json.dumps(creds), KEY)
    return ConnectionRow(
        id="c1",
        name="n",
        protocol=protocol,
        config=config or {"bucket": "b"},
        credentials=credentials,
        host_key=None,
    )


def test_build_adapter_constructs_adapter():
    adapter = build_adapter(_conn(), KEY, ALLOW)
    assert isinstance(adapter, S3Adapter)


def test_build_adapter_missing_credentials():
    with pytest.raises(AdapterBuildError, match="no stored credentials"):
        build_adapter(_conn(credentials=None), KEY, ALLOW)


def test_build_adapter_bad_envelope():
    with pytest.raises(AdapterBuildError, match="decryption failed"):
        build_adapter(_conn(credentials=b"\x01not-a-valid-envelope-at-all"), KEY, ALLOW)


def test_build_adapter_non_json_payload():
    envelope = seal("not json", KEY)
    with pytest.raises(AdapterBuildError, match="not valid JSON"):
        build_adapter(_conn(credentials=envelope), KEY, ALLOW)


def test_build_adapter_reserved_protocol():
    # stac-api is reserved; the factory raises NotImplementedError → build error.
    with pytest.raises(AdapterBuildError):
        build_adapter(_conn(protocol="stac-api", config={}), KEY, ALLOW)
