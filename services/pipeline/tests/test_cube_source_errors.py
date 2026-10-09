"""Source errors: file-specific (a row outcome) vs transport (the job retries).

The lead's rule on PR #101: a NODD outage must not leave permanent gaps, so
an error in transit fails the job instead of the row. The second half proves
what the real obstore path raises, against a local S3-style server.
"""

from __future__ import annotations

import asyncio
import socket

import pytest
from obspec_utils.registry import ObjectStoreRegistry
from obstore.exceptions import (
    GenericError,
    InvalidPathError,
    NotFoundError,
    PermissionDeniedError,
    UnauthenticatedError,
)

from _cube_sources import GOES_CONFIG, scan, write_goes_file
from _fake_nodd import BUCKET, FakeNodd, closed_port, short_retry_store
from pipeline.connections import egress
from pipeline.connections.egress import EgressBlocked, resolve_pinned
from pipeline.cubes.config import parse_cube_sink_config
from pipeline.cubes.resolve import SourceUnavailable, is_transport_error
from pipeline.cubes.source import SourceConnectionError
from pipeline.cubes.steps import LayoutError, parse_header

CONFIG = parse_cube_sink_config(dict(GOES_CONFIG))


def chained(outer: BaseException, cause: BaseException, *, implicit: bool = False):
    """``outer`` raised from (or, implicitly, while handling) ``cause``."""
    try:
        try:
            raise cause
        except BaseException as inner:
            if implicit:
                raise outer  # noqa: B904 - the implicit __context__ is the point
            raise outer from inner
    except BaseException as exc:
        return exc


def dns_failure() -> EgressBlocked:
    """What ``resolve_pinned`` raises when DNS itself fails."""
    return chained(
        EgressBlocked("egress to s3.us-east-1.amazonaws.com is blocked: DNS resolution failed"),
        socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution"),
    )


@pytest.mark.parametrize(
    "exc",
    [
        GenericError("Generic S3 error: Server returned non-2xx status code: 503"),
        TimeoutError("timed out"),
        ConnectionResetError("connection reset by peer"),
        ConnectionRefusedError("connection refused"),
        socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution"),
    ],
    ids=["obstore-generic", "timeout", "reset", "refused", "gaierror"],
)
def test_transport_errors(exc):
    assert is_transport_error(exc)


@pytest.mark.parametrize(
    "exc",
    [
        FileNotFoundError("Object at location x.nc not found"),
        NotFoundError("not found"),
        PermissionDeniedError("denied"),
        UnauthenticatedError("unauthenticated"),
        InvalidPathError("bad path"),
        OSError("Unable to synchronously open file (file signature not found)"),
        ValueError("corrupt header"),
        LayoutError("DQF is missing"),
        SourceConnectionError("signed_source_unsupported"),
        EgressBlocked("egress to meta.internal is blocked (resolves to a non-public address)"),
        SourceUnavailable("skipped", "no_source_connection"),
    ],
    ids=[
        "file-not-found", "obstore-not-found", "permission", "unauthenticated", "invalid-path",
        "corrupt-header", "value-error", "layout", "signed-source", "egress-block", "unavailable",
    ],
)
def test_file_specific_errors(exc):
    assert not is_transport_error(exc)


def test_the_whole_chain_is_walked():
    # h5py / obspec-utils may wrap the obstore error; explicit and implicit chains
    wrapped = chained(OSError("Unable to read header"), GenericError("503 Slow Down"))
    assert is_transport_error(wrapped)
    implicit = chained(ValueError("bad buffer"), TimeoutError("timed out"), implicit=True)
    assert is_transport_error(implicit)
    deep = chained(RuntimeError("parse failed"), wrapped)
    assert is_transport_error(deep)


def test_the_outermost_decisive_link_wins():
    # A not-found or a denial wrapping a transport error is still about the file.
    assert not is_transport_error(chained(FileNotFoundError("gone"), GenericError("x")))
    assert not is_transport_error(chained(PermissionDeniedError("no"), TimeoutError("x")))


