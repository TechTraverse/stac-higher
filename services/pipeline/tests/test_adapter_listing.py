"""Adapter ``list()`` returns FileEntry with size/mtime/etag (mocked clients).

The DISCOVER stage's settled-check depends on this metadata, so each adapter is
verified to surface it from its native listing without touching real servers.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import stat

from pipeline.connections.adapters import FileEntry, FtpAdapter, S3Adapter, SftpAdapter
from pipeline.connections.adapters import ftp as ftp_mod
from pipeline.connections.adapters import s3 as s3_mod

_PINNED_IP = "203.0.113.10"


class _PinSpy:
    def __init__(self, pinned=(_PINNED_IP,)):
        self.calls: list[str] = []
        self._pinned = list(pinned)

    def __call__(self, host, allow_hosts=()):
        self.calls.append(host)
        return list(self._pinned)


# --------------------------------------------------------------------------- #
# S3
# --------------------------------------------------------------------------- #


class _FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kwargs):
        self.kwargs = kwargs
        yield from self._pages


class _FakeS3Client:
    def __init__(self, pages):
        self._pages = pages

    def get_paginator(self, operation_name):
        assert operation_name == "list_objects_v2"
        return _FakePaginator(self._pages)


async def test_s3_list_returns_file_entries(monkeypatch):
    monkeypatch.setattr(s3_mod, "resolve_pinned", _PinSpy())
    lm = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.UTC)
    pages = [
        {
            "Contents": [
                {"Key": "a.tif", "Size": 123, "LastModified": lm, "ETag": '"abc123"'},
                {"Key": "folder/", "Size": 0, "LastModified": lm, "ETag": '"d41d"'},
            ]
        }
    ]
    monkeypatch.setattr(s3_mod.boto3, "client", lambda *a, **k: _FakeS3Client(pages))

    adapter = S3Adapter(
        {"bucket": "b", "endpoint": "http://minio:9000", "force_path_style": True},
        {"access_key_id": "x", "secret_access_key": "y"},
    )
    entries = await adapter.list("prefix/")

    assert entries[0] == FileEntry(
        path="a.tif", size=123, mtime=lm.timestamp(), etag="abc123", is_dir=False
    )
    # trailing-slash keys are treated as directories; etag quotes are stripped
    assert entries[1].is_dir is True
    assert entries[1].etag == "d41d"


# --------------------------------------------------------------------------- #
# SFTP
# --------------------------------------------------------------------------- #


class _FakeAttrs:
    def __init__(self, size=None, mtime=None, permissions=None):
        self.size = size
        self.mtime = mtime
        self.permissions = permissions


class _FakeName:
    def __init__(self, filename, attrs):
        self.filename = filename
        self.attrs = attrs


class _FakeSftp:
    def __init__(self, names):
        self._names = names

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def readdir(self, path):
        self.readdir_path = path
        return self._names


class _FakeConn:
    def __init__(self, sftp):
        self._sftp = sftp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def start_sftp_client(self):
        return self._sftp


async def test_sftp_list_returns_entries_skipping_dot_entries(monkeypatch):
    names = [
        _FakeName(".", _FakeAttrs(permissions=stat.S_IFDIR | 0o755)),
        _FakeName("..", _FakeAttrs(permissions=stat.S_IFDIR | 0o755)),
        _FakeName(
            "scene.tif",
            _FakeAttrs(size=456, mtime=1_700_000_000, permissions=stat.S_IFREG | 0o644),
        ),
        _FakeName("sub", _FakeAttrs(permissions=stat.S_IFDIR | 0o755)),
    ]
    adapter = SftpAdapter(
        {"host": "h", "port": 22, "root_path": "/data"},
        {"username": "u", "password": "p"},
    )

    async def _fake_connect():
        return _FakeConn(_FakeSftp(names))

    monkeypatch.setattr(adapter, "_connect", _fake_connect)
    entries = await adapter.list("in")

    # "." and ".." are dropped
    assert [e.path for e in entries] == ["scene.tif", "sub"]
    assert entries[0] == FileEntry(path="scene.tif", size=456, mtime=1_700_000_000.0, is_dir=False)
    assert entries[1].is_dir is True


# --------------------------------------------------------------------------- #
# FTP
# --------------------------------------------------------------------------- #


class _FakeFtp:
    async def list(self, target):
        self.listed = target
        return [
            (
                pathlib.PurePosixPath("a.tif"),
                {"type": "file", "size": "123", "modify": "20260102030405"},
            ),
            (pathlib.PurePosixPath("sub"), {"type": "dir"}),
        ]

    async def quit(self):
        self.quit_called = True

    def close(self):
        pass


async def test_ftp_list_parses_size_and_modify(monkeypatch):
    monkeypatch.setattr(ftp_mod, "resolve_pinned", _PinSpy())
    adapter = FtpAdapter({"host": "h", "root_path": "/pub"}, {"username": "u", "password": "p"})
    fake = _FakeFtp()

    async def _fake_connect_client():
        return fake

    monkeypatch.setattr(adapter, "_connect_client", _fake_connect_client)
    entries = await adapter.list("in")

    expected_mtime = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.UTC).timestamp()
    assert entries[0] == FileEntry(path="a.tif", size=123, mtime=expected_mtime, is_dir=False)
    assert entries[1] == FileEntry(path="sub", size=None, mtime=None, is_dir=True)
    assert fake.quit_called is True
