"""Staging TTL cleanup: the delete primitive, endpoint pinning, the tick, and
the Phase 7 §4.1 ledger clock (live sessions are mtime-proof)."""

from __future__ import annotations

import datetime as dt

from _finalize_fake import FakeFinalizeRepo, Session
from pipeline.config import Settings
from pipeline.connections.egress import EgressBlocked
from pipeline.jobs import staging_cleanup as cleanup_mod
from pipeline.jobs.staging_cleanup import cleanup_tick
from pipeline.queue.memory import InMemoryQueue
from pipeline.storage import platform as platform_mod
from pipeline.storage.platform import build_platform_client, cleanup_expired

UTC = dt.UTC


class FakeS3:
    """Minimal boto3 S3 stand-in: paginated list + batch delete, no network."""

    def __init__(self, objects: list[tuple[str, dt.datetime]]) -> None:
        self._objects = objects
        self.deleted: list[str] = []

    def get_paginator(self, operation_name: str):
        assert operation_name == "list_objects_v2"
        return self

    def paginate(self, Bucket: str, Prefix: str):
        contents = [
            {"Key": k, "LastModified": lm}
            for k, lm in self._objects
            if k.startswith(Prefix)
        ]
        # split across two pages to exercise pagination
        yield {"Contents": contents[:1]}
        yield {"Contents": contents[1:]}

    def delete_objects(self, Bucket: str, Delete: dict):
        self.deleted.extend(o["Key"] for o in Delete["Objects"])
        return {"Deleted": Delete["Objects"]}


def test_cleanup_expired_deletes_only_old_objects():
    cutoff = dt.datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
    fake = FakeS3(
        [
            ("staging/a/old.tif", cutoff - dt.timedelta(hours=1)),
            ("staging/b/new.tif", cutoff + dt.timedelta(hours=1)),
            ("staging/c/older.tif", cutoff - dt.timedelta(days=2)),
        ]
    )

    deleted = cleanup_expired(fake, "stac-higher", "staging/", cutoff)

    assert deleted == 2
    assert set(fake.deleted) == {"staging/a/old.tif", "staging/c/older.tif"}


def test_cleanup_expired_empty_is_noop():
    fake = FakeS3([])
    assert cleanup_expired(fake, "b", "staging/", dt.datetime.now(UTC)) == 0
    assert fake.deleted == []


def test_build_platform_client_pins_http_minio_to_ip(monkeypatch):
    captured: dict = {}

    def _fake_client(service, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(platform_mod.boto3, "client", _fake_client)
    # non-allowlisted host resolves to a validated public IP → pin it
    monkeypatch.setattr(platform_mod, "resolve_pinned", lambda host, allow: ["203.0.113.10"])

    settings = Settings.from_env(env={"STAGING_S3_ENDPOINT": "http://minio:9000"})
    build_platform_client(settings)

    assert captured["endpoint_url"] == "http://203.0.113.10:9000"


def test_build_platform_client_keeps_allowlisted_hostname(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        platform_mod.boto3, "client", lambda service, **kw: captured.update(kw) or object()
    )
    # allowlisted host (compose-internal minio) → resolve_pinned returns [] → keep hostname
    monkeypatch.setattr(platform_mod, "resolve_pinned", lambda host, allow: [])

    settings = Settings.from_env(
        env={"STAGING_S3_ENDPOINT": "http://minio:9000", "EGRESS_ALLOW_HOSTS": "minio"}
    )
    build_platform_client(settings)

    assert captured["endpoint_url"] == "http://minio:9000"


async def test_cleanup_tick_uses_ttl_cutoff(monkeypatch):
    # now = 2026-07-16T12:00:00Z, ttl = 1h → cutoff = 11:00:00Z
    now_epoch = int(dt.datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC).timestamp())
    settings = Settings.from_env(env={"STAGING_TTL_SECONDS": "3600"})
    cutoff = dt.datetime(2026, 7, 16, 11, 0, 0, tzinfo=UTC)
    fake = FakeS3(
        [
            ("staging/x/expired.tif", cutoff - dt.timedelta(minutes=1)),
            ("staging/y/fresh.tif", cutoff + dt.timedelta(minutes=1)),
        ]
    )
    monkeypatch.setattr(cleanup_mod, "build_platform_client", lambda s: fake)

    deleted = await cleanup_tick(settings, now_epoch, repo=FakeFinalizeRepo())

    assert deleted == 1
    assert fake.deleted == ["staging/x/expired.tif"]


async def test_cleanup_tick_skips_live_ledger_sessions(monkeypatch):
    """§4.1 one governing clock: an old-mtime object whose ledger row is
    non-terminal and younger than the TTL is protected; a terminal row's
    leftovers keep the mtime rule."""
    now_epoch = int(dt.datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC).timestamp())
    settings = Settings.from_env(env={"STAGING_TTL_SECONDS": "3600"})
    cutoff = dt.datetime(2026, 7, 16, 11, 0, 0, tzinfo=UTC)
    old_mtime = cutoff - dt.timedelta(hours=2)
    fake = FakeS3(
        [
            ("staging/live/slow-upload.tif", old_mtime),  # live pending session
            ("staging/done/leftover.tif", old_mtime),  # finalized session debris
            ("staging/norow/orphan.tif", old_mtime),  # no ledger row at all
        ]
    )
    monkeypatch.setattr(cleanup_mod, "build_platform_client", lambda s: fake)
    repo = FakeFinalizeRepo(
        sessions={
            "live": Session(id="live", collection_id="c"),
            "done": Session(id="done", collection_id="c", status="finalized"),
        }
    )

    deleted = await cleanup_tick(settings, now_epoch, repo=repo)

    assert deleted == 2
    assert set(fake.deleted) == {"staging/done/leftover.tif", "staging/norow/orphan.tif"}


async def test_cleanup_tick_fails_safe_when_ledger_unreadable(monkeypatch):
    """No ledger, no deletions: sweeping blind could kill a live session."""

    class BrokenRepo(FakeFinalizeRepo):
        async def list_active_upload_ids(self, ttl_seconds: int) -> set[str]:
            raise RuntimeError("db down")

    now_epoch = int(dt.datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC).timestamp())
    settings = Settings.from_env(env={"STAGING_TTL_SECONDS": "3600"})
    fake = FakeS3(
        [("staging/x/ancient.tif", dt.datetime(2020, 1, 1, tzinfo=UTC))]
    )
    monkeypatch.setattr(cleanup_mod, "build_platform_client", lambda s: fake)

    deleted = await cleanup_tick(settings, now_epoch, repo=BrokenRepo())

    assert deleted == 0
    assert fake.deleted == []


async def test_register_swallows_egress_block(monkeypatch):
    """A cleanup tick whose client build is blocked logs+returns, never raises."""

    def _blocked(_settings):
        raise EgressBlocked("nope")

    monkeypatch.setattr(cleanup_mod, "build_platform_client", _blocked)
    monkeypatch.setattr(cleanup_mod, "PgFinalizeRepo", lambda dsn: FakeFinalizeRepo())

    queue = InMemoryQueue()
    cleanup_mod.register(queue, Settings.from_env(env={}))
    await queue.run_periodic(cleanup_mod.JOB_NAME, timestamp=0)
