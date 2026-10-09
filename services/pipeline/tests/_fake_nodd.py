"""A local S3-style HTTP server: the fake NODD bucket for source-error tests.

It serves the files under ``root`` path style (``/{bucket}/{key}``), with
``HEAD``, ``GET`` and ``Range`` the way obstore's ``S3Store`` reads a header,
and S3's error shapes on demand: ``faults[key] = 404 | 403 | 503``, or
``down = True`` to answer 503 to everything (a NODD outage). ``libs()`` is a
``SourceLibs`` for both cube libraries, like ``cubes.source.libs_from_connection``
builds for a real connection, but with a SHORT obstore retry budget: obstore
retries 5xx and connection errors for up to 3 minutes by default.

``close()`` collects garbage first: an HDF5 open that failed leaves an h5py
file object whose finalizer HEADs the object (obspec-utils' reader), and it
must not fire later, against a closed server, in the middle of another test.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import gc
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import icechunk as ic
from obstore.store import S3Store

from pipeline.cubes.source import SourceLibs

BUCKET = "nodd"
_ERRORS = {
    403: ("AccessDenied", "Access Denied"),
    404: ("NoSuchKey", "The specified key does not exist."),
    503: ("SlowDown", "Please reduce your request rate."),
}


def short_retry_store(endpoint: str, bucket: str = BUCKET) -> S3Store:
    """An anonymous path-style S3Store that retries once, quickly. The
    timeouts are generous so a loaded test machine doesn't turn a slow local
    answer into a transport error."""
    return S3Store(
        bucket=bucket,
        region="us-east-1",
        endpoint=endpoint,
        virtual_hosted_style_request=False,
        skip_signature=True,
        client_options={"allow_http": True, "timeout": dt.timedelta(seconds=10)},
        retry_config={
            "max_retries": 1,
            "retry_timeout": dt.timedelta(seconds=10),
            "backoff": {
                "init_backoff": dt.timedelta(milliseconds=5),
                "max_backoff": dt.timedelta(milliseconds=20),
                "base": 2,
            },
        },
    )


def closed_port() -> int:
    """A local port nothing listens on: connections to it are refused."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeNodd:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.faults: dict[str, int] = {}
        self.down = False
        #: key -> requests seen (HEAD and GET)
        self.hits: dict[str, int] = {}
        nodd = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # keep test output quiet
                pass

            def do_HEAD(self) -> None:
                nodd._answer(self, body=False)

            def do_GET(self) -> None:
                nodd._answer(self, body=True)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def close(self) -> None:
        gc.collect()
        self._server.shutdown()
        self._server.server_close()

    def libs(self) -> SourceLibs:
        return SourceLibs(
            prefix=f"s3://{BUCKET}/",
            registry_key=f"s3://{BUCKET}",
            store=short_retry_store(self.endpoint),
            container_store=ic.s3_store(
                region="us-east-1",
                endpoint_url=self.endpoint,
                allow_http=True,
                anonymous=True,
                force_path_style=True,
            ),
            credentials=ic.s3_credentials(anonymous=True),
        )

    def _answer(self, req: BaseHTTPRequestHandler, *, body: bool) -> None:
        path = req.path.split("?", 1)[0]
        _, bucket, key = path.split("/", 2)
        self.hits[key] = self.hits.get(key, 0) + 1
        status = 503 if self.down else self.faults.get(key)
        # Keys never leave the fixture root: "../" in a request is a 404.
        root = os.path.realpath(self.root)
        target = os.path.realpath(os.path.join(root, key))
        if status is None and (
            bucket != BUCKET
            or not target.startswith(root + os.sep)
            or not os.path.isfile(target)
        ):
            status = 404
        if status is not None:
            code, message = _ERRORS[status]
            xml = (
                "<?xml version='1.0' encoding='UTF-8'?>"
                f"<Error><Code>{code}</Code><Message>{message}</Message></Error>"
            ).encode()
            req.send_response(status)
            req.send_header("Content-Type", "application/xml")
            req.send_header("Content-Length", str(len(xml) if body else 0))
            req.end_headers()
            if body:
                req.wfile.write(xml)
            return
        with open(target, "rb") as f:
            data = f.read()
        stat = os.stat(target)
        span = req.headers.get("Range") if body else None
        if span:
            first, last = span.split("=", 1)[1].split("-", 1)
            start = int(first)
            end = min(int(last) if last else len(data) - 1, len(data) - 1)
            chunk = data[start : end + 1]
            req.send_response(206)
            req.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        else:
            chunk = data
            req.send_response(200)
        req.send_header("Content-Length", str(len(chunk) if body else len(data)))
        req.send_header("Last-Modified", email.utils.formatdate(stat.st_mtime, usegmt=True))
        req.send_header("ETag", f'"{int(stat.st_mtime)}-{len(data)}"')
        req.end_headers()
        if body:
            req.wfile.write(chunk)
