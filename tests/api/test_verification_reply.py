"""Tests for backend-015 verification reply ingestion.

Focused on the reply parser since that's the only part that's
business-logic. The webhook wiring is exercised through manual end-
to-end tests against a Shopify dev store; here we pin the parser
contract so future template changes don't accidentally break the
yes/no recognition surface that this whole sprint slice depends on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.application.use_cases.shopify.handle_verification_reply import (
    is_within_reply_window,
    parse_reply,
)


class TestParseReply:
    @pytest.mark.parametrize(
        "text",
        [
            "yes",
            "Yes",
            "YES",
            "y",
            "Y",
            "confirm",
            "ok",
            "okay",
            "sure",
            "نعم",
            "ايوه",
            "أيوه",
            "اه",
            "yes!",
            "yes.",
            " yes ",
            "نعم؟",
        ],
    )
    def test_yes_tokens_classify_as_confirmed(self, text):
        assert parse_reply(text) == "confirmed"

    @pytest.mark.parametrize(
        "text",
        [
            "no",
            "No",
            "n",
            "cancel",
            "Cancel",
            "stop",
            "nope",
            "لا",
            "لأ",
            "no.",
            "no!",
            " no ",
        ],
    )
    def test_no_tokens_classify_as_rejected(self, text):
        assert parse_reply(text) == "rejected"

    @pytest.mark.parametrize(
        "text",
        [
            "yes please send the link again",
            "what is this for?",
            "I want to change my address",
            "yes, but can you call me",
            "12345",
            "",
            None,
            "   ",
            "thanks",
        ],
    )
    def test_other_text_is_not_a_reply(self, text):
        """Long messages and clarifications must NOT be treated as
        verification answers — they should flow through the regular
        conversation path so a merchant can respond. Lock this in so
        a future "fuzzy match" optimization doesn't regress quietly."""
        assert parse_reply(text) == "not_a_reply"


class TestReplyWindow:
    def test_reply_within_24h_is_in_window(self):
        sent = datetime.now(UTC) - timedelta(hours=2)
        assert is_within_reply_window(sent) is True

    def test_reply_at_exact_24h_is_in_window(self):
        # Pin "now" to avoid microsecond drift between the test's
        # datetime.now and the helper's internal call.
        anchor = datetime.now(UTC)
        sent = anchor - timedelta(hours=24)
        assert is_within_reply_window(sent, now=anchor) is True

    def test_reply_after_24h_is_out_of_window(self):
        sent = datetime.now(UTC) - timedelta(hours=25)
        assert is_within_reply_window(sent) is False

    def test_naive_datetime_is_treated_as_utc(self):
        """Defensive: some legacy DB columns return naive datetimes.
        Treating them as UTC is correct for our logging convention
        and prevents a TypeError on subtraction."""
        sent = (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None)
        assert is_within_reply_window(sent) is True
