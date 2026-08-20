"""Which TikTok Events API answers earn another attempt.

The whole point of this module is that TikTok hides failure inside success:
the transport works, the status is 200, and the verdict is a number in the
body. Every test here is really asking one question — does a transient
condition dressed as HTTP 200 still get retried?
"""

from __future__ import annotations

import pytest

from src.core.services.meta_delivery_policy import FailureKind
from src.core.services.tiktok_delivery_policy import classify_response


class TestSuccess:
    def test_two_hundred_with_code_zero_is_delivered(self):
        assert classify_response(200, 0, {"message": "OK"}) is None

    def test_two_hundred_with_a_nonzero_code_is_not_success(self):
        """external-contracts.md row 5: the success signal is `code == 0`,
        not the HTTP status. A 2xx alone proves only that TikTok answered."""
        assert classify_response(200, 40_100, None) is not None


class TestTheBugThisFixes:
    """A TikTok-side server error arrives as HTTP 200 with a 5xxxx code.

    The previous rule retried only on HTTP 429/5xx, so this was classified
    permanent and the event was dropped without a single retry — the same
    shape as the Meta defect where throttling arrived as HTTP 400.
    """

    @pytest.mark.parametrize("code", [50_000, 50_001, 51_234, 59_999])
    def test_server_error_family_is_retryable_despite_http_200(self, code):
        kind = classify_response(200, code, {"message": "internal error"})
        assert kind is FailureKind.SERVER_ERROR
        assert kind.retryable


class TestAmbiguous40100:
    """40100 is contested, and the default has to fail in the cheap direction.

    TikTok's own rate-limit documentation says the server returns 40100 when
    throttled; several third-party references call it an invalid token. The
    message decides, and silence defaults to retryable — a wrongly-retried
    dead token costs 12 bounded attempts, a wrongly-dropped throttled event
    is gone for good.
    """

    def test_throttle_wording_reads_as_rate_limited(self):
        kind = classify_response(200, 40_100, {"message": "rate limit exceeded"})
        assert kind is FailureKind.RATE_LIMITED
        assert kind.retryable

    def test_token_wording_reads_as_credentials(self):
        kind = classify_response(
            200, 40_100, {"message": "access token is invalid or expired"}
        )
        assert kind is FailureKind.INVALID_CREDENTIALS
        assert not kind.retryable

    def test_silence_defaults_to_retryable(self):
        assert classify_response(200, 40_100, {"message": ""}).retryable
        assert classify_response(200, 40_100, None).retryable


class TestPermanent:
    @pytest.mark.parametrize("code", [40_001, 40_002, 40_105])
    def test_auth_and_permission_codes_are_not_retried(self, code):
        kind = classify_response(200, code, {"message": "not authorized"})
        assert kind is FailureKind.INVALID_CREDENTIALS
        assert not kind.retryable

    def test_an_unrecognised_code_is_treated_as_our_bad_payload(self):
        kind = classify_response(200, 40_055, {"message": "bad field"})
        assert kind is FailureKind.INVALID_PAYLOAD
        assert not kind.retryable


class TestTransportSignals:
    def test_http_429_is_rate_limited(self):
        assert classify_response(429, None, None) is FailureKind.RATE_LIMITED

    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    def test_http_5xx_is_retryable(self, status):
        assert classify_response(status, None, None).retryable

    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_statuses_are_credentials(self, status):
        assert classify_response(status, None, None) is (
            FailureKind.INVALID_CREDENTIALS
        )


class TestDegradedBodies:
    """A proxy can return HTML, and `response_body` is then `{"raw": ...}`
    with no code at all. The classifier must still decide something sane."""

    def test_no_code_and_throttle_wording_still_retries(self):
        kind = classify_response(200, None, {"message": "Too many requests"})
        assert kind.retryable

    def test_no_code_and_nothing_to_go_on_is_permanent(self):
        kind = classify_response(400, None, {"raw": "<html>bad request</html>"})
        assert kind is FailureKind.INVALID_PAYLOAD

    def test_non_dict_body_does_not_explode(self):
        assert classify_response(200, 50_000, "plain string").retryable
        assert classify_response(200, 40_055, None) is FailureKind.INVALID_PAYLOAD


class TestVocabularyIsShared:
    def test_it_reuses_the_meta_failure_kinds(self):
        """One vocabulary across both vendors is what makes the eventual
        shared module (remains.md R-10) a move rather than a merge."""
        kind = classify_response(200, 50_000, None)
        assert isinstance(kind, FailureKind)
