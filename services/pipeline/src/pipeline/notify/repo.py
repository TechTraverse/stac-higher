"""Repository seam for notification fan-out + webhook dispatch (M2-C).

Mirrors the flow-monitor repo: a :class:`NotifyRepo` ABC the tick/job logic
depends on (unit-tested against ``FakeNotifyRepo``) plus a psycopg
``PgNotifyRepo`` whose methods are ``# pragma: no cover`` — exercised by the
M2-I rehearsal.

Ownership (ADR 0001): reads ``alerts`` / ``notification_channels`` (plus the
scoping joins); writes ``alerts.notified_at``, the ``notification_deliveries``
ledger, and — via the same upsert the monitor uses — channel-anchored
``webhook_failed`` alerts. The app owns all DDL (migration 015) and the
channel CRUD.
"""

from __future__ import annotations

import abc
import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from pipeline.flow.repo import AlertCondition

#: The dedup `kind` this module owns; deliberately NOT in the flow monitor's
#: MONITOR_KINDS so its auto-resolve never clobbers these rows. Resolution
#: happens here, on the next successful delivery to the channel.
WEBHOOK_FAILED_KIND = "webhook_failed"


@dataclass(frozen=True)
class NotifiableAlert:
    """A firing alert that has not been fanned out yet."""

    id: str
    source: str
    kind: str
    message: str
    #: Derived: alert → connection (direct or via association) → group, the
    #: channel's group for channel-anchored alerts, or the collection's
    #: settings group for collection-anchored alerts (P7-H). None when the
    #: anchor row is gone or the collection is unowned — nothing to notify.
    group_id: str | None
    connection_id: str | None = None
    association_id: str | None = None
    #: Set on channel-anchored alerts; fan-out excludes this channel so a
    #: broken webhook is never asked to announce its own failure.
    channel_id: str | None = None
    connection_name: str | None = None
    collection_id: str | None = None
    first_seen: dt.datetime | None = None
    last_seen: dt.datetime | None = None


@dataclass(frozen=True)
class WebhookChannel:
    id: str
    group_id: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ClaimedDelivery:
    """One claimed ledger row, joined with everything dispatch needs."""

    id: str
    channel_id: str
    channel_config: dict[str, Any]
    attempts: int  # attempts BEFORE this one
    alert: NotifiableAlert


class NotifyRepo(abc.ABC):
    @abc.abstractmethod
    async def list_unnotified_alerts(self) -> list[NotifiableAlert]:
        """Firing alerts with ``notified_at IS NULL``, oldest first."""

    @abc.abstractmethod
    async def list_group_webhook_channels(self, group_id: str) -> list[WebhookChannel]:
        """The group's ``kind='webhook'`` channels."""

    @abc.abstractmethod
    async def create_pending_deliveries(self, alert_id: str, channel_ids: list[str]) -> int:
        """Insert one ``pending`` ledger row per channel, idempotently
        (``UNIQUE (alert_id, channel_id)`` — re-running a crashed fan-out is a
        no-op). Returns the number of NEW rows."""

    @abc.abstractmethod
    async def mark_notified(self, alert_id: str) -> None:
        """Stamp ``alerts.notified_at`` — the fan-out watermark. Stamped after
        the inserts, so a crash between the two re-runs the idempotent
        fan-out (at-least-once)."""

    @abc.abstractmethod
    async def revive_stalled_deliveries(
        self, stall_seconds: int, max_attempts: int
    ) -> list[tuple[str, str, str]]:
        """Flip ``delivering`` rows older than the stall window back into the
        retry path (a worker died mid-POST; the POST may or may not have
        landed — webhooks are at-least-once). Counts as an attempt; rows at
        the cap go ``dead``. Returns ``(delivery_id, channel_id, new_status)``
        so the tick can raise ``webhook_failed`` for newly dead rows."""

    @abc.abstractmethod
    async def list_dispatchable_deliveries(
        self, max_attempts: int, retry_seconds: int
    ) -> list[str]:
        """Ids of rows ready for a webhook job: ``pending`` immediately,
        ``failed`` under the attempt cap once the retry cool-off has passed."""

    @abc.abstractmethod
    async def claim_delivery(self, delivery_id: str) -> ClaimedDelivery | None:
        """``pending``/``failed`` → ``delivering``, returning the joined
        dispatch context — or None when the row is already claimed, finished,
        or gone (duplicate enqueue between ticks → later job no-ops)."""

    @abc.abstractmethod
    async def record_delivery_success(self, delivery_id: str, channel_id: str) -> None:
        """``delivering`` → ``delivered``; auto-resolves the channel's open
        ``webhook_failed`` alert (the channel demonstrably notifies again)."""

    @abc.abstractmethod
    async def record_delivery_failure(
        self, delivery_id: str, error: str, max_attempts: int
    ) -> str:
        """Record a failed attempt: attempts+1, keep the error; ``failed``
        below the cap, ``dead`` at it. Returns the new status."""

    @abc.abstractmethod
    async def raise_alert(self, condition: AlertCondition) -> None:
        """Raise-or-bump one alert (same upsert semantics as the monitor's
        ``sync_alerts``) — used for ``webhook_failed``."""


