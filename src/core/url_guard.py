"""SSRF guard for server-side fetches of user-supplied URLs.

Used by endpoints that fetch a URL the caller controls (e.g. connecting a
theme dev server). Without this, a store-owner token can point the URL at
cloud metadata (``169.254.169.254``), loopback, or an internal service and
have the API fetch it and echo the response — a classic SSRF.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    """Raised when a URL is not safe to fetch from the server."""


def _blocked(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_private: bool
) -> bool:
    # IPv4-mapped IPv6 (e.g. ::ffff:169.254.169.254) — evaluate the inner v4.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _blocked(ip.ipv4_mapped, allow_private=allow_private)
    # ALWAYS blocked, before any allowance: link-local covers the cloud
    # metadata endpoint (169.254.169.254 / fe80::) — note Python folds
    # link-local into is_private, so this MUST be checked first — plus
    # multicast/unspecified which are never a legitimate fetch target.
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return True
    # Loopback (127.0.0.0/8, ::1) + RFC1918/private: permitted only outside
    # production, so the local dev-server workflow keeps working in dev while
    # prod can reach public hosts only (a tunnelled dev URL, not internals).
    if ip.is_loopback or ip.is_private:
        return not allow_private
    # Otherwise-reserved IETF ranges: block everywhere.
    return ip.is_reserved


def assert_public_http_url(url: str, *, allow_private: bool | None = None) -> None:
    """Validate ``url`` is an http(s) URL whose host resolves only to allowed IPs.

    Raises :class:`UnsafeUrlError` otherwise. ``allow_private`` defaults to
    True outside production (``ENVIRONMENT != "production"``) so local dev
    against ``localhost``/LAN keeps working; link-local (IMDS) is blocked even
    then.

    Note: this validates at call time. httpx re-resolves on connect, so a
    DNS-rebind TOCTOU remains theoretically possible; keep redirects disabled
    on the client and treat this as defense-in-depth on an already
    authenticated, rate-limited endpoint.
    """
    if allow_private is None:
        allow_private = os.getenv("ENVIRONMENT", "development") != "production"

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError("URL must use http or https.")
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("URL has no host.")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"Host does not resolve: {host}") from exc
    if not infos:
        raise UnsafeUrlError(f"Host does not resolve: {host}")

    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError as exc:
            raise UnsafeUrlError(
                f"Host resolved to an invalid address: {ip_str}"
            ) from exc
        if _blocked(ip, allow_private=allow_private):
            raise UnsafeUrlError(
                f"Refusing to fetch a non-public address ({ip_str}) for host '{host}'."
            )
