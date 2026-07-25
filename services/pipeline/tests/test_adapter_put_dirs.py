"""B-iii live-run fallout (ISSUES I-45): delivery path templates are
directory-shaped, so SFTP/FTP `put` must create missing parent directories —
the live runs 553'd / "No such file"d without this. Also the dedicated
concrete `move()` body tests I-45 called for (SFTP `posix_rename`, FTP
`rename`)."""

import pytest

from pipeline.connections.adapters.ftp import FtpAdapter
from pipeline.connections.adapters.sftp import SftpAdapter

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# sftp
# --------------------------------------------------------------------------- #


class _FakeFile:
    def __init__(self, log):
        self._log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def write(self, data):
        self._log.append(("write", data))


class _FakeSFTP:
    def __init__(self):
        self.log = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def makedirs(self, path, exist_ok=False):
        self.log.append(("makedirs", path, exist_ok))

    def open(self, path, mode):
        self.log.append(("open", path, mode))
        return _FakeFile(self.log)

    async def posix_rename(self, src, dst):
        self.log.append(("posix_rename", src, dst))


class _FakeSSHConn:
    def __init__(self, sftp):
        self._sftp = sftp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def start_sftp_client(self):
        return self._sftp


def _sftp_adapter(fake):
    adapter = SftpAdapter(
        {"host": "sftp-test", "port": 22, "root_path": "/upload"},
        {"username": "demo", "password": "demo"},
    )

    async def _connect():
        return _FakeSSHConn(fake)

    adapter._connect = _connect
    return adapter


async def test_sftp_put_creates_parent_directories():
    fake = _FakeSFTP()
    await _sftp_adapter(fake).put("b3/item-1/a.tif", b"DATA")
    assert fake.log == [
        ("makedirs", "/upload/b3/item-1", True),
        ("open", "/upload/b3/item-1/a.tif", "wb"),
        ("write", b"DATA"),
    ]


async def test_sftp_put_at_root_skips_makedirs():
    fake = _FakeSFTP()
    adapter = SftpAdapter(
        {"host": "sftp-test", "root_path": "/"}, {"username": "d", "password": "d"}
    )

    async def _connect():
        return _FakeSSHConn(fake)

    adapter._connect = _connect
    await adapter.put("a.tif", b"X")
    assert fake.log[0] == ("open", "/a.tif", "wb")


async def test_sftp_move_uses_posix_rename_under_root():
    fake = _FakeSFTP()
    await _sftp_adapter(fake).move("b3/a.tif.part", "b3/a.tif")
    assert fake.log == [("posix_rename", "/upload/b3/a.tif.part", "/upload/b3/a.tif")]


# --------------------------------------------------------------------------- #
# ftp
# --------------------------------------------------------------------------- #


class _FakeStream:
    def __init__(self, log):
        self._log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def write(self, data):
        self._log.append(("write", data))


class _FakeFtpClient:
    def __init__(self, mkdir_error=None):
        self.log = []
        self.mkdir_error = mkdir_error

    async def make_directory(self, path):
        if self.mkdir_error:
            self.log.append(("make_directory_failed", path))
            raise self.mkdir_error
        self.log.append(("make_directory", path))

    def upload_stream(self, path):
        self.log.append(("upload", path))
        return _FakeStream(self.log)

    async def rename(self, src, dst):
        self.log.append(("rename", src, dst))

    async def quit(self):
        self.log.append(("quit",))


def _ftp_adapter(fake):
    adapter = FtpAdapter(
        {"host": "ftp-test", "port": 21, "root_path": "/"},
        {"username": "demo", "password": "demo"},
    )

    async def _connect_client():
        return fake

    adapter._connect_client = _connect_client
    return adapter


async def test_ftp_put_creates_parent_directory():
    fake = _FakeFtpClient()
    await _ftp_adapter(fake).put("b3/item-1/a.tif", b"DATA")
    assert fake.log == [
        ("make_directory", "/b3/item-1"),
        ("upload", "/b3/item-1/a.tif"),
        ("write", b"DATA"),
        ("quit",),
    ]


async def test_ftp_put_tolerates_mkdir_failure_on_existing_dir():
    fake = _FakeFtpClient(mkdir_error=OSError("550 exists"))
    await _ftp_adapter(fake).put("b3/a.tif", b"X")
    assert ("upload", "/b3/a.tif") in fake.log  # upload proceeds regardless


async def test_ftp_move_uses_rename():
    fake = _FakeFtpClient()
    await _ftp_adapter(fake).move("b3/a.tif.part", "b3/a.tif")
    assert fake.log == [("rename", "/b3/a.tif.part", "/b3/a.tif"), ("quit",)]
