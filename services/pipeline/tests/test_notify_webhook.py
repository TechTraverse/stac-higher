"""M2-C webhook transport: signing, scheme/egress policy, HTTP outcomes."""

from __future__ import annotations

import hashlib
import hmac
import http.server
import threading
from typing import ClassVar

import pytest

from pipeline.notify.config import (
    NotificationConfigError,
    WebhookConfig,
    parse_webhook_config,
)
from pipeline.notify.webhook import (
    SIGNATURE_HEADER,
    WebhookDeliveryError,
    deliver,
    sign,
)


class TestParseWebhookConfig:
    def test_minimal(self):
        cfg = parse_webhook_config({"url": " https://h.example/x "})
        assert cfg == WebhookConfig(url="https://h.example/x", secret=None)

    def test_secret_kept(self):
        cfg = parse_webhook_config({"url": "https://h.example/x", "secret": "s"})
        assert cfg.secret == "s"

    def test_empty_secret_normalizes_to_none(self):
        assert parse_webhook_config({"url": "https://h", "secret": ""}).secret is None

    def test_rejects_non_dict(self):
        with pytest.raises(NotificationConfigError):
            parse_webhook_config(None)

    def test_rejects_missing_url(self):
        with pytest.raises(NotificationConfigError):
            parse_webhook_config({})


class TestSign:
    def test_matches_manual_hmac(self):
        body = b'{"event":"alert.firing"}'
        expected = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        assert sign(body, "secret") == f"sha256={expected}"


class _Handler(http.server.BaseHTTPRequestHandler):
    status: ClassVar[int] = 200
    received: ClassVar[list[dict]] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        _Handler.received.append(
            {
                "path": self.path,
                "body": self.rfile.read(length),
                "signature": self.headers.get(SIGNATURE_HEADER),
                "content_type": self.headers.get("Content-Type"),
            }
        )
        self.send_response(_Handler.status)
        self.end_headers()

    def log_message(self, *args):  # quiet
        pass


@pytest.fixture
def local_server():
    _Handler.received = []
    _Handler.status = 200
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


ALLOW_LOCAL = frozenset({"127.0.0.1"})


class TestDeliver:
    def test_posts_signed_body_to_allowlisted_local_target(self, local_server):
        deliver(
            WebhookConfig(url=f"{local_server}/hook?x=1", secret="s"),
            b'{"a":1}',
            allow_hosts=ALLOW_LOCAL,
        )
        [req] = _Handler.received
        assert req["path"] == "/hook?x=1"
        assert req["body"] == b'{"a":1}'
        assert req["signature"] == sign(b'{"a":1}', "s")
        assert req["content_type"] == "application/json"

    def test_no_signature_without_secret(self, local_server):
        deliver(WebhookConfig(url=f"{local_server}/hook"), b"{}", allow_hosts=ALLOW_LOCAL)
        assert _Handler.received[0]["signature"] is None

    def test_non_2xx_raises(self, local_server):
        _Handler.status = 500
        with pytest.raises(WebhookDeliveryError, match="500"):
            deliver(WebhookConfig(url=f"{local_server}/hook"), b"{}", allow_hosts=ALLOW_LOCAL)

    def test_loopback_is_blocked_without_allowlist(self, local_server):
        with pytest.raises(WebhookDeliveryError, match="blocked"):
            deliver(WebhookConfig(url=f"{local_server}/hook"), b"{}")

    def test_non_http_scheme_rejected(self):
        with pytest.raises(WebhookDeliveryError, match="http"):
            deliver(WebhookConfig(url="ftp://h.example/x"), b"{}")

    def test_connection_refused_is_a_delivery_error(self):
        # Port 9 on loopback (allowlisted) — nothing listens there.
        with pytest.raises(WebhookDeliveryError):
            deliver(
                WebhookConfig(url="http://127.0.0.1:9/hook"),
                b"{}",
                allow_hosts=ALLOW_LOCAL,
                timeout=2.0,
            )
