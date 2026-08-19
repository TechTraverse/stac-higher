"""Typed view over a webhook channel's ``config`` jsonb (M2-C, ADR 0010).

Python side of the cross-runtime contract: the app writes the config through
``app/src/lib/notifications/schemas.ts`` (Zod, strict) and this reader parses
the same JSON back out of ``notification_channels.config`` at dispatch time.
Field names must not drift; the golden fixture in
``tests/contract-fixtures/webhook-channel-config.json`` keeps both sides
honest.

Lenient-reader semantics (matches the other config parsers): unknown keys are
ignored, but a missing/unusable ``url`` raises — a silently mis-read target
would drop notifications invisibly. Scheme and egress policy are enforced at
dispatch time (``webhook.deliver``), not here, so a row with an odd-scheme URL
fails loudly per attempt rather than becoming unparseable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class NotificationConfigError(ValueError):
    """The stored channel ``config`` jsonb is not a usable webhook config."""


@dataclass(frozen=True)
class WebhookConfig:
    url: str
    #: Optional shared secret; when set, each POST body is signed with
    #: HMAC-SHA256 in the X-StacHigher-Signature header.
    secret: str | None = None


def parse_webhook_config(raw: dict[str, Any] | None) -> WebhookConfig:
    if not isinstance(raw, dict):
        raise NotificationConfigError(f"webhook config must be an object, got {raw!r}")
    url = raw.get("url")
    if not isinstance(url, str) or not url.strip():
        raise NotificationConfigError(f"webhook config needs a non-empty url, got {url!r}")
    secret = raw.get("secret")
    if secret is not None and not isinstance(secret, str):
        raise NotificationConfigError(f"webhook secret must be a string, got {secret!r}")
    return WebhookConfig(url=url.strip(), secret=secret or None)
