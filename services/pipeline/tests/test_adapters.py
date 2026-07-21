"""Adapter ``test()`` behaviour with fully mocked clients — no live servers.

Each adapter's ``resolve_pinned`` symbol is monkeypatched to a spy so we assert
the egress gate is invoked with the config host and that adapters dial the
*pinned* IP it returns (the DNS-rebinding defence), without touching real DNS.
"""

from __future__ import annotations

import pathlib
from typing import ClassVar

import aioftp
import pytest

from pipeline.connections.adapters import (
    FtpAdapter,
    FtpsAdapter,
    S3Adapter,
    SftpAdapter,
    adapter_for,
)
from pipeline.connections.adapters import ftp as ftp_mod
from pipeline.connections.adapters import ftps as ftps_mod
from pipeline.connections.adapters import s3 as s3_mod
from pipeline.connections.adapters import sftp as sftp_mod
from pipeline.connections.adapters.base import StorageAdapter
from pipeline.connections.adapters.ftp import _EgressFtpClient
from pipeline.connections.egress import EgressBlocked

#: a public (TEST-NET-3) IP the pin spy returns — never in a blocked range.
_PINNED_IP = "203.0.113.10"


class _PinSpy:
    """Stand-in for :func:`egress.resolve_pinned`: records the host and returns
    a fixed pinned-IP list (``[]`` to simulate an allowlisted host), or raises
    :class:`EgressBlocked` when ``blocked``.
    """

    def __init__(self, pinned=(_PINNED_IP,), blocked: bool = False):
        self.calls: list[str] = []
        self._pinned = list(pinned)
        self._blocked = blocked

    def __call__(self, host, allow_hosts=()):
        self.calls.append(host)
        if self._blocked:
            raise EgressBlocked(f"blocked: {host}")
        return list(self._pinned)


# --------------------------------------------------------------------------- #
# S3
# --------------------------------------------------------------------------- #


class _FakeS3Client:
    def __init__(self, *, head_error: Exception | None = None):
        self._head_error = head_error
        self.head_called = False
        self.list_called = False

    def head_bucket(self, Bucket):
        self.head_called = True
        if self._head_error:
            raise self._head_error

    def list_objects_v2(self, Bucket, MaxKeys=None):
        self.list_called = True
        return {"Contents": []}


async def test_s3_test_ok(monkeypatch):
    spy = _PinSpy()
    monkeypatch.setattr(s3_mod, "resolve_pinned", spy)
    fake = _FakeS3Client()
    monkeypatch.setattr(s3_mod.boto3, "client", lambda *a, **k: fake)

    adapter = S3Adapter(
        {"bucket": "b", "region": "us-east-1"}, {"access_key_id": "x", "secret_access_key": "y"}
    )
    result = await adapter.test()

    assert result["ok"] is True
    assert "latency_ms" in result
    assert fake.head_called
    assert spy.calls == ["s3.us-east-1.amazonaws.com"]


async def test_s3_http_endpoint_pinned_to_ip(monkeypatch):
    """A custom http endpoint (MinIO) is rewritten to the validated IP so a DNS
    rebind cannot redirect the connection."""
    spy = _PinSpy()
    monkeypatch.setattr(s3_mod, "resolve_pinned", spy)
    seen: dict[str, object] = {}

    def _client(*a, **k):
        seen["endpoint_url"] = k.get("endpoint_url")
        return _FakeS3Client()

    monkeypatch.setattr(s3_mod.boto3, "client", _client)

    adapter = S3Adapter(
        {"bucket": "b", "endpoint": "http://minio:9000", "force_path_style": True},
        {"access_key_id": "x", "secret_access_key": "y"},
    )
    await adapter.test()
    assert spy.calls == ["minio"]
    assert seen["endpoint_url"] == f"http://{_PINNED_IP}:9000"


async def test_s3_https_endpoint_keeps_hostname(monkeypatch):
    """An https custom endpoint keeps its hostname (TLS/SNI) while still being
    egress-checked."""
    spy = _PinSpy(pinned=[])  # allowlisted-style: no pin
    monkeypatch.setattr(s3_mod, "resolve_pinned", spy)
    seen: dict[str, object] = {}

    def _client(*a, **k):
        seen["endpoint_url"] = k.get("endpoint_url")
        return _FakeS3Client()

    monkeypatch.setattr(s3_mod.boto3, "client", _client)

    adapter = S3Adapter(
        {"bucket": "b", "endpoint": "https://minio:9000", "force_path_style": True},
        {"access_key_id": "x", "secret_access_key": "y"},
        allow_hosts=frozenset({"minio"}),
    )
    await adapter.test()
    assert spy.calls == ["minio"]
    assert seen["endpoint_url"] == "https://minio:9000"


