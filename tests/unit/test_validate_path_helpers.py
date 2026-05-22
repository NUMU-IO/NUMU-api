"""Unit tests for the SSRF guardrail helpers in storefront_validation.

The HTTP path is exercised via integration tests; the security-critical
helpers below get focused unit coverage so SSRF + path-injection
regressions are caught regardless of network conditions.

Covers SEC-002 (SSRF guardrails) and SEC-005 (negative cases on path
input).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.api.v1.routes.stores.storefront_validation import (
    _path_looks_external,
    _resolved_ip_is_private,
    _same_host,
)

# ── _path_looks_external (SEC-005) ──────────────────────────────────


@pytest.mark.parametrize(
    "path,expected_external",
    [
        # Valid in-origin paths
        ("/", False),
        ("/products", False),
        ("/product/abc-123", False),
        ("/collections?category=summer", False),
        ("/lookbook/eid-2026", False),
        ("/path-with-trailing-slash/", False),
        # Scheme injection
        ("http://internal.local/admin", True),
        ("https://evil.com/payload", True),
        ("HTTP://EVIL.COM/", True),  # case-insensitive scheme detection
        # Protocol-relative URL → reject
        ("//evil.com/path", True),
        # Backslash URL (Windows-style) → reject
        ("\\\\evil.com\\path", True),
        # Relative paths without leading slash → reject (we only accept
        # absolute paths)
        ("products", True),
        ("./path", True),
        ("../escape", True),
    ],
)
def test_path_looks_external(path, expected_external):
    assert _path_looks_external(path) is expected_external


# ── _resolved_ip_is_private (SEC-002) ───────────────────────────────


def _mock_addrinfo(ip: str):
    """Return a single-entry getaddrinfo-style result.

    socket.getaddrinfo returns tuples of (family, type, proto, canon, sockaddr)
    where sockaddr is at minimum (ip, port) for IPv4 or (ip, port, flow, scope)
    for IPv6. We only read sockaddr[0], so the rest doesn't matter.
    """
    return [(0, 0, 0, "", (ip, 0))]


@pytest.mark.parametrize(
    "ip,is_private",
    [
        # Public IPs → safe (validator allows HEAD)
        ("8.8.8.8", False),  # Google DNS
        ("1.1.1.1", False),  # Cloudflare DNS
        ("142.250.80.46", False),  # google.com range
    ],
)
def test_public_ips_are_not_private(ip, is_private):
    with patch(
        "src.api.v1.routes.stores.storefront_validation.socket.getaddrinfo",
        return_value=_mock_addrinfo(ip),
    ):
        assert _resolved_ip_is_private("any-host") is is_private


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",  # loopback
        "10.0.0.1",  # private RFC 1918 /8
        "10.255.255.255",
        "172.16.0.1",  # private RFC 1918 /12
        "172.31.255.255",
        "192.168.1.1",  # private RFC 1918 /16
        "169.254.169.254",  # AWS / GCP / Azure instance metadata — link-local
        "0.0.0.0",  # unspecified
        "::1",  # IPv6 loopback
        "fc00::1",  # IPv6 unique local
        "fe80::1",  # IPv6 link-local
    ],
)
def test_internal_ips_are_blocked(ip):
    with patch(
        "src.api.v1.routes.stores.storefront_validation.socket.getaddrinfo",
        return_value=_mock_addrinfo(ip),
    ):
        assert _resolved_ip_is_private("any-host") is True


def test_dns_failure_treated_as_internal():
    """Unresolvable host → return True so the validator rejects.

    Safer to refuse a HEAD request to a host we can't resolve than to
    attempt one and risk an unexpected timeout/dial against the wrong
    address.
    """
    import socket as _socket

    def _raise(*_args, **_kwargs):
        raise _socket.gaierror("name resolution failed")

    with patch(
        "src.api.v1.routes.stores.storefront_validation.socket.getaddrinfo",
        side_effect=_raise,
    ):
        assert _resolved_ip_is_private("nonexistent.invalid") is True


# ── _same_host ──────────────────────────────────────────────────────


def test_relative_redirect_is_same_host():
    """A redirect to /foo is always same-host by definition."""
    assert _same_host("/different/path", "https://acme.numueg.app") is True


def test_absolute_redirect_to_same_host_passes():
    assert _same_host("https://acme.numueg.app/foo", "https://acme.numueg.app") is True


def test_absolute_redirect_to_different_host_rejected():
    """SEC-002: the validator must NOT follow a redirect off-origin."""
    assert _same_host("https://evil.com/payload", "https://acme.numueg.app") is False


def test_redirect_with_different_scheme_rejected():
    """A http→https rewrite on the same host is still same-host; an
    ftp/file/data scheme is not."""
    assert _same_host("ftp://acme.numueg.app/file", "https://acme.numueg.app") is False


def test_malformed_redirect_url_rejected():
    """urlparse can't parse some inputs; treat as different-host."""
    assert _same_host("javascript:alert(1)", "https://acme.numueg.app") is False
