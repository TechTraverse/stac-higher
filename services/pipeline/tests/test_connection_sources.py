"""association_for_href: the ADR 0018 rule shared by staging and the cube writer."""

from __future__ import annotations

from pipeline.config import Settings
from pipeline.connections.envelope import load_master_key, seal
from pipeline.connections.repo import ConnectionRow
from pipeline.connections.sources import association_for_href
from pipeline.ingest.repo import IngestAssociation

KEY = load_master_key({"CREDENTIALS_MASTER_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="})
NODD = {"bucket": "noaa-goes19", "region": "us-east-1", "anonymous": True}
HREF = "https://noaa-goes19.s3.us-east-1.amazonaws.com/ABI-L2-CMIPC/2026/276/17/OR%20x.nc"
NONE = frozenset()


def _assoc(
    assoc_id: str,
    *,
    config: dict | None = None,
    mode: str = "reference",
    protocol: str = "s3",
    sealed: bool = True,
) -> IngestAssociation:
    conn = ConnectionRow(
        id=f"c-{assoc_id}",
        name=assoc_id,
        protocol=protocol,
        config=config or NODD,
        # An anonymous connection stores an EMPTY envelope (G-1), never NULL.
        credentials=seal("{}", KEY) if sealed else None,
        host_key=None,
    )
    return IngestAssociation(
        id=assoc_id, collection_id="src", config={"storage_mode": mode}, connection=conn
    )


def test_matches_the_reference_association_and_decodes_the_key():
    match = association_for_href(HREF, [_assoc("a")], KEY, NONE)
    assert match is not None
    assert match.association.id == "a"
    assert match.key == "ABI-L2-CMIPC/2026/276/17/OR x.nc"


def test_copy_mode_associations_never_match():
    assert association_for_href(HREF, [_assoc("a", mode="copy")], KEY, NONE) is None


def test_an_unbuildable_association_is_passed_over():
    found = association_for_href(HREF, [_assoc("broken", sealed=False), _assoc("ok")], KEY, NONE)
    assert found is not None and found.association.id == "ok"


def test_another_bucket_does_not_match():
    other = _assoc("a", config={"bucket": "noaa-goes18", "region": "us-east-1", "anonymous": True})
    assert association_for_href(HREF, [other], KEY, NONE) is None


def test_a_path_style_custom_endpoint_matches():
    silo = _assoc(
        "a",
        config={
            "bucket": "src",
            "endpoint": "http://minio:9000",
            "force_path_style": True,
            "anonymous": True,
        },
    )
    match = association_for_href("http://minio:9000/src/goes/a.nc", [silo], KEY, NONE)
    assert match is not None and match.key == "goes/a.nc"


async def test_remote_fetcher_reads_through_the_owning_association(monkeypatch):
    from pipeline.connections.adapters.s3 import S3Adapter
    from pipeline.process import staging

    assoc = _assoc("a")

    class _Repo:
        def __init__(self, database_url: str) -> None:
            pass

        async def list_enabled_ingest_associations(self):
            return [assoc]

    got: list[str] = []

    async def fake_get(self, path: str) -> bytes:
        got.append(path)
        return b"bytes"

    monkeypatch.setattr(staging, "PgIngestRepo", _Repo)
    monkeypatch.setattr(S3Adapter, "get", fake_get)
    fetch = staging.build_remote_fetcher(Settings.from_env(env={}), KEY)
    assert await fetch(HREF) == b"bytes"
    assert got == ["ABI-L2-CMIPC/2026/276/17/OR x.nc"]