async def test_s3_test_failure(monkeypatch):
    from botocore.exceptions import ClientError

    monkeypatch.setattr(s3_mod, "resolve_pinned", _PinSpy())
    err = ClientError({"Error": {"Code": "403", "Message": "denied"}}, "HeadBucket")
    fake = _FakeS3Client(head_error=err)

    def _list_fail(Bucket, MaxKeys=None):
        raise ClientError({"Error": {"Code": "404", "Message": "no bucket"}}, "ListObjectsV2")

    fake.list_objects_v2 = _list_fail
    monkeypatch.setattr(s3_mod.boto3, "client", lambda *a, **k: fake)

    adapter = S3Adapter({"bucket": "b"}, {"access_key_id": "x", "secret_access_key": "y"})
    result = await adapter.test()
    assert result["ok"] is False
    assert "failed" in result["message"]


async def test_s3_egress_blocked_short_circuits(monkeypatch):
    monkeypatch.setattr(s3_mod, "resolve_pinned", _PinSpy(blocked=True))
    # boto3.client must never be called when egress blocks.
    monkeypatch.setattr(
        s3_mod.boto3, "client", lambda *a, **k: pytest.fail("client built despite block")
    )
    adapter = S3Adapter({"bucket": "b"}, {"access_key_id": "x", "secret_access_key": "y"})
    result = await adapter.test()
    assert result["ok"] is False
    assert "blocked" in result["message"]


def test_s3_public_object_url_path_style_custom_endpoint():
    from pipeline.connections.adapters.s3 import S3Adapter

    a = S3Adapter(
        {"bucket": "src-bucket", "endpoint": "http://minio:9000", "force_path_style": True},
        {"access_key_id": "k", "secret_access_key": "s"},
    )
    assert (
        a.public_object_url("products/scene.tif")
        == "http://minio:9000/src-bucket/products/scene.tif"
    )


def test_s3_public_object_url_virtual_hosted_default_aws():
    from pipeline.connections.adapters.s3 import S3Adapter

    a = S3Adapter(
        {"bucket": "src-bucket", "region": "us-west-2"},
        {"access_key_id": "k", "secret_access_key": "s"},
    )
    assert (
        a.public_object_url("products/scene.tif")
        == "https://src-bucket.s3.us-west-2.amazonaws.com/products/scene.tif"
    )


def test_base_adapter_public_object_url_raises():
    class Dummy(StorageAdapter):
        protocol = "dummy"

        async def test(self): ...

        async def list(self, prefix=""):
            return []

        async def get(self, path):
            return b""

        async def put(self, path, data): ...

        async def delete(self, path): ...

    with pytest.raises(NotImplementedError):
        Dummy().public_object_url("x")


# --------------------------------------------------------------------------- #
# SFTP / SSH
# --------------------------------------------------------------------------- #


class _FakeHostKey:
    def export_public_key(self, fmt):
        assert fmt == "openssh"
        return b"ssh-ed25519 AAAAOBSERVEDKEY comment-here"


class _FakeSFTP:
    def __init__(self):
        self.stat_path = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def stat(self, path):
        self.stat_path = path


class _FakeSSHConn:
    def __init__(self, sftp):
        self._sftp = sftp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get_server_host_key(self):
        return _FakeHostKey()

    def start_sftp_client(self):
        return self._sftp


async def test_sftp_test_ok_exposes_host_key(monkeypatch):
    spy = _PinSpy()
    monkeypatch.setattr(sftp_mod, "resolve_pinned", spy)
    sftp = _FakeSFTP()

    async def _connect(**kwargs):
        assert kwargs["known_hosts"] is None
        # dials the PINNED IP, not the hostname (DNS-rebinding defence)
        assert kwargs["host"] == _PINNED_IP
        return _FakeSSHConn(sftp)

    monkeypatch.setattr(sftp_mod.asyncssh, "connect", _connect)

    adapter = SftpAdapter(
        {"host": "sftp.example", "port": 22, "root_path": "/incoming"},
        {"username": "u", "password": "p"},
        protocol="sftp",
    )
    result = await adapter.test()

    assert result["ok"] is True
    assert result["host_key"] == "ssh-ed25519 AAAAOBSERVEDKEY"
    assert "latency_ms" in result
    assert sftp.stat_path == "/incoming"
    assert spy.calls == ["sftp.example"]


