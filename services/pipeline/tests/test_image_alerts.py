"""The process_image_flagged alert (container-images spec §10): one per
process whose current revision snapshots an image that is flagged, revoked,
gone or stale; resolved by absence."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

from pipeline.images.alerts import (
    ALERT_SOURCE,
    IMAGE_ALERT_KINDS,
    IMAGE_FLAGGED_KIND,
    ImageAlertsRepo,
    ImageInUse,
    image_alert_conditions,
    sync_image_alerts,
)

NOW = dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.UTC)
DIGEST = "sha256:" + "a" * 64
ROW = ImageInUse(
    process_id="p-1",
    process_name="geocolor",
    image_id="img-1",
    snapshot_reference="ghcr.io/org/satpy-runtime",
    snapshot_digest=DIGEST,
    status="approved",
    last_scanned_at=NOW - dt.timedelta(days=1),
    verdict={"pass": True, "reasons": []},
    diff=None,
)


def conditions(*rows, window=30):
    return image_alert_conditions(list(rows), scan_window_days=window, now=NOW)


def test_an_approved_fresh_image_raises_nothing():
    assert conditions(ROW) == []


def test_a_flagged_image_in_use_raises_one_process_anchored_alert():
    row = replace(
        ROW,
        status="flagged",
        verdict={"pass": False, "reasons": ["kev:CVE-2026-0001"]},
        diff={"new": ["CVE-2026-0001"], "new_kev": ["CVE-2026-0001"], "verdict_changed": True},
    )
    (c,) = conditions(row)
    assert (c.source, c.kind, c.process_id) == (ALERT_SOURCE, IMAGE_FLAGGED_KIND, "p-1")
    assert c.connection_id is None and c.source_id is None
    assert "ghcr.io/org/satpy-runtime@sha256:aaaaaaaaaaaa" in c.message
    assert "kev:CVE-2026-0001" in c.message
    assert "1 new KEV" in c.message and "verdict changed" in c.message
    assert "runs continue" in c.message


def test_many_reasons_are_named_up_to_five():
    reasons = [f"kev:CVE-2026-000{i}" for i in range(7)]
    (c,) = conditions(replace(ROW, status="flagged", verdict={"pass": False, "reasons": reasons}))
    assert "kev:CVE-2026-0004" in c.message
    assert "kev:CVE-2026-0005" not in c.message
    assert "and 2 more" in c.message


def test_a_stale_image_raises_even_when_approved():
    stale = replace(ROW, last_scanned_at=NOW - dt.timedelta(days=31))
    (c,) = conditions(stale)
    assert "stale" in c.message and "30-day scan window" in c.message


def test_the_stale_boundary_is_the_gates_exactly_the_window_ago_is_fresh():
    assert conditions(replace(ROW, last_scanned_at=NOW - dt.timedelta(days=30))) == []


def test_never_scanned_counts_as_stale():
    (c,) = conditions(replace(ROW, last_scanned_at=None))
    assert "never" in c.message


def test_revoked_and_gone_images_say_runs_die_at_launch():
    (revoked,) = conditions(replace(ROW, status="revoked"))
    assert "revoked" in revoked.message and "die at launch" in revoked.message
    (gone,) = conditions(replace(ROW, status=None, last_scanned_at=None, verdict=None))
    assert "no longer in the image registry" in gone.message


def test_only_live_enabled_processes_on_their_current_revision_are_considered():
    from pipeline.images.alerts import IMAGES_IN_USE_SQL

    assert "p.deleted_at IS NULL AND p.enabled" in IMAGES_IN_USE_SQL
    assert "r.id = p.current_revision" in IMAGES_IN_USE_SQL


def test_one_condition_per_process():
    a = replace(ROW, status="flagged")
    assert len(conditions(a, replace(a, image_id="img-2"))) == 1


class Repo(ImageAlertsRepo):
    def __init__(self, rows):
        self.rows = rows

    async def list_images_in_use(self):
        return list(self.rows)


class Sink:
    def __init__(self):
        self.calls = []

    async def __call__(self, found, owned):
        self.calls.append((found, owned))
        return (len(found), 0)


async def test_sync_is_scoped_to_the_image_kind_and_always_runs():
    """An empty condition list must still reach sync_alerts: that is what
    auto-resolves the alert once the image is approved again or the process
    moved off it."""
    sink = Sink()
    assert await sync_image_alerts(Repo([ROW]), sink, scan_window_days=30, now=NOW) == (0, 0)
    assert sink.calls == [([], IMAGE_ALERT_KINDS)]

    flagged = replace(ROW, status="flagged")
    raised, _ = await sync_image_alerts(Repo([flagged]), sink, scan_window_days=30, now=NOW)
    assert raised == 1 and sink.calls[1][1] == ("process_image_flagged",)
