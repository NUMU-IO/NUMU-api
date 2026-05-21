"""Unit tests for the Meta CAPI Celery task helpers.

These cover the pure-Python pieces of ``meta_capi.py`` — the per-step
mapping, the response redaction, and the backoff policy. The full
async ``_send_event`` integration (DB + httpx) is exercised by
``tests/integration/test_track_fanout.py``.

We deliberately mock httpx at a higher level in the integration tests
rather than calling Meta — see scope constraint "DO NOT call the real
Meta API from any test".
"""

from __future__ import annotations

import pytest

from src.infrastructure.messaging.tasks.meta_capi import (
    _backoff_from_response,
    _funnel_step_to_meta_event,
    _redact_response,
)

# ---------------------------------------------------------------------------
# _redact_response — PII-bearing response keys must be scrubbed before storage
# ---------------------------------------------------------------------------


class TestRedactResponse:
    """``meta_event_log.response_body`` is shown to merchants and stored
    long-term — it MUST NOT carry the PII fragments Meta sometimes
    echoes back in error messages."""

    def test_none_returns_none(self):
        assert _redact_response(None) is None

    def test_clean_response_passes_through(self):
        body = {"events_received": 1, "fbtrace_id": "Abc123"}
        out = _redact_response(body)
        assert out == body

    def test_top_level_error_user_msg_redacted(self):
        body = {
            "events_received": 0,
            "error_user_msg": "shopper@example.com is invalid",
            "error_user_title": "Invalid email",
        }
        out = _redact_response(body)
        assert out["error_user_msg"] == "[redacted]"
        assert out["error_user_title"] == "[redacted]"
        # Non-PII fields preserved.
        assert out["events_received"] == 0

    def test_nested_error_user_msg_redacted(self):
        body = {
            "error": {
                "code": 100,
                "type": "OAuthException",
                "message": "Invalid parameter",
                "error_user_msg": "shopper@example.com",
                "error_user_title": "Invalid email",
                "fbtrace_id": "Abc123",
            }
        }
        out = _redact_response(body)
        assert out["error"]["error_user_msg"] == "[redacted]"
        assert out["error"]["error_user_title"] == "[redacted]"
        # Code, type, fbtrace_id all retained.
        assert out["error"]["code"] == 100
        assert out["error"]["fbtrace_id"] == "Abc123"


# ---------------------------------------------------------------------------
# _backoff_from_response — Retry-After header & exponential fallback
# ---------------------------------------------------------------------------


class TestBackoffFromResponse:
    """When Meta returns 429/5xx, we must honor Retry-After if provided
    and fall back to exponential backoff capped at 5 minutes."""

    def test_retry_after_used_when_present(self):
        headers = {"retry-after": "47"}
        assert _backoff_from_response(headers, retries=0) == 47

    def test_retry_after_capped_at_300(self):
        headers = {"retry-after": "999"}
        assert _backoff_from_response(headers, retries=0) == 300

    def test_no_retry_after_uses_exponential(self):
        # 2^0 = 1, 2^1 = 2, 2^4 = 16, 2^9 = 512 → cap to 300
        assert _backoff_from_response({}, retries=0) == 1
        assert _backoff_from_response({}, retries=1) == 2
        assert _backoff_from_response({}, retries=4) == 16
        assert _backoff_from_response({}, retries=9) == 300

    def test_garbage_retry_after_falls_back(self):
        headers = {"retry-after": "not-a-number"}
        # Falls back to exponential from retries.
        assert _backoff_from_response(headers, retries=2) == 4

    def test_zero_retry_after_falls_back(self):
        # "0" is treated as "no preference" → exponential.
        headers = {"retry-after": "0"}
        assert _backoff_from_response(headers, retries=3) == 8


# ---------------------------------------------------------------------------
# Funnel-step mapping — ensure no drift between the public table and helper
# ---------------------------------------------------------------------------


class TestFunnelMappingHelper:
    """Mirror of test_tracking_settings.TestFunnelStepMapping — kept here
    too so failures pin the bug to the messaging-layer module."""

    @pytest.mark.parametrize(
        "step,expected",
        [
            ("page_view", "PageView"),
            ("product_view", "ViewContent"),
            ("add_to_cart", "AddToCart"),
            ("checkout_started", "InitiateCheckout"),
            ("order_completed", "Purchase"),
        ],
    )
    def test_all_supported_steps(self, step: str, expected: str):
        assert _funnel_step_to_meta_event(step) == expected

    def test_unknown_step_returns_none(self):
        assert _funnel_step_to_meta_event("order_delivered") is None
        assert _funnel_step_to_meta_event("totally_made_up") is None
