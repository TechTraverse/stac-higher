"""A small Kubernetes API client on the standard library (K-5, plan K5-R1).

The Kubernetes executor makes a handful of REST calls and two watches, all on
JSON it treats as plain dicts, so it talks to the API server directly — the
Docker executor's no-SDK precedent: a client library would widen the
dependency surface of a security-critical component, and the official one
still leaves ``410 Gone`` handling to its caller. In-cluster auth is the
pod's projected ServiceAccount token, READ ON EVERY REQUEST (bound tokens
rotate about hourly; an open watch ends within its ``timeoutSeconds`` and
reconnects with the current token), and the mounted cluster CA. There is no
kubeconfig parsing: outside a cluster (K-6's CI) the settings name a URL, a
token file and a CA file.

Errors are :class:`~pipeline.process.executor.ExecutorUnavailable` subclasses
carrying the API's ``Status`` (``status``, ``reason``); a request that never
got an answer is a plain ``ExecutorUnavailable``. Environment proxies and
redirects are ignored: the bearer token goes to the API server and nowhere
else.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pipeline.process.executor import ExecutorUnavailable
from pipeline.process.http_stream import (
    HttpStreamError,
    is_chunked,
    iter_body_lines,
    read_http_head,
)

if TYPE_CHECKING:
    from pipeline.config import Settings

#: Per-request timeout for REST calls and for opening a watch.
REQUEST_TIMEOUT_SECONDS = 10
#: How much of an API server's error message an exception carries.
_MESSAGE_CHARS = 300
_ERROR_BODY_BYTES = 64 * 1024


class KubeApiError(ExecutorUnavailable):
    """The API server answered with an error ``Status``."""

    def __init__(self, message: str, *, status: int, reason: str) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason


class KubeNotFound(KubeApiError):
    """404: the object (or the API, e.g. Kueue not installed) does not exist."""


class KubeGone(KubeApiError):
    """410: the resourceVersion a watch or list asked for is compacted."""


def _error(method: str, path: str, status: int, payload: bytes, http_reason: str,
           *, redact: bool) -> KubeApiError:
    reason, message = http_reason, ""
    try:
        doc = json.loads(payload)
    except ValueError:
        doc = None
    if isinstance(doc, dict) and doc.get("kind") == "Status":
        reason = str(doc.get("reason") or http_reason)
        message = str(doc.get("message") or "")
    text = f"kubernetes {method} {path} failed: {status} {reason}"
    if message and not redact:
        text += f": {message[:_MESSAGE_CHARS]}"
    cls = KubeNotFound if status == 404 else KubeGone if status == 410 else KubeApiError
    return cls(text, status=status, reason=reason)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the bearer token elsewhere: refuse it."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


@dataclass(frozen=True)
class KubeConfig:
    api_url: str
    token_file: Path
    #: The cluster CA. None only for a loopback ``http://`` URL (the tests).
    ca_file: Path | None
    request_timeout_seconds: int = REQUEST_TIMEOUT_SECONDS
    #: Test seam: builds the TLS context (``cafile=`` keyword).
    ssl_context_factory: Callable[..., ssl.SSLContext] = field(
        default=ssl.create_default_context, repr=False, compare=False
    )


class KubeApi:
    """Blocking REST calls (``asyncio.to_thread`` from async code) and an
    async watch. Building one does no I/O."""

    def __init__(self, config: KubeConfig) -> None:
        self.config = config
        parsed = urllib.parse.urlsplit(config.api_url)
        self._scheme = parsed.scheme
        self._host = parsed.hostname or ""
        self._port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self._base = config.api_url.rstrip("/")
        self._ssl: ssl.SSLContext | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> KubeApi:
        url = settings.process_k8s_api_url
        if not url:
            raise ExecutorUnavailable(
                "PROCESS_EXECUTOR=kubernetes but PROCESS_K8S_API_URL is unset and the "
                "pipeline is not running in a cluster (no KUBERNETES_SERVICE_HOST)"
            )
        https = urllib.parse.urlsplit(url).scheme == "https"
        return cls(KubeConfig(
            api_url=url,
            token_file=Path(settings.process_k8s_token_file),
            ca_file=Path(settings.process_k8s_ca_file) if https else None,
        ))

    # -- plumbing -------------------------------------------------------------

    def ssl_context(self) -> ssl.SSLContext:
        """The TLS context trusting the cluster CA, built on first use."""
        if self._ssl is None:
            ca = self.config.ca_file
            try:
                ctx = (
                    self.config.ssl_context_factory(cafile=str(ca))
                    if ca is not None else self.config.ssl_context_factory()
                )
            except (OSError, ssl.SSLError) as err:
                raise ExecutorUnavailable(
                    f"cannot load the Kubernetes CA at {ca}: {err}"
                ) from err
            ctx.check_hostname = True
            ctx.verify_mode = ssl.CERT_REQUIRED
            self._ssl = ctx
        return self._ssl

    def _token(self) -> str:
        path = self.config.token_file
        try:
            token = Path(path).read_text().strip()
        except OSError as err:
            raise ExecutorUnavailable(
                f"cannot read the Kubernetes token at {path}: {err.strerror or err}"
            ) from err
        if not token:
            raise ExecutorUnavailable(f"the Kubernetes token at {path} is empty")
        return token

    def _url(self, path: str, query: Mapping[str, str] | None) -> str:
        return f"{self._base}{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")

    def _opener(self) -> urllib.request.OpenerDirector:
        handlers: list[urllib.request.BaseHandler] = [
            urllib.request.ProxyHandler({}), _NoRedirect()
        ]
        if self._scheme == "https":
            handlers.append(urllib.request.HTTPSHandler(context=self.ssl_context()))
        return urllib.request.build_opener(*handlers)

    def _request(self, method: str, path: str, *, query: Mapping[str, str] | None = None,
                 body: dict | None = None, content_type: str = "application/json",
                 redact: bool = False) -> bytes:
        opener = self._opener()
        request = urllib.request.Request(
            self._url(path, query),
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
        )
        request.add_header("Authorization", f"Bearer {self._token()}")
        request.add_header("Accept", "application/json")
        if body is not None:
            request.add_header("Content-Type", content_type)
        try:
            with opener.open(request, timeout=self.config.request_timeout_seconds) as response:
                return response.read()
        except urllib.error.HTTPError as err:
            try:
                payload = err.read(_ERROR_BODY_BYTES)
            except OSError:
                payload = b""
            error = _error(method, path, err.code, payload, str(err.reason), redact=redact)
            if redact:
                raise error from None
            raise error from err
        except (urllib.error.URLError, OSError, http.client.HTTPException) as err:
            raise ExecutorUnavailable(
                f"kubernetes {method} {path} unreachable at {self.config.api_url}: {err}"
            ) from err

    def _json(self, payload: bytes, method: str, path: str) -> dict:
        try:
            doc = json.loads(payload) if payload else {}
        except ValueError as err:
            raise ExecutorUnavailable(f"kubernetes {method} {path} returned non-JSON") from err
        if not isinstance(doc, dict):
            raise ExecutorUnavailable(f"kubernetes {method} {path} returned {type(doc).__name__}")
        return doc

    # -- REST -----------------------------------------------------------------

    def get(self, path: str, *, query: Mapping[str, str] | None = None) -> dict:
        return self._json(self._request("GET", path, query=query), "GET", path)

    def get_raw(self, path: str, *, query: Mapping[str, str] | None = None) -> bytes:
        return self._request("GET", path, query=query)

    def list(self, path: str, *, label_selector: str, limit: int = 500
             ) -> tuple[list[dict], str]:
        """Every item matching ``label_selector`` (following ``continue``),
        and the resourceVersion of the last page."""
        items: list[dict] = []
        query = {"labelSelector": label_selector, "limit": str(limit)}
        while True:
            doc = self.get(path, query=query)
            items.extend(i for i in doc.get("items") or [] if isinstance(i, dict))
            meta = doc.get("metadata") or {}
            token = meta.get("continue")
            if not token:
                return items, str(meta.get("resourceVersion") or "")
            query = {**query, "continue": token}

    def create(self, path: str, body: dict, *, redact: bool = False) -> dict:
        """POST ``body``. ``redact``: an error never echoes the server's
        message (a Secret's validation error can quote its data)."""
        return self._json(self._request("POST", path, body=body, redact=redact), "POST", path)

    def patch(self, path: str, body: dict) -> dict:
        return self._json(
            self._request("PATCH", path, body=body,
                          content_type="application/merge-patch+json"),
            "PATCH", path,
        )

    def delete(self, path: str, *, propagation: str | None = None) -> None:
        query = {"propagationPolicy": propagation} if propagation else None
        self._request("DELETE", path, query=query)

    # -- watch ----------------------------------------------------------------

    async def watch(self, path: str, *, query: Mapping[str, str],
                    idle_timeout: float | None = None) -> AsyncIterator[dict]:
        """One decoded ``{"type", "object"}`` per event line until the server
        ends the stream (a normal return). Raises :class:`KubeGone` on HTTP
        410 or an ``ERROR`` 410 event, :class:`KubeApiError` on any other
        error status, and ``ExecutorUnavailable`` on a transport error or
        ``idle_timeout`` seconds without a line."""
        full_query = {**query, "watch": "1"}
        target = f"{path}?{urllib.parse.urlencode(full_query)}"
        token = self._token()
        tls = self.ssl_context() if self._scheme == "https" else None
        timeout = self.config.request_timeout_seconds
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self._host, self._port, ssl=tls,
                    server_hostname=self._host if tls is not None else None,
                ),
                timeout,
            )
        except (OSError, TimeoutError, ssl.SSLError) as err:
            raise ExecutorUnavailable(
                f"kubernetes watch {path} unreachable at {self.config.api_url}: {err}"
            ) from err
        lines: AsyncIterator[bytes] | None = None
        try:
            host = f"[{self._host}]" if ":" in self._host else self._host
            writer.write(
                f"GET {target} HTTP/1.1\r\nHost: {host}:{self._port}\r\n"
                f"Authorization: Bearer {token}\r\nAccept: application/json\r\n"
                "Connection: close\r\n\r\n".encode("latin-1")
            )
            await writer.drain()
            status, headers = await asyncio.wait_for(read_http_head(reader), timeout)
            chunked = is_chunked(headers)
            if status != 200:
                body = b""
                with contextlib.suppress(Exception):
                    body = await asyncio.wait_for(_read_body(reader, chunked), timeout)
                raise _error("GET", path, status, body, "", redact=False)
            lines = iter_body_lines(reader, chunked=chunked)
            while True:
                try:
                    if idle_timeout is None:
                        line = await lines.__anext__()
                    else:
                        line = await asyncio.wait_for(lines.__anext__(), idle_timeout)
                except StopAsyncIteration:
                    return
                except TimeoutError as err:
                    raise ExecutorUnavailable(
                        f"kubernetes watch {path} idle for {idle_timeout:g}s"
                    ) from err
                try:
                    event = json.loads(line)
                except ValueError as err:
                    raise ExecutorUnavailable(
                        f"kubernetes watch {path} sent a line that is not JSON"
                    ) from err
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "ERROR":
                    raise _watch_error(path, event.get("object"))
                yield event
        except (HttpStreamError, OSError, asyncio.IncompleteReadError, ssl.SSLError) as err:
            raise ExecutorUnavailable(f"kubernetes watch {path} broke: {err}") from err
        finally:
            if lines is not None:
                with contextlib.suppress(Exception):
                    await lines.aclose()  # type: ignore[attr-defined]
            writer.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(writer.wait_closed(), 1.0)


async def _read_body(reader: asyncio.StreamReader, chunked: bool) -> bytes:
    out = b""
    async for line in iter_body_lines(reader, chunked=chunked):
        out += line
        if len(out) > _ERROR_BODY_BYTES:
            break
    return out


def _watch_error(path: str, obj: object) -> KubeApiError:
    code = obj.get("code") if isinstance(obj, dict) else None
    status = code if isinstance(code, int) else 500
    payload = json.dumps(obj).encode() if isinstance(obj, dict) else b""
    return _error("WATCH", path, status, payload, "", redact=False)
