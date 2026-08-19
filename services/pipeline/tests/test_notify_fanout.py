"""M2-C notify tick + webhook delivery handler (spec §4, ADR 0010)."""

from __future__ import annotations

import json

from _notify_fake import FakeDelivery, FakeNotifyRepo, alert
from pipeline.notify.config import WebhookConfig
from pipeline.notify.fanout import (
    build_payload,
    handle_webhook_delivery,
    notify_tick,
)
from pipeline.notify.repo import WEBHOOK_FAILED_KIND, WebhookChannel
from pipeline.notify.webhook import WebhookDeliveryError

EO = "earth-observation"


def channel(channel_id: str = "ch1", group_id: str = EO, **config) -> WebhookChannel:
    return WebhookChannel(
        id=channel_id,
        group_id=group_id,
        config=config or {"url": "https://hooks.example.com/stac"},
    )


def collect_enqueue(into: list[str]):
    async def enqueue(delivery_id: str) -> None:
        into.append(delivery_id)

    return enqueue


async def tick(repo, enqueued=None, *, max_attempts=5, retry_seconds=60, stall_seconds=900):
    return await notify_tick(
        repo,
        collect_enqueue(enqueued if enqueued is not None else []),
        max_attempts=max_attempts,
        retry_seconds=retry_seconds,
        stall_seconds=stall_seconds,
    )


class TestFanOut:
    async def test_fans_a_firing_alert_out_to_group_webhooks_and_enqueues(self):
        repo = FakeNotifyRepo(
            alerts=[alert()],
            channels=[channel("ch1"), channel("ch2"), channel("other", group_id="g2")],
        )
        enqueued: list[str] = []
        result = await tick(repo, enqueued)
        assert result.fanned_out == 1
        assert result.deliveries_created == 2  # own-group channels only
        assert "a1" in repo.notified
        assert sorted(d.channel_id for d in repo.deliveries) == ["ch1", "ch2"]
        assert sorted(enqueued) == sorted(d.id for d in repo.deliveries)

    async def test_no_channels_still_marks_notified(self):
        repo = FakeNotifyRepo(alerts=[alert()])
        result = await tick(repo)
        assert result.fanned_out == 1
        assert result.deliveries_created == 0
        assert "a1" in repo.notified

    async def test_groupless_alert_is_marked_notified_without_fanout(self):
        repo = FakeNotifyRepo(alerts=[alert(group_id=None)], channels=[channel()])
        result = await tick(repo)
        assert result.deliveries_created == 0
        assert "a1" in repo.notified

    async def test_channel_anchored_alert_skips_its_own_channel(self):
        # A webhook_failed alert about ch1 must not be dispatched through ch1.
        repo = FakeNotifyRepo(
            alerts=[
                alert(
                    "a2",
                    source="job_failure",
                    kind=WEBHOOK_FAILED_KIND,
                    connection_id=None,
                    connection_name=None,
                    channel_id="ch1",
                )
            ],
            channels=[channel("ch1"), channel("ch2")],
        )
        await tick(repo)
        assert [d.channel_id for d in repo.deliveries] == ["ch2"]

    async def test_rerun_is_idempotent(self):
        repo = FakeNotifyRepo(alerts=[alert()], channels=[channel()])
        await tick(repo)
        # Simulate the crash-between case: alert unmarked, deliveries exist.
        repo.notified.clear()
        result = await tick(repo)
        assert result.deliveries_created == 0  # UNIQUE(alert, channel) held
        assert len(repo.deliveries) == 1