def test_a_cycle_in_the_chain_terminates():
    a, b = OSError("a"), ValueError("b")
    a.__cause__, b.__cause__ = b, a
    assert not is_transport_error(a)


def test_egress_blocked_by_dns_failure_is_transport_but_a_real_block_is_not():
    assert is_transport_error(dns_failure())
    unavailable = chained(SourceUnavailable("failed", "EgressBlocked: x"), dns_failure())
    assert is_transport_error(unavailable)
    genuine = chained(SourceUnavailable("failed", "EgressBlocked: x"), EgressBlocked("private"))
    assert not is_transport_error(genuine)


def test_resolve_pinned_turns_a_dns_failure_into_a_transport_error(monkeypatch):
    def no_dns(*args, **kwargs):
        raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")

    monkeypatch.setattr(egress.socket, "getaddrinfo", no_dns)
    with pytest.raises(EgressBlocked) as dns:
        resolve_pinned("s3.us-east-1.amazonaws.com")
    assert is_transport_error(dns.value)
    with pytest.raises(EgressBlocked) as private:
        resolve_pinned("10.0.0.1")
    assert not is_transport_error(private.value)


# ---- what the real obstore path raises (local S3-style server) ----------


@pytest.fixture
def nodd(tmp_path):
    server = FakeNodd(tmp_path / "bucket")
    write_goes_file(server.root / "ok.nc", when=scan(0))
    yield server
    server.close()


async def _head(store, key: str):
    import obstore

    return await obstore.head_async(store, key)


def _parse(store, key: str):
    registry = ObjectStoreRegistry({f"s3://{BUCKET}": store})
    return parse_header(f"s3://{BUCKET}/{key}", registry, CONFIG)


async def _both(store, key: str) -> tuple[BaseException, BaseException]:
    with pytest.raises(Exception) as head:
        await _head(store, key)
    with pytest.raises(Exception) as parse:
        await asyncio.to_thread(_parse, store, key)
    return head.value, parse.value


async def test_a_missing_s3_object_raises_the_builtin_file_not_found(nodd):
    store = nodd.libs().store
    head, parse = await _both(store, "missing.nc")
    # obstore 0.11 maps object_store's NotFound to the BUILTIN FileNotFoundError
    # (its own NotFoundError is not a FileNotFoundError subclass, and is not raised)
    assert type(head) is FileNotFoundError and type(parse) is FileNotFoundError
    assert not is_transport_error(head) and not is_transport_error(parse)


async def test_a_503_raises_a_generic_error_after_obstore_retries(nodd):
    nodd.faults["ok.nc"] = 503
    head, parse = await _both(nodd.libs().store, "ok.nc")
    assert type(head) is GenericError and type(parse) is GenericError
    assert "503" in str(head) and "after 1 retries" in str(head)
    assert is_transport_error(head) and is_transport_error(parse)
    assert nodd.hits["ok.nc"] >= 4  # every request was retried once


async def test_a_503_on_the_ranged_get_after_a_good_head(nodd):
    store = nodd.libs().store
    assert (await _head(store, "ok.nc"))["size"] > 0
    nodd.down = True
    with pytest.raises(GenericError) as exc:
        await asyncio.to_thread(_parse, store, "ok.nc")
    assert is_transport_error(exc.value)


async def test_a_refused_connection_raises_a_generic_error():
    store = short_retry_store(f"http://127.0.0.1:{closed_port()}")
    head, parse = await _both(store, "ok.nc")
    assert type(head) is GenericError and type(parse) is GenericError
    assert is_transport_error(head) and is_transport_error(parse)


async def test_a_403_raises_permission_denied(nodd):
    nodd.faults["ok.nc"] = 403
    head, parse = await _both(nodd.libs().store, "ok.nc")
    assert type(head) is PermissionDeniedError and type(parse) is PermissionDeniedError
    assert not is_transport_error(head) and not is_transport_error(parse)


async def test_a_healthy_object_heads_and_parses(nodd):
    store = nodd.libs().store
    assert (await _head(store, "ok.nc"))["last_modified"] is not None
    assert await asyncio.to_thread(_parse, store, "ok.nc") is not None
