"""Item → source object through its reference association (spec §6.1)."""

from __future__ import annotations

import json
import socket

import pytest

from _cube_sources import SOURCE_LAST_MODIFIED, local_libs, scan, write_goes_file
from pipeline.config import Settings
from pipeline.connections import egress
from pipeline.connections.envelope import load_master_key, seal
from pipeline.connections.repo import ConnectionRow
from pipeline.cubes.resolve import (
    PgSourceResolver,
    ResolvedSource,
    SourceUnavailable,
    is_transport_error,
    pick_hdf_href,
)
from pipeline.ingest.repo import IngestAssociation
from pipeline.storage import platform

KEY = load_master_key({"CREDENTIALS_MASTER_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="})
BASE = "https://noaa-goes19.s3.us-east-1.amazonaws.com/"
NC = "ABI-L2-CMIPC/2026/276/17/OR_ABI-L2-CMIPC-M6C13_G19_s1.nc"


def _assoc(
    config: dict | None = None,
    *,
    id: str = "a1",
    collection_id: str = "src",
    connection_id: str = "nodd",
    credentials: dict | None = None,
    storage_mode: str = "reference",
) -> IngestAssociation:
    conn = ConnectionRow(
        id=connection_id, name=connection_id, protocol="s3",
        config=config or {"bucket": "noaa-goes19", "region": "us-east-1", "anonymous": True},
        credentials=seal(json.dumps(credentials or {}), KEY), host_key=None,
    )
    return IngestAssociation(
        id=id, collection_id=collection_id, config={"storage_mode": storage_mode},
        connection=conn,
    )


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    def fake(host, allow_hosts=()):
        if host.endswith(".internal"):
            from pipeline.connections.egress import EgressBlocked

            raise EgressBlocked(f"egress to {host} is blocked")
        return ["203.0.113.7"]

    monkeypatch.setattr(platform, "resolve_pinned", fake)


def _resolver(
    hrefs: dict[str, str], associations: list[IngestAssociation], producer: str = "a1"
):
    """``hrefs``: filename -> source href, every file produced by ``producer``."""
    calls = {"assoc": 0}

    async def get_files(collection_id: str, item_id: str) -> dict[str, tuple[str, str]]:
        assert collection_id == "src"
        return {name: (href, producer) for name, href in hrefs.items()}

    async def get_assocs() -> list[IngestAssociation]:
        calls["assoc"] += 1
        return associations

    return PgSourceResolver(Settings.from_env(env={}), KEY, get_files, get_assocs), calls


def test_pick_hdf_href():
    assert pick_hdf_href({"a.nc": "u1", "a.json": "u2"}) == "u1"
    with pytest.raises(SourceUnavailable) as none:
        pick_hdf_href({})
    assert (none.value.status, none.value.reason) == ("skipped", "source_missing")
    for hrefs in ({"a.json": "u"}, {"a.nc": "u1", "b.h5": "u2"}):
        with pytest.raises(SourceUnavailable) as bad:
            pick_hdf_href(hrefs)
        assert (bad.value.status, bad.value.reason) == ("skipped", "unsupported_layout")


async def test_resolves_through_the_reference_association():
    resolver, calls = _resolver({"x.nc": BASE + NC}, [_assoc()])
    first = await resolver.resolve("src", "item-1")
    second = await resolver.resolve("src", "item-2")
    assert first.url == f"s3://noaa-goes19/{NC}"
    assert first.libs is second.libs  # built once per connection per job
    assert calls["assoc"] == 1  # associations listed once per job


async def test_resolves_through_the_association_that_produced_the_file():
    # Two collections ingest the same bucket through different connections;
    # the signed one is listed first and claims the href by prefix too.
    signed = _assoc(
        {"bucket": "noaa-goes19", "region": "us-east-1", "anonymous": False},
        id="a0", collection_id="other", connection_id="signed",
        credentials={"access_key_id": "AK", "secret_access_key": "SK"},
    )
    resolver, _ = _resolver({"x.nc": BASE + NC}, [signed, _assoc()], producer="a1")
    resolved = await resolver.resolve("src", "item-1")
    assert resolved.url == f"s3://noaa-goes19/{NC}"
    assert resolver._libs.keys() == {"nodd"}  # its own connection, never "signed"


