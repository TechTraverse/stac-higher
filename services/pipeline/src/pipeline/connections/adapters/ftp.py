"""FTP adapter — aioftp driven (plaintext control + data channels).

FTPS extends this in ``ftps.py``. ``root_path`` scopes every path.

SSRF hardening (two egress holes an adversarial review found):

- **DNS-rebinding TOCTOU** — the control host is resolved+validated ONCE via
  :func:`resolve_pinned` and the socket is dialed at the returned IP literal,
  so a low-TTL rebind between check and connect cannot reach an internal
  address. (FTPS dials by hostname instead — see ``ftps.py`` — because TLS
  certificate validation needs the name; that path keeps a narrow rebind
  window, documented there.)
- **PASV/EPSV data-channel redirect** — aioftp opens the data connection to
  whatever IP the server advertises in its PASV/EPSV reply.
  :class:`_EgressFtpClient` overrides aioftp's one connection choke point
  (``_open_connection``) to force every *data* connection to the
  already-validated control host, so a malicious or compromised server cannot
  bounce the data channel to 169.254.169.254 or an RFC1918 address.
"""

from __future__ import annotations

import datetime as dt
import posixpath
import time
from typing import Any

import aioftp

from pipeline.connections.adapters.base import FileEntry, StorageAdapter, TestResult
from pipeline.connections.egress import EgressBlocked, resolve_pinned


def _parse_modify(value: str | None) -> float | None:
    """Parse an MLSD ``modify`` fact (``YYYYMMDDHHMMSS[.frac]``, UTC) to epoch
    seconds. Returns None when absent or unparseable."""
    if not value:
        return None
    try:
        stamp = dt.datetime.strptime(value[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return stamp.replace(tzinfo=dt.UTC).timestamp()


def _parse_size(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class _EgressFtpClient(aioftp.Client):
    """aioftp client that pins the data channel to the validated control host.

    ``_open_connection`` is aioftp's single entry point for opening BOTH the
    control connection (during ``connect()``, while ``self._stream`` is still
    ``None``) and each passive *data* connection (after the control stream
    exists). We force the data connections to the control host so a PASV/EPSV
    reply cannot redirect them (see module docstring). Allowlisted
    (operator-vouched, compose-internal) hosts skip the force — their data
    channel may legitimately live on a private address.
    """

    #: class-level defaults so an unconfigured instance behaves like a stock
    #: client (never forces).
    _eg_control_host: str | None = None
    _eg_host_allowlisted: bool = False

    def configure_egress(self, control_host: str, host_allowlisted: bool) -> None:
        self._eg_control_host = control_host
        self._eg_host_allowlisted = host_allowlisted

    async def _open_connection(self, host: str, port: int) -> Any:  # type: ignore[override]
        if (
            self._stream is not None
            and not self._eg_host_allowlisted
            and self._eg_control_host is not None
        ):
            # passive/data connection — ignore the server-advertised address and
            # dial the validated control host instead.
            host = self._eg_control_host
        return await super()._open_connection(host, port)


class FtpAdapter(StorageAdapter):
    protocol = "ftp"
    default_port = 21

    def __init__(
        self,
        config: dict[str, Any],
        credentials: dict[str, Any],
        allow_hosts: frozenset[str] = frozenset(),
    ) -> None:
        self._host = config["host"]
        self._port = int(config.get("port", self.default_port))
        self._root = config.get("root_path", "/") or "/"
        self._creds = credentials
        self._allow_hosts = allow_hosts

    def _resolve(self, path: str) -> str:
        return posixpath.normpath(posixpath.join(self._root, path.lstrip("/")))

    def _make_client(self) -> _EgressFtpClient:
        """Plain client. FTPS overrides to supply a TLS context / upgrade."""
        return _EgressFtpClient(socket_timeout=15, connection_timeout=15)

    def _dial_host(self, pinned: list[str]) -> str:
        """Host to dial for the control channel.

        Plain FTP dials the pinned IP literal (defeats DNS rebinding). FTPS
        overrides this to dial the hostname (TLS cert validation needs it).
        """
        return pinned[0]

    def _pin(self) -> tuple[str, bool]:
        """Resolve+validate the host once; return ``(dial_host, allowlisted)``.

        Raises :class:`EgressBlocked` for a disallowed host.
        """
        pinned = resolve_pinned(self._host, self._allow_hosts)
        if pinned:
            return self._dial_host(pinned), False
        # empty => operator-allowlisted (compose-internal); dial by name.
        return self._host, True

    async def _connect_client(self) -> _EgressFtpClient:
        """Egress-check + pin, connect, and log in; caller must close."""
        dial_host, allowlisted = self._pin()
        client = self._make_client()
        client.configure_egress(dial_host, allowlisted)
        await client.connect(dial_host, self._port)
        await self._authenticate(client)
        return client

    async def _authenticate(self, client: aioftp.Client) -> None:
        await client.login(
            user=self._creds.get("username", "anonymous"),
            password=self._creds.get("password", ""),
        )

    async def test(self) -> TestResult:
        started = time.monotonic()
        try:
            client = await self._connect_client()
        except EgressBlocked as exc:
            return {"ok": False, "message": str(exc)}
        except (OSError, aioftp.AIOFTPException) as exc:
            return {"ok": False, "message": f"{self.protocol.upper()} test failed: {exc}"}
        try:
            # prove the root is listable (reachability + auth + path). This opens
            # a passive data channel — pinned to the control host.
            await client.list(self._root)
        except (OSError, aioftp.AIOFTPException) as exc:
            return {"ok": False, "message": f"{self.protocol.upper()} test failed: {exc}"}
        finally:
            await _safe_quit(client)
        latency_ms = int((time.monotonic() - started) * 1000)
        return {
            "ok": True,
            "message": f"{self.protocol} endpoint reachable",
            "latency_ms": latency_ms,
        }

    async def list(self, prefix: str = "") -> list[FileEntry]:
        client = await self._connect_client()
        try:
            target = self._resolve(prefix)
            listing = await client.list(target)
        finally:
            await _safe_quit(client)
        entries: list[FileEntry] = []
        for path, info in listing:
            entries.append(
                FileEntry(
                    path=str(path),
                    size=_parse_size(info.get("size")),
                    mtime=_parse_modify(info.get("modify")),
                    is_dir=info.get("type") == "dir",
                )
            )
        return entries

    async def get(self, path: str) -> bytes:
        client = await self._connect_client()
        try:
            async with client.download_stream(self._resolve(path)) as stream:
                return await stream.read()
        finally:
            await _safe_quit(client)

    async def put(self, path: str, data: bytes) -> None:
        client = await self._connect_client()
        try:
            async with client.upload_stream(self._resolve(path)) as stream:
                await stream.write(data)
        finally:
            await _safe_quit(client)

    async def delete(self, path: str) -> None:
        client = await self._connect_client()
        try:
            await client.remove_file(self._resolve(path))
        finally:
            await _safe_quit(client)

    async def move(self, src: str, dst: str) -> None:
        client = await self._connect_client()
        try:
            await client.rename(self._resolve(src), self._resolve(dst))
        finally:
            await _safe_quit(client)


async def _safe_quit(client: aioftp.Client) -> None:
    """Best-effort graceful close; never raise from cleanup."""
    try:
        await client.quit()
    except (OSError, aioftp.AIOFTPException):
        client.close()