async def test_sftp_allowlisted_dials_hostname(monkeypatch):
    """An allowlisted host (resolve_pinned -> []) is dialed by name."""
    monkeypatch.setattr(sftp_mod, "resolve_pinned", _PinSpy(pinned=[]))
    sftp = _FakeSFTP()

    async def _connect(**kwargs):
        assert kwargs["host"] == "sftp-test"
        return _FakeSSHConn(sftp)

    monkeypatch.setattr(sftp_mod.asyncssh, "connect", _connect)
    adapter = SftpAdapter(
        {"host": "sftp-test"},
        {"username": "u", "password": "p"},
        allow_hosts=frozenset({"sftp-test"}),
    )
    result = await adapter.test()
    assert result["ok"] is True


async def test_sftp_test_failure(monkeypatch):
    monkeypatch.setattr(sftp_mod, "resolve_pinned", _PinSpy())

    async def _connect(**kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(sftp_mod.asyncssh, "connect", _connect)
    adapter = SftpAdapter({"host": "h"}, {"username": "u", "password": "p"})
    result = await adapter.test()
    assert result["ok"] is False
    assert "SFTP test failed" in result["message"]


async def test_sftp_egress_blocked(monkeypatch):
    monkeypatch.setattr(sftp_mod, "resolve_pinned", _PinSpy(blocked=True))

    async def _connect(**kwargs):  # pragma: no cover - must not run
        pytest.fail("connected despite egress block")

    monkeypatch.setattr(sftp_mod.asyncssh, "connect", _connect)
    adapter = SftpAdapter({"host": "internal"}, {"username": "u", "password": "p"})
    result = await adapter.test()
    assert result["ok"] is False
    assert "blocked" in result["message"]


# --------------------------------------------------------------------------- #
# FTP / FTPS
# --------------------------------------------------------------------------- #


class _FakeFtpClient:
    instances: ClassVar[list[_FakeFtpClient]] = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.connected = None
        self.logged_in = None
        self.listed = None
        self.quit_called = False
        self.upgraded = False
        self.egress = None
        _FakeFtpClient.instances.append(self)

    def configure_egress(self, control_host, host_allowlisted):
        self.egress = (control_host, host_allowlisted)

    async def connect(self, host, port):
        self.connected = (host, port)

    async def login(self, user="anonymous", password=""):
        self.logged_in = (user, password)

    async def upgrade_to_tls(self, sslcontext=None):
        self.upgraded = True

    async def list(self, path=""):
        self.listed = path
        return [(pathlib.PurePosixPath("a.txt"), {"type": "file"})]

    async def quit(self):
        self.quit_called = True

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _reset_ftp_instances():
    _FakeFtpClient.instances = []
    yield


async def test_ftp_test_ok(monkeypatch):
    spy = _PinSpy()
    monkeypatch.setattr(ftp_mod, "resolve_pinned", spy)
    monkeypatch.setattr(ftp_mod, "_EgressFtpClient", _FakeFtpClient)

    adapter = FtpAdapter(
        {"host": "ftp.example", "port": 21, "root_path": "/pub"},
        {"username": "u", "password": "p"},
    )
    result = await adapter.test()

    assert result["ok"] is True
    assert "latency_ms" in result
    client = _FakeFtpClient.instances[-1]
    # plain FTP dials the PINNED IP, not the hostname
    assert client.connected == (_PINNED_IP, 21)
    assert client.egress == (_PINNED_IP, False)
    assert client.logged_in == ("u", "p")
    assert client.listed == "/pub"
    assert client.quit_called
    assert spy.calls == ["ftp.example"]


async def test_ftp_test_failure(monkeypatch):
    monkeypatch.setattr(ftp_mod, "resolve_pinned", _PinSpy())

    class _BadClient(_FakeFtpClient):
        async def login(self, user="anonymous", password=""):
            raise aioftp.StatusCodeError("530", "530", "login failed")

    monkeypatch.setattr(ftp_mod, "_EgressFtpClient", _BadClient)
    adapter = FtpAdapter({"host": "h"}, {"username": "u", "password": "bad"})
    result = await adapter.test()
    assert result["ok"] is False
    assert "FTP test failed" in result["message"]


async def test_ftp_egress_blocked(monkeypatch):
    monkeypatch.setattr(ftp_mod, "resolve_pinned", _PinSpy(blocked=True))
    monkeypatch.setattr(
        ftp_mod, "_EgressFtpClient", lambda *a, **k: pytest.fail("client built despite block")
    )
    adapter = FtpAdapter({"host": "internal"}, {"username": "u", "password": "p"})
    result = await adapter.test()
    assert result["ok"] is False
    assert "blocked" in result["message"]


async def test_ftps_explicit_upgrades_tls(monkeypatch):
    # test() is inherited from FtpAdapter; it resolves via the ftp module.
    monkeypatch.setattr(ftp_mod, "resolve_pinned", _PinSpy())
    monkeypatch.setattr(ftps_mod, "_EgressFtpClient", _FakeFtpClient)

    adapter = FtpsAdapter(
        {"host": "ftps.example", "implicit": False},
        {"username": "u", "password": "p"},
    )
    result = await adapter.test()
    assert result["ok"] is True
    client = _FakeFtpClient.instances[-1]
    assert client.upgraded is True  # AUTH TLS happened
    # explicit mode: no ssl passed to the constructor
    assert client.kwargs.get("ssl") is None
    # FTPS dials the HOSTNAME (TLS cert validation), not the pinned IP
    assert client.connected == ("ftps.example", 21)


async def test_ftps_implicit_passes_ssl_context(monkeypatch):
    monkeypatch.setattr(ftp_mod, "resolve_pinned", _PinSpy())
    monkeypatch.setattr(ftps_mod, "_EgressFtpClient", _FakeFtpClient)

    adapter = FtpsAdapter(
        {"host": "ftps.example", "implicit": True},
        {"username": "u", "password": "p"},
    )
    result = await adapter.test()
    assert result["ok"] is True
    client = _FakeFtpClient.instances[-1]
    assert client.upgraded is False  # implicit: no explicit AUTH TLS
    assert client.kwargs.get("ssl") is not None


# --------------------------------------------------------------------------- #
# PASV/EPSV data-channel egress guard (the second SSRF hole)
# --------------------------------------------------------------------------- #


async def test_ftp_passive_data_channel_forced_to_control_host(monkeypatch):
    """A PASV/EPSV reply advertising an internal IP must NOT open a data
    connection there — it is forced to the validated control host."""
    opened: list[tuple[str, int]] = []

    async def _super_open(self, host, port):
        opened.append((host, port))
        return ("reader", "writer")

    monkeypatch.setattr(aioftp.Client, "_open_connection", _super_open)

    client = _EgressFtpClient()
    client.configure_egress(_PINNED_IP, host_allowlisted=False)
    client._stream = object()  # simulate an established control channel

    # server advertises the cloud metadata address for the data channel
    await client._open_connection("169.254.169.254", 50000)
    assert opened == [(_PINNED_IP, 50000)]  # forced to control host; port kept


async def test_ftp_passive_allowlisted_host_not_forced(monkeypatch):
    """An operator-allowlisted (compose-internal) host may use its advertised
    private data address."""
    opened: list[tuple[str, int]] = []

    async def _super_open(self, host, port):
        opened.append((host, port))
        return ("reader", "writer")

    monkeypatch.setattr(aioftp.Client, "_open_connection", _super_open)

    client = _EgressFtpClient()
    client.configure_egress("sftp-test", host_allowlisted=True)
    client._stream = object()
    await client._open_connection("10.0.0.9", 50000)
    assert opened == [("10.0.0.9", 50000)]  # allowlisted: advertised addr kept


async def test_ftp_control_connection_not_forced(monkeypatch):
    """The control connection itself (stream not yet established) passes through
    unchanged."""
    opened: list[tuple[str, int]] = []

    async def _super_open(self, host, port):
        opened.append((host, port))
        return ("reader", "writer")

    monkeypatch.setattr(aioftp.Client, "_open_connection", _super_open)

    client = _EgressFtpClient()
    client.configure_egress(_PINNED_IP, host_allowlisted=False)
    # _stream is None -> control connection
    await client._open_connection(_PINNED_IP, 21)
    assert opened == [(_PINNED_IP, 21)]


# --------------------------------------------------------------------------- #
# factory
# --------------------------------------------------------------------------- #


def test_adapter_for_maps_protocols():
    creds = {"username": "u", "password": "p"}
    assert isinstance(
        adapter_for(
            {"protocol": "s3", "config": {"bucket": "b"}},
            {"access_key_id": "x", "secret_access_key": "y"},
        ),
        S3Adapter,
    )
    assert isinstance(adapter_for({"protocol": "ftp", "config": {"host": "h"}}, creds), FtpAdapter)
    assert isinstance(
        adapter_for({"protocol": "ftps", "config": {"host": "h"}}, creds), FtpsAdapter
    )

    ssh = adapter_for({"protocol": "ssh", "config": {"host": "h"}}, creds)
    assert isinstance(ssh, SftpAdapter) and ssh.protocol == "ssh"
    sftp = adapter_for({"protocol": "sftp", "config": {"host": "h"}}, creds)
    assert isinstance(sftp, SftpAdapter) and sftp.protocol == "sftp"


def test_adapter_for_stac_api_reserved():
    with pytest.raises(NotImplementedError, match="reserved for a future release"):
        adapter_for({"protocol": "stac-api", "config": {}}, {})


def test_adapter_for_unknown_protocol():
    with pytest.raises(ValueError, match="unknown connection protocol"):
        adapter_for({"protocol": "gopher", "config": {}}, {})
