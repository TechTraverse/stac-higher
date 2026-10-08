"""libs_from_connection: explicit endpoint, path style, egress-pinned (spec §6.1)."""

from __future__ import annotations

import pytest

from pipeline.connections.egress import EgressBlocked
from pipeline.connections.repo import ConnectionRow
from pipeline.cubes.source import (
    REASON_SIGNED_SOURCE,
    SourceConnectionError,
    explicit_endpoint,
    libs_from_connection,
)
from pipeline.storage import platform

NODD = {"bucket": "noaa-goes19", "region": "us-east-1", "anonymous": True}
NONE: frozenset[str] = frozenset()


def _conn(config: dict | None = None, *, protocol: str = "s3") -> ConnectionRow:
    return ConnectionRow(
        id="c1", name="nodd", protocol=protocol, config=config or NODD,
        credentials=None, host_key=None,
    )


@pytest.fixture
def vetted(monkeypatch) -> list[str]:
    """resolve_pinned without DNS: public hosts pin to a TEST-NET address,
    ``*.internal`` is blocked, allowlisted hosts return the no-pin sentinel."""
    calls: list[str] = []

    def fake(host: str, allow_hosts=()) -> list[str]:
        calls.append(host)
        if host in set(allow_hosts):
            return []
        if host.endswith(".internal"):
            raise EgressBlocked(f"egress to {host} is blocked")
        return ["203.0.113.7"]

    monkeypatch.setattr(platform, "resolve_pinned", fake)
    return calls


def test_explicit_endpoint():
    assert explicit_endpoint(None, "us-west-2") == "https://s3.us-west-2.amazonaws.com"
    assert explicit_endpoint(None, None) == "https://s3.us-east-1.amazonaws.com"
    assert explicit_endpoint("http://minio:9000/", None) == "http://minio:9000"


def test_an_aws_source_gets_an_explicit_regional_path_style_endpoint(vetted):
    libs = libs_from_connection(_conn(), NONE)
    assert vetted == ["s3.us-east-1.amazonaws.com"]  # the host checked is the host dialled
    assert libs.prefix == "s3://noaa-goes19/"
    assert libs.registry_key == "s3://noaa-goes19"
    assert libs.url("/ABI/x.nc") == "s3://noaa-goes19/ABI/x.nc"
    assert libs.store.config["endpoint"] == "https://s3.us-east-1.amazonaws.com"
    assert libs.store.config["virtual_hosted_style_request"] == "false"
    assert libs.store.config["skip_signature"] == "true"
    container = str(libs.container_store)
    assert 'endpoint_url: "https://s3.us-east-1.amazonaws.com"' in container
    assert "force_path_style: True" in container
    assert "anonymous: True" in container


def test_region_defaults_to_us_east_1(vetted):
    libs_from_connection(_conn({"bucket": "b", "anonymous": True}), NONE)
    assert vetted == ["s3.us-east-1.amazonaws.com"]


def test_a_plaintext_endpoint_is_dialled_by_ip_but_persisted_by_name(vetted):
    libs = libs_from_connection(
        _conn({"bucket": "b", "endpoint": "http://silo.example:9000",
               "force_path_style": True, "anonymous": True}),
        NONE,
    )
    assert libs.store.config["endpoint"] == "http://203.0.113.7:9000"
    container = str(libs.container_store)
    assert 'endpoint_url: "http://silo.example:9000"' in container
    assert "allow_http: True" in container


def test_an_allowlisted_compose_endpoint_keeps_its_hostname(vetted):
    libs = libs_from_connection(
        _conn({"bucket": "b", "endpoint": "http://minio:9000", "anonymous": True}),
        frozenset({"minio"}),
    )
    assert libs.store.config["endpoint"] == "http://minio:9000"


def test_a_blocked_endpoint_raises_egress_blocked(vetted):
    with pytest.raises(EgressBlocked):
        libs_from_connection(
            _conn({"bucket": "b", "endpoint": "http://meta.internal", "anonymous": True}), NONE
        )


def test_a_signed_connection_is_refused(vetted):
    with pytest.raises(SourceConnectionError, match=REASON_SIGNED_SOURCE):
        libs_from_connection(_conn({"bucket": "b", "anonymous": False}), NONE)
    assert vetted == []  # refused before any egress check


def test_a_non_s3_connection_is_refused(vetted):
    with pytest.raises(SourceConnectionError, match="s3"):
        libs_from_connection(_conn({"host": "h"}, protocol="sftp"), NONE)
