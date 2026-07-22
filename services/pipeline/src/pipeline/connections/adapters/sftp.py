"""SFTP/SSH adapter — asyncssh driven.

Serves both the ``sftp`` and ``ssh`` protocols (same transport; SFTP subsystem).
Host-key verification is intentionally delegated to our own TOFU layer, so we
connect with ``known_hosts=None`` and expose the observed server key for the
drain/health-sweep jobs to pin/compare. ``root_path`` scopes every path.
"""

from __future__ import annotations

import posixpath
import stat
import time
from typing import Any

import asyncssh

from pipeline.connections.adapters.base import FileEntry, StorageAdapter, TestResult
from pipeline.connections.egress import EgressBlocked, resolve_pinned


def host_key_string(key: asyncssh.SSHKey) -> str:
    """Stable string form of a server host key for pinning/comparison.

    ``<keytype> <base64-openssh-wire>`` — the same canonical form OpenSSH uses
    in ``known_hosts``, so a fingerprint can be derived downstream. Never log
    the raw value.
    """
    # export_public_key('openssh') -> b"ssh-ed25519 AAAA... [comment]"
    exported = key.export_public_key("openssh").decode("ascii").strip()
    parts = exported.split()
    # drop any trailing comment; keep "<type> <base64>"
    return " ".join(parts[:2])


class SftpAdapter(StorageAdapter):
    def __init__(
        self,
        config: dict[str, Any],
        credentials: dict[str, Any],
        protocol: str = "sftp",
        allow_hosts: frozenset[str] = frozenset(),
    ) -> None:
        self.protocol = protocol
        self._host = config["host"]
        self._port = int(config.get("port", 22))
        self._root = config.get("root_path", "/") or "/"
        self._creds = credentials
        self._allow_hosts = allow_hosts

    def _resolve(self, path: str) -> str:
        """Join a caller path under root_path (defends against absolute paths)."""
        return posixpath.normpath(posixpath.join(self._root, path.lstrip("/")))

    def _pinned_host(self) -> str:
        """Resolve+validate once, return the IP to dial (defeats rebinding).

        Allowlisted (operator-vouched, compose-internal) hosts resolve to ``[]``
        and we fall back to the hostname. Host identity is verified by our TOFU
        layer against the observed server key, so dialing a bare IP is safe.
        """
        pinned = resolve_pinned(self._host, self._allow_hosts)
        return pinned[0] if pinned else self._host

    def _connect_kwargs(self, host: str) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "host": host,
            "port": self._port,
            "username": self._creds.get("username"),
            # our TOFU layer verifies the key; disable asyncssh's own check.
            "known_hosts": None,
            "connect_timeout": 15,
        }
        password = self._creds.get("password")
        if password:
            kwargs["password"] = password
        private_key = self._creds.get("private_key")
        if private_key:
            passphrase = self._creds.get("passphrase")
            kwargs["client_keys"] = [asyncssh.import_private_key(private_key, passphrase)]
        return kwargs

    async def _connect(self) -> asyncssh.SSHClientConnection:
        host = self._pinned_host()
        return await asyncssh.connect(**self._connect_kwargs(host))

    async def test(self) -> TestResult:
        started = time.monotonic()
        try:
            host = self._pinned_host()
        except EgressBlocked as exc:
            return {"ok": False, "message": str(exc)}
        try:
            async with await asyncssh.connect(**self._connect_kwargs(host)) as conn:
                observed = host_key_string(conn.get_server_host_key())
                async with conn.start_sftp_client() as sftp:
                    # prove the root is reachable/authorized
                    await sftp.stat(self._root)
        except (OSError, asyncssh.Error) as exc:
            return {"ok": False, "message": f"SFTP test failed: {exc}"}
        latency_ms = int((time.monotonic() - started) * 1000)
        return {
            "ok": True,
            "message": f"{self.protocol} endpoint reachable",
            "host_key": observed,
            "latency_ms": latency_ms,
        }

    async def list(self, prefix: str = "") -> list[FileEntry]:
        target = self._resolve(prefix)
        async with await self._connect() as conn, conn.start_sftp_client() as sftp:
            names = await sftp.readdir(target)
        entries: list[FileEntry] = []
        for entry in names:
            name = entry.filename
            if name in (".", ".."):
                continue
            attrs = entry.attrs
            permissions = getattr(attrs, "permissions", None)
            mtime = getattr(attrs, "mtime", None)
            entries.append(
                FileEntry(
                    path=name,
                    size=getattr(attrs, "size", None),
                    mtime=float(mtime) if mtime is not None else None,
                    is_dir=permissions is not None and stat.S_ISDIR(permissions),
                )
            )
        return entries

    async def get(self, path: str) -> bytes:
        target = self._resolve(path)
        async with (
            await self._connect() as conn,
            conn.start_sftp_client() as sftp,
            sftp.open(target, "rb") as fh,
        ):
            return await fh.read()

    async def put(self, path: str, data: bytes) -> None:
        target = self._resolve(path)
        async with (
            await self._connect() as conn,
            conn.start_sftp_client() as sftp,
            sftp.open(target, "wb") as fh,
        ):
            await fh.write(data)

    async def delete(self, path: str) -> None:
        target = self._resolve(path)
        async with await self._connect() as conn, conn.start_sftp_client() as sftp:
            await sftp.remove(target)

    async def move(self, src: str, dst: str) -> None:
        src_t, dst_t = self._resolve(src), self._resolve(dst)
        async with await self._connect() as conn, conn.start_sftp_client() as sftp:
            # posix_rename atomically overwrites an existing dst where the server
            # supports the openssh extension (our target servers do).
            await sftp.posix_rename(src_t, dst_t)
