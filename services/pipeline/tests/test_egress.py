"""Egress policy: block private/loopback/metadata; allowlist escape hatch."""

import socket

import pytest

from pipeline.connections.egress import (
    EgressBlocked,
    assert_ip_allowed,
    enforce,
    is_blocked_address,
    resolve_pinned,
)


def _fake_getaddrinfo(ip: str, family: int = socket.AF_INET):
    def _inner(host, port, *args, **kwargs):
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))]

    return _inner


def test_public_host_allowed(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    enforce("example.com")  # no raise


def test_private_host_blocked(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("10.0.0.5"))
    with pytest.raises(EgressBlocked):
        enforce("internal.corp")


def test_loopback_blocked(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("127.0.0.1"))
    with pytest.raises(EgressBlocked):
        enforce("localhost.evil")


def test_metadata_ip_blocked(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("169.254.169.254"))
    with pytest.raises(EgressBlocked):
        enforce("metadata.example")


def test_allowlisted_private_host_permitted(monkeypatch):
    # even resolving to a private IP, an allowlisted host is permitted and DNS
    # is not even consulted.
    def _boom(*a, **k):  # pragma: no cover - must not be called
        raise AssertionError("getaddrinfo should be skipped for allowlisted hosts")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    enforce("sftp-test", allow_hosts={"sftp-test"})


def test_allowlist_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("10.1.2.3"))
    enforce("MinIO", allow_hosts={"minio"})


def test_ipv6_loopback_blocked(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("::1", family=socket.AF_INET6))
    with pytest.raises(EgressBlocked):
        enforce("v6-loopback.example")


def test_ipv6_unique_local_blocked(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("fc00::1", family=socket.AF_INET6))
    with pytest.raises(EgressBlocked):
        enforce("v6-ula.example")


def test_ipv4_mapped_private_blocked(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_getaddrinfo("::ffff:10.0.0.1", family=socket.AF_INET6),
    )
    with pytest.raises(EgressBlocked):
        enforce("mapped.example")


def test_bare_ip_literal_public_allowed(monkeypatch):
    def _boom(*a, **k):  # pragma: no cover - literal short-circuits DNS
        raise AssertionError("getaddrinfo should not run for an IP literal")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    enforce("8.8.8.8")


def test_bare_ip_literal_private_blocked(monkeypatch):
    def _boom(*a, **k):  # pragma: no cover
        raise AssertionError("getaddrinfo should not run for an IP literal")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    with pytest.raises(EgressBlocked):
        enforce("192.168.1.1")


def test_dns_failure_blocks(monkeypatch):
    def _fail(*a, **k):
        raise socket.gaierror("nxdomain")

    monkeypatch.setattr(socket, "getaddrinfo", _fail)
    with pytest.raises(EgressBlocked):
        enforce("does-not-resolve.example")


def test_is_blocked_address_direct():
    assert is_blocked_address("169.254.169.254")
    assert is_blocked_address("10.0.0.1")
    assert is_blocked_address("::1")
    assert not is_blocked_address("1.1.1.1")


# --------------------------------------------------------------------------- #
# resolve_pinned — the IP-pinning resolver (DNS-rebinding defence)
# --------------------------------------------------------------------------- #


def test_resolve_pinned_returns_validated_ips(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))
    assert resolve_pinned("example.com") == ["93.184.216.34"]


def test_resolve_pinned_allowlisted_returns_empty(monkeypatch):
    def _boom(*a, **k):  # pragma: no cover - DNS skipped for allowlisted hosts
        raise AssertionError("getaddrinfo should be skipped for allowlisted hosts")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    # sentinel [] means "connect by hostname, operator-vouched"
    assert resolve_pinned("sftp-test", allow_hosts={"sftp-test"}) == []


def test_resolve_pinned_blocks_when_any_address_private(monkeypatch):
    def _multi(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("169.254.169.254", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", _multi)
    with pytest.raises(EgressBlocked):
        resolve_pinned("rebind.example")  # fail closed if ANY address is bad


def test_resolve_pinned_ip_literal(monkeypatch):
    def _boom(*a, **k):  # pragma: no cover - literal short-circuits DNS
        raise AssertionError("getaddrinfo should not run for an IP literal")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    assert resolve_pinned("8.8.8.8") == ["8.8.8.8"]
    with pytest.raises(EgressBlocked):
        resolve_pinned("192.168.1.1")


# --------------------------------------------------------------------------- #
# assert_ip_allowed — guard for a server-advertised (e.g. PASV) data address
# --------------------------------------------------------------------------- #


def test_assert_ip_allowed_blocks_private_when_not_allowlisted():
    with pytest.raises(EgressBlocked):
        assert_ip_allowed("169.254.169.254", host_allowlisted=False)


def test_assert_ip_allowed_permits_public():
    assert_ip_allowed("1.1.1.1", host_allowlisted=False)  # no raise


def test_assert_ip_allowed_bypassed_for_allowlisted_host():
    # an allowlisted host's data channel may live on a private address
    assert_ip_allowed("10.0.0.9", host_allowlisted=True)  # no raise
