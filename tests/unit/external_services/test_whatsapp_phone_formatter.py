"""Unit tests for ``WhatsAppMessagingService._format_phone_number``.

The formatter is the last hop before phone digits hit the WhatsApp
Cloud API. WA wants international digits *without* the ``+``. Pre-Part-1
the formatter hard-coded a ``2`` prefix for any 11-digit number starting
with ``0``, which broke non-EG recipients. Now it delegates to
``phonenumbers`` and only falls back to ``EG`` parsing for legacy rows.
"""

from __future__ import annotations

import pytest

from src.infrastructure.external_services.whatsapp.messaging_service import (
    WhatsAppMessagingService,
)


@pytest.fixture()
def service() -> WhatsAppMessagingService:
    # Constructor doesn't touch the network — we can build a bare instance
    # with placeholder creds since we're only exercising the pure helper.
    return WhatsAppMessagingService(
        access_token="test-token",
        phone_number_id="test-id",
        business_account_id="test-business",
        app_secret="test-secret",
    )


class TestFormatPhoneNumber:
    """The formatter must work for any country, not only Egypt."""

    def test_egyptian_e164(self, service: WhatsAppMessagingService) -> None:
        assert service._format_phone_number("+201001234567") == "201001234567"

    def test_saudi_e164(self, service: WhatsAppMessagingService) -> None:
        out = service._format_phone_number("+966501234567")
        assert out.startswith("966")
        assert "+" not in out

    def test_uae_e164(self, service: WhatsAppMessagingService) -> None:
        out = service._format_phone_number("+971501234567")
        assert out.startswith("971")
        assert "+" not in out

    def test_us_e164(self, service: WhatsAppMessagingService) -> None:
        assert service._format_phone_number("+12025551234") == "12025551234"

    def test_legacy_egyptian_local_format(
        self, service: WhatsAppMessagingService
    ) -> None:
        # Legacy rows that escaped the backfill still arrive as ``01…``.
        # The formatter should fall back to EG parsing and produce the
        # right international digits.
        assert service._format_phone_number("01001234567") == "201001234567"

    def test_invalid_raises(self, service: WhatsAppMessagingService) -> None:
        with pytest.raises(ValueError):
            service._format_phone_number("not-a-phone")

    def test_empty_raises(self, service: WhatsAppMessagingService) -> None:
        with pytest.raises(ValueError):
            service._format_phone_number("")

    def test_result_has_no_plus(self, service: WhatsAppMessagingService) -> None:
        # The contract: WA Cloud rejects payloads with ``+``.
        for raw in ("+201001234567", "+966501234567", "+12025551234"):
            assert "+" not in service._format_phone_number(raw)