async def test_a_producing_association_not_enabled_is_no_source_connection():
    for producer, assocs in (
        ("gone", [_assoc()]),  # deleted or disabled: not in the enabled list
        ("a1", [_assoc(storage_mode="copy")]),  # no longer reference mode
    ):
        resolver, _ = _resolver({"x.nc": BASE + NC}, assocs, producer=producer)
        with pytest.raises(SourceUnavailable) as exc:
            await resolver.resolve("src", "item-1")
        assert (exc.value.status, exc.value.reason) == ("skipped", "no_source_connection")


async def test_an_href_no_association_claims_is_no_source_connection():
    resolver, _ = _resolver({"x.nc": "https://elsewhere.example/x.nc"}, [_assoc()])
    with pytest.raises(SourceUnavailable) as exc:
        await resolver.resolve("src", "item-1")
    assert (exc.value.status, exc.value.reason) == ("skipped", "no_source_connection")


async def test_an_item_without_reference_hrefs_is_source_missing():
    resolver, _ = _resolver({}, [_assoc()])
    with pytest.raises(SourceUnavailable) as exc:
        await resolver.resolve("src", "item-1")
    assert (exc.value.status, exc.value.reason) == ("skipped", "source_missing")


async def test_egress_blocked_fails_the_row_with_the_message():
    blocked = _assoc({"bucket": "b", "endpoint": "http://meta.internal", "force_path_style": True,
                      "anonymous": True})
    resolver, _ = _resolver({"x.nc": "http://meta.internal/b/x.nc"}, [blocked])
    with pytest.raises(SourceUnavailable) as exc:
        await resolver.resolve("src", "item-1")
    assert exc.value.status == "failed"
    assert "meta.internal" in exc.value.reason


async def test_a_dns_failure_is_a_transport_error(monkeypatch):
    # The real resolve_pinned, with DNS down: EgressBlocked from a gaierror.
    monkeypatch.setattr(platform, "resolve_pinned", egress.resolve_pinned)
    lookups: list[str] = []

    def no_dns(host, *args, **kwargs):
        lookups.append(host)
        raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")

    monkeypatch.setattr(egress.socket, "getaddrinfo", no_dns)
    resolver, _ = _resolver({"x.nc": BASE + NC}, [_assoc()])
    for item in ("item-1", "item-2"):
        with pytest.raises(SourceUnavailable) as exc:
            await resolver.resolve("src", item)
        assert exc.value.status == "failed"
        assert "DNS resolution failed" in exc.value.reason
        assert is_transport_error(exc.value)  # the job retries; the row is not failed
    assert lookups == ["s3.us-east-1.amazonaws.com"] * 2  # nothing cached: DNS may return


async def test_a_genuine_egress_block_is_not_a_transport_error():
    blocked = _assoc({"bucket": "b", "endpoint": "http://meta.internal", "force_path_style": True,
                      "anonymous": True})
    resolver, _ = _resolver({"x.nc": "http://meta.internal/b/x.nc"}, [blocked])
    for item in ("item-1", "item-2"):
        with pytest.raises(SourceUnavailable) as exc:
            await resolver.resolve("src", item)
        assert exc.value.status == "failed"
        assert not is_transport_error(exc.value)


async def test_last_modified_is_the_object_head(tmp_path):
    write_goes_file(tmp_path / "a.nc", when=scan(0))
    resolver, _ = _resolver({}, [])
    libs = local_libs(tmp_path)
    assert await resolver.last_modified(ResolvedSource(libs, "a.nc")) == SOURCE_LAST_MODIFIED
    with pytest.raises(FileNotFoundError):
        await resolver.last_modified(ResolvedSource(libs, "missing.nc"))
