"""Tests for the SSRF URL guard (Phase 1)."""

import ipaddress

import pytest

from src.core.url_guard import UnsafeUrlError, _blocked, assert_public_http_url


class TestBlocked:
    def test_link_local_metadata_blocked_even_when_private_allowed(self):
        ip = ipaddress.ip_address("169.254.169.254")  # cloud IMDS
        assert _blocked(ip, allow_private=True) is True
        assert _blocked(ip, allow_private=False) is True

    def test_ipv4_mapped_metadata_blocked(self):
        ip = ipaddress.ip_address("::ffff:169.254.169.254")
        assert _blocked(ip, allow_private=True) is True

    def test_loopback_and_private_allowed_in_dev_blocked_in_prod(self):
        for addr in ("127.0.0.1", "10.0.0.5", "192.168.1.10", "::1"):
            ip = ipaddress.ip_address(addr)
            assert _blocked(ip, allow_private=True) is False
            assert _blocked(ip, allow_private=False) is True

    def test_public_ip_never_blocked(self):
        for addr in ("8.8.8.8", "1.1.1.1"):
            ip = ipaddress.ip_address(addr)
            assert _blocked(ip, allow_private=True) is False
            assert _blocked(ip, allow_private=False) is False


class TestAssertPublicHttpUrl:
    def test_rejects_non_http_scheme(self):
        for url in ("ftp://example.com", "file:///etc/passwd", "gopher://x"):
            with pytest.raises(UnsafeUrlError):
                assert_public_http_url(url)

    def test_rejects_missing_host(self):
        with pytest.raises(UnsafeUrlError):
            assert_public_http_url("http:///theme.js")

    def test_rejects_metadata_ip_in_any_env(self):
        with pytest.raises(UnsafeUrlError):
            assert_public_http_url(
                "http://169.254.169.254/latest/meta-data/", allow_private=True
            )

    def test_rejects_loopback_in_prod(self):
        with pytest.raises(UnsafeUrlError):
            assert_public_http_url("http://127.0.0.1:5173", allow_private=False)
        with pytest.raises(UnsafeUrlError):
            assert_public_http_url("https://[::1]/theme.js", allow_private=False)

    def test_allows_localhost_in_dev(self):
        # Must not raise — the local dev-server workflow.
        assert_public_http_url("http://127.0.0.1:5173", allow_private=True)
        assert_public_http_url("http://localhost:4321", allow_private=True)