@dataclass
class PgNotifyRepo(NotifyRepo):
    database_url: str

    async def _connect(self):  # pragma: no cover - thin psycopg wrapper
        import psycopg

        return await psycopg.AsyncConnection.connect(self.database_url)

    _ALERT_JOIN = (
        " FROM stac_higher.alerts a"
        " LEFT JOIN stac_higher.collection_connections cc ON cc.id = a.association_id"
        " LEFT JOIN stac_higher.connections c"
        "   ON c.id = COALESCE(a.connection_id, cc.connection_id)"
        " LEFT JOIN stac_higher.notification_channels nch ON nch.id = a.channel_id"
        " LEFT JOIN stac_higher.collection_settings cs"
        "   ON cs.collection_id = a.collection_id"
    )
    _ALERT_COLUMNS = (
        "SELECT a.id, a.source, a.kind, a.message,"
        " COALESCE(c.group_id, nch.group_id, cs.group_id),"
        " a.connection_id, a.association_id, a.channel_id,"
        " c.name, COALESCE(a.collection_id, cc.collection_id),"
        " a.first_seen, a.last_seen"
    )

    @staticmethod
    def _alert_from_row(r: Any) -> NotifiableAlert:
        return NotifiableAlert(
            id=str(r[0]),
            source=r[1],
            kind=r[2],
            message=r[3],
            group_id=r[4],
            connection_id=str(r[5]) if r[5] else None,
            association_id=str(r[6]) if r[6] else None,
            channel_id=str(r[7]) if r[7] else None,
            connection_name=r[8],
            collection_id=r[9],
            first_seen=r[10],
            last_seen=r[11],
        )

    async def list_unnotified_alerts(self) -> list[NotifiableAlert]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"{self._ALERT_COLUMNS}{self._ALERT_JOIN}"
                " WHERE a.state = 'firing' AND a.notified_at IS NULL"
                " ORDER BY a.first_seen"
            )
            rows = await cur.fetchall()
        return [self._alert_from_row(r) for r in rows]

    async def list_group_webhook_channels(  # pragma: no cover
        self, group_id: str
    ) -> list[WebhookChannel]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id, group_id, config FROM stac_higher.notification_channels"
                " WHERE group_id = %s AND kind = 'webhook'",
                (group_id,),
            )
            rows = await cur.fetchall()
        return [
            WebhookChannel(id=str(r[0]), group_id=r[1], config=dict(r[2]) if r[2] else {})
            for r in rows
        ]

    async def create_pending_deliveries(  # pragma: no cover
        self, alert_id: str, channel_ids: list[str]
    ) -> int:
        if not channel_ids:
            return 0
        created = 0
        async with await self._connect() as conn:
            for channel_id in channel_ids:
                cur = await conn.execute(
                    "INSERT INTO stac_higher.notification_deliveries (alert_id, channel_id)"
                    " VALUES (%s, %s) ON CONFLICT (alert_id, channel_id) DO NOTHING",
                    (alert_id, channel_id),
                )
                created += cur.rowcount or 0
            await conn.commit()
        return created

    async def mark_notified(self, alert_id: str) -> None:  # pragma: no cover
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.alerts SET notified_at = now() WHERE id = %s",
                (alert_id,),
            )
            await conn.commit()

    async def revive_stalled_deliveries(  # pragma: no cover
        self, stall_seconds: int, max_attempts: int
    ) -> list[tuple[str, str, str]]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.notification_deliveries"
                " SET attempts = attempts + 1, updated_at = now(),"
                "     last_error = COALESCE(last_error, 'stalled mid-delivery'),"
                "     status = CASE WHEN attempts + 1 >= %s THEN 'dead' ELSE 'failed' END"
                " WHERE status = 'delivering'"
                " AND updated_at < now() - make_interval(secs => %s)"
                " RETURNING id, channel_id, status",
                (max_attempts, stall_seconds),
            )
            rows = await cur.fetchall()
            await conn.commit()
        return [(str(r[0]), str(r[1]), r[2]) for r in rows]

    async def list_dispatchable_deliveries(  # pragma: no cover
        self, max_attempts: int, retry_seconds: int
    ) -> list[str]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id FROM stac_higher.notification_deliveries"
                " WHERE status = 'pending'"
                " OR (status = 'failed' AND attempts < %s"
                "     AND updated_at < now() - make_interval(secs => %s))"
                " ORDER BY updated_at",
                (max_attempts, retry_seconds),
            )
            rows = await cur.fetchall()
        return [str(r[0]) for r in rows]

    async def claim_delivery(  # pragma: no cover
        self, delivery_id: str
    ) -> ClaimedDelivery | None:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.notification_deliveries"
                " SET status = 'delivering', updated_at = now()"
                " WHERE id = %s AND status IN ('pending', 'failed')"
                " RETURNING alert_id, channel_id, attempts",
                (delivery_id,),
            )
            row = await cur.fetchone()
            if row is None:
                await conn.commit()
                return None
            alert_id, channel_id, attempts = str(row[0]), str(row[1]), int(row[2])
            cur = await conn.execute(
                f"{self._ALERT_COLUMNS}{self._ALERT_JOIN} WHERE a.id = %s",
                (alert_id,),
            )
            alert_row = await cur.fetchone()
            cur = await conn.execute(
                "SELECT config FROM stac_higher.notification_channels WHERE id = %s",
                (channel_id,),
            )
            channel_row = await cur.fetchone()
            await conn.commit()
        if alert_row is None or channel_row is None:
            return None
        return ClaimedDelivery(
            id=delivery_id,
            channel_id=channel_id,
            channel_config=dict(channel_row[0]) if channel_row[0] else {},
            attempts=attempts,
            alert=self._alert_from_row(alert_row),
        )

    async def record_delivery_success(  # pragma: no cover
        self, delivery_id: str, channel_id: str
    ) -> None:
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.notification_deliveries"
                " SET status = 'delivered', last_error = NULL, updated_at = now()"
                " WHERE id = %s",
                (delivery_id,),
            )
            # The channel notifies again — its open failure alert is over.
            await conn.execute(
                "UPDATE stac_higher.alerts"
                " SET state = 'resolved', resolved_at = now()"
                " WHERE state <> 'resolved' AND kind = %s AND channel_id = %s",
                (WEBHOOK_FAILED_KIND, channel_id),
            )
            await conn.commit()

    async def record_delivery_failure(  # pragma: no cover
        self, delivery_id: str, error: str, max_attempts: int
    ) -> str:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.notification_deliveries"
                " SET attempts = attempts + 1, last_error = %s, updated_at = now(),"
                "     status = CASE WHEN attempts + 1 >= %s THEN 'dead' ELSE 'failed' END"
                " WHERE id = %s"
                " RETURNING status",
                (error, max_attempts, delivery_id),
            )
            row = await cur.fetchone()
            await conn.commit()
        return row[0] if row else "failed"

    async def raise_alert(self, condition: AlertCondition) -> None:  # pragma: no cover
        async with await self._connect() as conn:
            # The conflict target must match the migration-021 open-dedup
            # index expressions EXACTLY (lockstep with flow/repo.py).
            await conn.execute(
                "INSERT INTO stac_higher.alerts"
                " (source, kind, connection_id, association_id, channel_id,"
                "  collection_id, message)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (source, kind,"
                "   coalesce(connection_id::text, ''),"
                "   coalesce(association_id::text, ''),"
                "   coalesce(channel_id::text, ''),"
                "   coalesce(collection_id, ''))"
                " WHERE state <> 'resolved'"
                " DO UPDATE SET last_seen = now(), message = EXCLUDED.message",
                (
                    condition.source,
                    condition.kind,
                    condition.connection_id,
                    condition.association_id,
                    condition.channel_id,
                    condition.collection_id,
                    condition.message,
                ),
            )
            await conn.commit()
