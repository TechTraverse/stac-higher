"""Webhook HTTP dispatch with the connections egress policy (M2-C, spec §4).

Webhook egress lives HERE, pipeline-side, and NOT in the app: the app's
``safeFetch`` guard stays narrow by design, while a webhook target is
operator-declared configuration in the same trust class as a connection
endpoint. The same ``resolve_pinned`` codepath the adapters use resolves and
range-checks the host ONCE, and the socket dials the returned IP literal —
closing the DNS-rebinding TOCTOU hole — while TLS verification and the Host
header keep using the original hostname.

Delivery is a single POST; anything but a 2xx raises (redirects included —
following one would re-open the egress question at a new host). Retries and
dead-lettering live in the ``notification_deliveries`` ledger, not here.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import socket
import ssl
import urllib.parse
from collections.abc import Iterable

from pipeline.connections.egress import resolve_pinned
from pipeline.notify.config import WebhookConfig

SIGNATURE_HEADER = "X-StacHigher-Signature"
USER_AGENT = "stac-higher-pipeline"


class WebhookDeliveryError(Exception):
    """The webhook POST could not be delivered (policy, network, or non-2xx)."""


def sign(body: bytes, secret: str) -> str:
    """HMAC-SHA256 signature header value for ``body``."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to a pre-resolved IP, with SNI/verification on the hostname."""

    def __init__(
        self, pinned_ip: str, host: str, port: int, *, context: ssl.SSLContext, timeout: float
    ) -> None:
        super().__init__(host, port, timeout=timeout, context=context)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:  # pragma: no cover - exercised against real sockets
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def deliver(
    config: WebhookConfig,
    body: bytes,
    *,
    allow_hosts: Iterable[str] = (),
    timeout: float = 10.0,
) -> None:
    """POST ``body`` to the webhook target, or raise :class:`WebhookDeliveryError`.

    Blocking — callers run it in a thread (``asyncio.to_thread``).
    """
    parts = urllib.parse.urlsplit(config.url)
    if parts.scheme not in ("http", "https"):
        raise WebhookDeliveryError(f"webhook url must be http(s), got {config.url!r}")
    host = parts.hostname
    if not host:
        raise WebhookDeliveryError(f"webhook url has no host: {config.url!r}")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))

    try:
        pinned = resolve_pinned(host, allow_hosts)
    except Exception as exc:
        raise WebhookDeliveryError(str(exc)) from exc

    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        # Explicit Host so a pinned-IP connection still addresses the vhost.
        "Host": host if port in (80, 443) else f"{host}:{port}",
    }
    if config.secret:
        headers[SIGNATURE_HEADER] = sign(body, config.secret)

    conn: http.client.HTTPConnection
    if parts.scheme == "https":
        context = ssl.create_default_context()
        if pinned:
            conn = _PinnedHTTPSConnection(
                pinned[0], host, port, context=context, timeout=timeout
            )
        else:  # allowlisted host — connect by name
            conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=context)
    else:
        conn = http.client.HTTPConnection(pinned[0] if pinned else host, port, timeout=timeout)

    try:
        conn.request("POST", path, body=body, headers=headers)
        response = conn.getresponse()
        # Drain so the connection can close cleanly; body content is ignored.
        response.read()
        if not 200 <= response.status < 300:
            raise WebhookDeliveryError(f"webhook target answered {response.status}")
    except WebhookDeliveryError:
        raise
    except OSError as exc:
        raise WebhookDeliveryError(f"webhook POST failed: {exc}") from exc
    finally:
        conn.close()
