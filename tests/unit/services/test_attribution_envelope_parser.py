"""Unit tests for `_read_attribution_envelope` in the tracking route.

The helper resolves the attribution envelope from either the request
body (preferred) or the `numu_attribution` cookie (legacy fallback). It
NEVER raises — analytics outages must not break the storefront
response. These tests pin that contract.

Coverage:
- Body envelope wins when present
- Cookie fallback when body is absent
- Malformed cookie JSON returns None (silent fail)
- Wrong schema version returns None (forward-compat hatch)
- URL-encoded cookie (browser default) round-trips correctly
- Missing cookie returns None
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock
from urllib.parse import quote

import pytest

from src.api.v1.routes.storefront.tracking import _read_attribution_envelope
from src.core.entities.attribution import (
    ATTRIBUTION_SCHEMA_VERSION,
    AttributionSnapshot,
    AttributionTouch,
)


def _sample_touch(utm_source: str = "facebook") -> AttributionTouch:
    return AttributionTouch(
        ts="2026-05-21T14:33:00.000Z",
        utm_source=utm_source,
        utm_medium="social",
        utm_campaign="eid-sale-AB7K9X",
        utm_term=None,
        utm_content=None,
        gclid=None,
        fbclid=None,
        referrer="https://facebook.com/",
        landing_path="/product/abc",
    )


def _sample_envelope() -> AttributionSnapshot:
    return AttributionSnapshot(
        v=ATTRIBUTION_SCHEMA_VERSION,
        first_touch=_sample_touch(),
        last_touch=_sample_touch(),
        session_id="01HX2M",
    )


def _request_with_cookies(cookies: dict[str, str]) -> MagicMock:
    """Make a Request-like mock exposing only the `cookies` dict — enough
    for the helper, no need for a full FastAPI Request."""
    req = MagicMock()
    req.cookies = cookies
    return req


# ── Body wins over cookie ───────────────────────────────────────────


def test_body_envelope_is_returned_when_present():
    envelope = _sample_envelope()
    req = _request_with_cookies({"numu_attribution": "ignored"})
    out = _read_attribution_envelope(envelope, req)
    assert out is envelope


def test_body_envelope_wins_even_when_cookie_differs():
    body_env = _sample_envelope()
    cookie_env = AttributionSnapshot(
        v=1,
        first_touch=_sample_touch(utm_source="instagram"),
        last_touch=_sample_touch(utm_source="instagram"),
        session_id="OTHERID",
    )
    raw = quote(cookie_env.model_dump_json())
    req = _request_with_cookies({"numu_attribution": raw})
    out = _read_attribution_envelope(body_env, req)
    assert out is body_env
    assert out.last_touch.utm_source == "facebook"  # body's value


# ── Cookie fallback ─────────────────────────────────────────────────


def test_cookie_used_when_body_absent():
    envelope = _sample_envelope()
    raw = quote(envelope.model_dump_json())
    req = _request_with_cookies({"numu_attribution": raw})
    out = _read_attribution_envelope(None, req)
    assert out is not None
    assert out.last_touch.utm_source == "facebook"
    assert out.session_id == "01HX2M"


def test_unquoted_cookie_value_still_parses():
    """Some servers / proxies pre-decode cookie values."""
    envelope = _sample_envelope()
    raw = envelope.model_dump_json()  # no URL-encoding
    req = _request_with_cookies({"numu_attribution": raw})
    out = _read_attribution_envelope(None, req)
    assert out is not None
    assert out.last_touch.utm_campaign == "eid-sale-AB7K9X"


# ── Failure modes (silent None) ─────────────────────────────────────


def test_no_body_no_cookie_returns_none():
    req = _request_with_cookies({})
    assert _read_attribution_envelope(None, req) is None


def test_malformed_json_returns_none():
    """Bad JSON in the cookie value must not raise — just no attribution."""
    req = _request_with_cookies({"numu_attribution": "not-json"})
    assert _read_attribution_envelope(None, req) is None


def test_wrong_schema_version_returns_none():
    """A v2 envelope on a v1 server short-circuits cleanly — forward
    compatibility hatch."""
    payload = {
        "v": 99,
        "first_touch": json.loads(_sample_touch().model_dump_json()),
        "last_touch": json.loads(_sample_touch().model_dump_json()),
        "session_id": "X",
    }
    req = _request_with_cookies({"numu_attribution": quote(json.dumps(payload))})
    assert _read_attribution_envelope(None, req) is None


def test_partial_envelope_returns_none():
    """Missing required first_touch / last_touch falls through to None."""
    payload = {"v": 1, "session_id": "X"}
    req = _request_with_cookies({"numu_attribution": quote(json.dumps(payload))})
    assert _read_attribution_envelope(None, req) is None


def test_empty_cookie_value_returns_none():
    req = _request_with_cookies({"numu_attribution": ""})
    assert _read_attribution_envelope(None, req) is None


@pytest.mark.parametrize(
    "raw",
    [
        "null",
        "[]",
        '{"v": "not-an-int"}',
        '{"v": 1, "first_touch": "wrong-type"}',
    ],
)
def test_assorted_garbage_returns_none(raw):
    req = _request_with_cookies({"numu_attribution": quote(raw)})
    assert _read_attribution_envelope(None, req) is None