class TestRetrySweep:
    async def test_failed_row_requeues_after_cooloff_only(self):
        repo = FakeNotifyRepo(
            deliveries=[
                FakeDelivery("d1", "a1", "ch1", status="failed", attempts=1, updated_ago=30),
                FakeDelivery("d2", "a1", "ch2", status="failed", attempts=1, updated_ago=120),
            ]
        )
        enqueued: list[str] = []
        await tick(repo, enqueued, retry_seconds=60)
        assert enqueued == ["d2"]

    async def test_stalled_claim_revives_into_retry_path(self):
        repo = FakeNotifyRepo(
            deliveries=[
                FakeDelivery("d1", "a1", "ch1", status="delivering", updated_ago=1000)
            ]
        )
        result = await tick(repo, stall_seconds=900)
        assert result.revived == 1
        assert repo.deliveries[0].status == "failed"
        assert repo.raised == []

    async def test_stall_at_attempt_cap_goes_dead_and_raises(self):
        repo = FakeNotifyRepo(
            deliveries=[
                FakeDelivery(
                    "d1", "a1", "ch1", status="delivering", attempts=4, updated_ago=1000
                )
            ]
        )
        await tick(repo, max_attempts=5, stall_seconds=900)
        assert repo.deliveries[0].status == "dead"
        assert len(repo.raised) == 1
        assert repo.raised[0].kind == WEBHOOK_FAILED_KIND
        assert repo.raised[0].channel_id == "ch1"


class TestWebhookHandler:
    async def test_success_records_delivered_and_resolves_channel_alerts(self):
        repo = FakeNotifyRepo(
            alerts=[alert()],
            channels=[channel("ch1", secret="s3cret", url="https://h.example/x")],
            deliveries=[FakeDelivery("d1", "a1", "ch1")],
        )
        sent: list[tuple[WebhookConfig, bytes]] = []

        def fake_send(config, body, *, allow_hosts=(), timeout=10.0):
            sent.append((config, body))

        status = await handle_webhook_delivery(
            repo, "d1", max_attempts=5, send=fake_send
        )
        assert status == "delivered"
        assert repo.deliveries[0].status == "delivered"
        assert repo.resolved_channels == ["ch1"]
        config, body = sent[0]
        assert config.url == "https://h.example/x"
        assert config.secret == "s3cret"
        payload = json.loads(body)
        assert payload["event"] == "alert.firing"
        assert payload["alert"]["id"] == "a1"
        assert payload["alert"]["kind"] == "connection_error"

    async def test_failure_records_failed_below_cap(self):
        repo = FakeNotifyRepo(
            alerts=[alert()],
            channels=[channel("ch1")],
            deliveries=[FakeDelivery("d1", "a1", "ch1")],
        )

        def failing_send(config, body, *, allow_hosts=(), timeout=10.0):
            raise WebhookDeliveryError("webhook target answered 500")

        status = await handle_webhook_delivery(
            repo, "d1", max_attempts=5, send=failing_send
        )
        assert status == "failed"
        assert repo.deliveries[0].attempts == 1
        assert "500" in (repo.deliveries[0].last_error or "")
        assert repo.raised == []

    async def test_terminal_failure_goes_dead_and_raises_webhook_failed(self):
        repo = FakeNotifyRepo(
            alerts=[alert()],
            channels=[channel("ch1")],
            deliveries=[FakeDelivery("d1", "a1", "ch1", status="failed", attempts=4)],
        )

        def failing_send(config, body, *, allow_hosts=(), timeout=10.0):
            raise WebhookDeliveryError("connection refused")

        status = await handle_webhook_delivery(
            repo, "d1", max_attempts=5, send=failing_send
        )
        assert status == "dead"
        assert repo.raised[0].kind == WEBHOOK_FAILED_KIND
        assert repo.raised[0].channel_id == "ch1"
        assert "connection refused" in repo.raised[0].message

    async def test_unusable_config_is_a_recorded_failure_not_a_crash(self):
        repo = FakeNotifyRepo(
            alerts=[alert()],
            channels=[WebhookChannel(id="ch1", group_id=EO, config={})],
            deliveries=[FakeDelivery("d1", "a1", "ch1")],
        )
        status = await handle_webhook_delivery(repo, "d1", max_attempts=5)
        assert status == "failed"
        assert "url" in (repo.deliveries[0].last_error or "")

    async def test_already_finished_row_is_skipped(self):
        repo = FakeNotifyRepo(
            deliveries=[FakeDelivery("d1", "a1", "ch1", status="delivered")]
        )
        status = await handle_webhook_delivery(repo, "d1", max_attempts=5)
        assert status == "skipped"


def test_build_payload_is_stable_json():
    body = build_payload(alert())
    payload = json.loads(body)
    assert payload["alert"]["group_id"] == EO
    assert payload["alert"]["first_seen"] == "2026-08-18T00:00:00+00:00"
    assert payload["alert"]["collection_id"] is None
