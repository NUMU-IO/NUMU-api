"""Unit tests for the IP-anonymization helper used by /track ingest.

The function lives in the storefront tracking route module so the
import keeps it next to the call-site. Behaviour spec:

* IPv4 is truncated to /24 (last octet zeroed)
* IPv6 is truncated to /48
* Unparseable input returns None — better to drop the row than
  persist something that turns out to be a hostname or PII string
"""

from src.api.v1.routes.storefront.tracking import _anonymize_ip


def test_ipv4_truncated_to_slash_24():
    assert _anonymize_ip("203.0.113.45") == "203.0.113.0"
    assert _anonymize_ip("10.20.30.40") == "10.20.30.0"


def test_ipv6_truncated_to_slash_48():
    # 2001:0db8:1234:5678:abcd::1 → /48 keeps the first three groups
    out = _anonymize_ip("2001:db8:1234:5678:abcd::1")
    assert out is not None
    assert out.startswith("2001:db8:1234")


def test_none_input_returns_none():
    assert _anonymize_ip(None) is None
    assert _anonymize_ip("") is None


def test_garbage_input_returns_none():
    assert _anonymize_ip("not-an-ip") is None
    assert _anonymize_ip("999.999.999.999") is None
