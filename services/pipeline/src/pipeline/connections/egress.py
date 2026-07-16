"""Outbound egress policy for connection adapters (ROADMAP §5.2 SSRF guard).

Every adapter resolves its target host through :func:`enforce` BEFORE opening a
socket. A host is BLOCKED when any of its resolved addresses is loopback,
private, link-local, unique-local, multicast, reserved, unspecified, or the
cloud metadata address (169.254.169.254 / its IPv6 forms) — including
IPv4-mapped IPv6 forms. The only escape hatch is an explicit allowlist
(``EGRESS_ALLOW_HOSTS``), used for the compose-internal test servers.

This mirrors the app's ``safeFetch`` posture: deny-by-default for anything that
resolves inside the trust boundary, allow public destinations and named
exceptions.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable

# The cloud metadata endpoints. 169.254.169.254 is already link-local (and thus
# blocked), but we name it explicitly so the block reason is unambiguous.
_METADATA_IPS = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)


class EgressBlocked(Exception):
    """Raised when a target host is not permitted by the egress policy."""


def _mapped_to_v4(addr: ipaddress._BaseAddress) -> ipaddress._BaseAddress:
    """Collapse an IPv4-mapped/compatible IPv6 address to its IPv4 form."""
    if isinstance(addr, ipaddress.IPv6Address):
        mapped = addr.ipv4_mapped
        if mapped is not None:
            return mapped
        # ::ffff:a.b.c.c already covered above; also handle 6to4/sixtofour.
        if addr.sixtofour is not None:
            return addr.sixtofour
    return addr


def is_blocked_address(ip: str) -> bool:
    """True when ``ip`` falls in any disallowed range (used after resolution)."""
    addr = ipaddress.ip_address(ip)
    addr = _mapped_to_v4(addr)
    if addr in _METADATA_IPS:
        return True
    return (
        addr.is_loopback
        or addr.is_private  # covers RFC1918, unique-local fc00::/7, etc.
        or addr.is_link_local  # 169.254/16, fe80::/10
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def resolve_pinned(host: str, allow_hosts: Iterable[str] = ()) -> list[str]:
    """Resolve + validate ``host`` ONCE and return the IP(s) to connect to.

    This is the single resolution codepath that defeats the DNS-rebinding TOCTOU
    hole: adapters resolve/validate here and then dial the returned IP literal,
    instead of handing the hostname to a client library that would independently
    re-resolve it at socket time (a low-TTL rebind between check and connect
    could otherwise reach an internal/metadata IP).

    Return value:

    - **Allowlisted host** (operator-vouched, e.g. compose-internal
      ``sftp-test`` that legitimately resolves to a private IP) → ``[]``, a
      sentinel meaning "connect by hostname, do not pin". DNS is not consulted.
    - **IP literal** → range-checked; returns ``[host]`` or raises
      :class:`EgressBlocked`.
    - **Hostname** → ``getaddrinfo``; if ANY resolved address is blocked, raise
      :class:`EgressBlocked` (fail closed). Otherwise return the validated IP
      strings, deduped with resolution order preserved.
    """
    allow = {h.lower() for h in allow_hosts}
    if host.lower() in allow:
        return []

    # A bare IP literal short-circuits DNS but still gets range-checked.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if is_blocked_address(str(literal)):
            raise EgressBlocked(
                f"egress to {host} is blocked (resolves to a non-public address) "
                f"and it is not in EGRESS_ALLOW_HOSTS"
            )
        return [host]

    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise EgressBlocked(f"egress to {host} is blocked: DNS resolution failed") from exc

    if not infos:
        raise EgressBlocked(f"egress to {host} is blocked: no addresses resolved")

    pinned: list[str] = []
    for info in infos:
        ip = info[4][0]
        if is_blocked_address(ip):
            raise EgressBlocked(
                f"egress to {host} is blocked: it resolves to a non-public "
                f"address, and it is not in EGRESS_ALLOW_HOSTS"
            )
        if ip not in pinned:
            pinned.append(ip)
    return pinned


def enforce(host: str, allow_hosts: Iterable[str] = ()) -> None:
    """Resolve ``host`` and raise :class:`EgressBlocked` if it is not permitted.

    Thin wrapper over :func:`resolve_pinned` so there is exactly one resolution
    codepath. Callers that can pin the validated IP (SFTP/FTP/FTPS/S3) call
    :func:`resolve_pinned` directly and dial the returned address; this shim
    stays for callers that only need the allow/deny decision.
    """
    resolve_pinned(host, allow_hosts)


def assert_ip_allowed(ip: str, host_allowlisted: bool) -> None:
    """Guard a server-advertised address (e.g. an FTP PASV/EPSV data IP).

    Raises :class:`EgressBlocked` when the host was NOT operator-allowlisted and
    ``ip`` falls in a blocked range. An allowlisted host bypasses the check — its
    data channel may legitimately live on a private compose-internal address.
    """
    if not host_allowlisted and is_blocked_address(ip):
        raise EgressBlocked(
            f"egress to server-advertised data address {ip} is blocked "
            f"(non-public) and the host is not in EGRESS_ALLOW_HOSTS"
        )
