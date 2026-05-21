"""Unit tests for InstapayPaymentService + reference code + QR generator."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.core.interfaces.services.payment_service import PaymentProvider
from src.infrastructure.external_services.instapay.payment_service import (
    DEFAULT_EXPIRY_MINUTES,
    InstapayPaymentService,
    generate_reference_code,
)
from src.infrastructure.external_services.instapay.qr_generator import (
    build_qr_payload,
    render_qr_data_url,
)


class TestReferenceCode:
    """Ref codes must be short, typable, and collision-resistant."""

    def test_format_is_prefix_dash_six_alphanumerics(self):
        code = generate_reference_code()
        assert code.startswith("NU-")
        suffix = code[3:]
        assert len(suffix) == 6
        assert suffix.isalnum()

    def test_alphabet_excludes_ambiguous_chars(self):
        # Crockford-ish: avoid 0/O and 1/I confusion to reduce customer typos
        for _ in range(200):
            code = generate_reference_code()
            suffix = code[3:]
            assert "0" not in suffix
            assert "1" not in suffix
            assert "I" not in suffix
            assert "O" not in suffix

    def test_prefix_is_customizable(self):
        assert generate_reference_code(prefix="XX").startswith("XX-")

    def test_codes_are_distinct(self):
        # 10⁹ combinations → collisions in 500 samples are vanishingly rare
        codes = {generate_reference_code() for _ in range(500)}
        assert len(codes) == 500


class TestQRPayload:
    """The scannable string must encode the four things bank apps need."""

    def test_payload_includes_all_fields(self):
        payload = build_qr_payload(
            ipa="merchant@cib",
            amount_cents=12_345,
            reference_code="NU-ABCDEF",
            note="Order 123",
        )
        assert payload.startswith("instapay://pay?")
        assert "ipa=merchant@cib" in payload
        assert "amount=123.45" in payload
        assert "ref=NU-ABCDEF" in payload
        # Notes can contain spaces — must be URL-encoded
        assert "Order%20123" in payload

    def test_note_is_optional(self):
        payload = build_qr_payload(
            ipa="merchant@cib",
            amount_cents=1000,
            reference_code="NU-123456",
        )
        assert "note=" not in payload

    def test_amount_uses_two_decimal_places(self):
        assert "amount=10.00" in build_qr_payload(
            ipa="a@b", amount_cents=1000, reference_code="X"
        )
        assert "amount=0.05" in build_qr_payload(
            ipa="a@b", amount_cents=5, reference_code="X"
        )

    def test_render_qr_data_url_returns_png_data_url(self):
        payload = build_qr_payload(
            ipa="merchant@cib", amount_cents=1000, reference_code="NU-XXXXXX"
        )
        data_url = render_qr_data_url(payload)
        assert data_url.startswith("data:image/png;base64,")
        # Guardrail: real QR PNG should be more than a few bytes
        assert len(data_url) > 500


class TestInstapayPaymentService:
    """Provider identity + intent payload + checkout response shape."""

    def setup_method(self) -> None:
        self.service = InstapayPaymentService(
            ipa="merchant@cib",
            ipa_display_name="Test Store",
            fallback_phone="+201234567890",
        )

    def test_provider_is_instapay(self):
        assert self.service.provider == PaymentProvider.INSTAPAY

    def test_ipa_required(self):
        with pytest.raises(Exception):
            InstapayPaymentService(ipa="")

    def test_build_intent_payload_returns_payload_and_expiry(self):
        payload, expires_at = self.service.build_intent_payload(
            amount_cents=10_000,
            reference_code="NU-TESTXX",
            note="Order 42",
        )
        assert "ipa=merchant@cib" in payload
        assert "ref=NU-TESTXX" in payload
        # Expiry is in the future, roughly the default window
        delta = (expires_at - datetime.now(UTC)).total_seconds() / 60
        assert delta >= DEFAULT_EXPIRY_MINUTES - 1
        assert delta <= DEFAULT_EXPIRY_MINUTES + 1

    def test_build_intent_payload_respects_custom_expiry_minutes(self):
        svc = InstapayPaymentService(ipa="merchant@cib", expiry_minutes=10)
        _, expires_at = svc.build_intent_payload(
            amount_cents=100,
            reference_code="NU-XX",
        )
        delta_min = (expires_at - datetime.now(UTC)).total_seconds() / 60
        assert 9 <= delta_min <= 11

    @pytest.mark.asyncio
    async def test_create_payment_intent_returns_ref_code_as_id(self):
        intent = await self.service.create_payment_intent(
            amount=10_000,
            currency="EGP",
            metadata={"reference_code": "NU-FIXED1", "order_number": "NU-0001"},
        )
        assert intent.id == "NU-FIXED1"
        assert intent.amount == 10_000
        assert intent.currency == "EGP"
        assert intent.provider == PaymentProvider.INSTAPAY
        assert intent.status == "awaiting_payment"
        # client_secret carries the QR payload so the storefront can render
        # a QR without a second round-trip
        assert intent.client_secret.startswith("instapay://pay?")

    @pytest.mark.asyncio
    async def test_create_payment_intent_generates_ref_code_when_absent(self):
        intent = await self.service.create_payment_intent(amount=5_000, currency="EGP")
        assert intent.id.startswith("NU-")
        assert len(intent.id) == 9

    @pytest.mark.asyncio
    async def test_push_payment_methods_are_neutral_stubs(self):
        # The use case / route, not this service, drives real state —
        # these must not blow up when called by generic plumbing.
        confirm = await self.service.confirm_payment("NU-TESTXX")
        assert confirm.success is False
        assert confirm.error_code == "manual_verification_required"

        cancel = await self.service.cancel_payment("NU-TESTXX")
        assert cancel.success is True

        refund = await self.service.refund_payment("NU-TESTXX")
        assert refund.success is False

    def test_no_webhook_verification(self):
        # Signals to webhook dispatchers that nothing here will ever
        # validate — a dead-letter queue signal rather than a silent pass.
        assert self.service.verify_webhook_signature(b"{}", "sig") is None

    def test_to_checkout_payload_shape(self):
        expires = datetime.now(UTC)
        payload = self.service.to_checkout_payload(
            reference_code="NU-ABCDEF",
            qr_payload="instapay://pay?ipa=merchant@cib&amount=1.00&ref=NU-ABCDEF",
            amount_cents=100,
            currency="EGP",
            expires_at=expires,
            order_id="order-uuid",
        )
        assert payload["provider"] == "instapay"
        assert payload["type"] == "manual_verification"
        assert payload["reference_code"] == "NU-ABCDEF"
        assert payload["ipa"] == "merchant@cib"
        assert payload["ipa_display_name"] == "Test Store"
        assert payload["fallback_phone"] == "+201234567890"
        assert payload["amount_cents"] == 100
        assert payload["amount"] == "1.00"
        assert payload["currency"] == "EGP"
        assert payload["order_id"] == "order-uuid"
        # QR is now rendered client-side from qr_payload; the backend
        # does not ship the PNG data URL on the checkout path anymore.
        assert "qr_data_url" not in payload
        assert payload["qr_payload"].startswith("instapay://")
        assert "expires_at" in payload
        assert "expires_in_seconds" in payload
