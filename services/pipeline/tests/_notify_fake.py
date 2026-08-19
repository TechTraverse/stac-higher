"""In-memory NotifyRepo for the M2-C fan-out/dispatch tests."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from pipeline.flow.repo import AlertCondition
from pipeline.notify.repo import (
    ClaimedDelivery,
    NotifiableAlert,
    NotifyRepo,
    WebhookChannel,
)


@dataclass
class FakeDelivery:
    id: str
    alert_id: str
    channel_id: str
    status: str = "pending"
    attempts: int = 0
    last_error: str | None = None
    #: age of the row's last update, seconds before "now" (drives stall/backoff)
    updated_ago: int = 0


@dataclass
class FakeNotifyRepo(NotifyRepo):
    alerts: list[NotifiableAlert] = field(default_factory=list)
    channels: list[WebhookChannel] = field(default_factory=list)
    deliveries: list[FakeDelivery] = field(default_factory=list)
    notified: set[str] = field(default_factory=set)
    raised: list[AlertCondition] = field(default_factory=list)
    resolved_channels: list[str] = field(default_factory=list)
    _next_id: int = 1

    def _delivery(self, delivery_id: str) -> FakeDelivery | None:
        return next((d for d in self.deliveries if d.id == delivery_id), None)

    async def list_unnotified_alerts(self) -> list[NotifiableAlert]:
        return [a for a in self.alerts if a.id not in self.notified]

    async def list_group_webhook_channels(self, group_id: str) -> list[WebhookChannel]:
        return [c for c in self.channels if c.group_id == group_id]

    async def create_pending_deliveries(self, alert_id: str, channel_ids: list[str]) -> int:
        created = 0
        for channel_id in channel_ids:
            if any(
                d.alert_id == alert_id and d.channel_id == channel_id
                for d in self.deliveries
            ):
                continue
            self.deliveries.append(
                FakeDelivery(
                    id=f"nd-{self._next_id}", alert_id=alert_id, channel_id=channel_id
                )
            )
            self._next_id += 1
            created += 1
        return created

    async def mark_notified(self, alert_id: str) -> None:
        self.notified.add(alert_id)

    async def revive_stalled_deliveries(
        self, stall_seconds: int, max_attempts: int
    ) -> list[tuple[str, str, str]]:
        revived: list[tuple[str, str, str]] = []
        for d in self.deliveries:
            if d.status == "delivering" and d.updated_ago >= stall_seconds:
                d.attempts += 1
                d.last_error = d.last_error or "stalled mid-delivery"
                d.status = "dead" if d.attempts >= max_attempts else "failed"
                d.updated_ago = 0
                revived.append((d.id, d.channel_id, d.status))
        return revived

    async def list_dispatchable_deliveries(
        self, max_attempts: int, retry_seconds: int
    ) -> list[str]:
        return [
            d.id
            for d in self.deliveries
            if d.status == "pending"
            or (
                d.status == "failed"
                and d.attempts < max_attempts
                and d.updated_ago >= retry_seconds
            )
        ]

    async def claim_delivery(self, delivery_id: str) -> ClaimedDelivery | None:
        d = self._delivery(delivery_id)
        if d is None or d.status not in ("pending", "failed"):
            return None
        d.status = "delivering"
        d.updated_ago = 0
        alert = next((a for a in self.alerts if a.id == d.alert_id), None)
        channel = next((c for c in self.channels if c.id == d.channel_id), None)
        if alert is None or channel is None:
            return None
        return ClaimedDelivery(
            id=d.id,
            channel_id=d.channel_id,
            channel_config=dict(channel.config),
            attempts=d.attempts,
            alert=alert,
        )

    async def record_delivery_success(self, delivery_id: str, channel_id: str) -> None:
        d = self._delivery(delivery_id)
        assert d is not None
        d.status = "delivered"
        d.last_error = None
        self.resolved_channels.append(channel_id)

    async def record_delivery_failure(
        self, delivery_id: str, error: str, max_attempts: int
    ) -> str:
        d = self._delivery(delivery_id)
        assert d is not None
        d.attempts += 1
        d.last_error = error
        d.status = "dead" if d.attempts >= max_attempts else "failed"
        d.updated_ago = 0
        return d.status

    async def raise_alert(self, condition: AlertCondition) -> None:
        self.raised.append(condition)


def alert(alert_id: str = "a1", **overrides: Any) -> NotifiableAlert:
    defaults: dict[str, Any] = {
        "id": alert_id,
        "source": "health",
        "kind": "connection_error",
        "message": "connection 'src' failing health checks: boom",
        "group_id": "earth-observation",
        "connection_id": "c1",
        "connection_name": "src",
        "first_seen": dt.datetime(2026, 8, 18, tzinfo=dt.UTC),
        "last_seen": dt.datetime(2026, 8, 18, 1, tzinfo=dt.UTC),
    }
    defaults.update(overrides)
    return NotifiableAlert(**defaults)
