"""Unit tests for MoyasarPaymentService (pure logic).

Covers the security-critical webhook authentication and the provider/auth
wiring. The HTTP methods (create/confirm/refund) are thin httpx wrappers and
are better exercised by integration tests against Moyasar's sandbox.
"""

import json

from src.core.interfaces.services.payment_service import PaymentProvider
from src.infrastructure.external_services.moyasar.payment_service import (
    MoyasarPaymentService,
)


class TestMoyasarPaymentService:
    def setup_method(self):
        self.service = MoyasarPaymentService(
            secret_key="sk_test_abc123",
            webhook_secret="whsec_shared_token",
            currency="SAR",
        )

    def test_provider_returns_moyasar(self):
        assert self.service.provider == PaymentProvider.MOYASAR

    def test_auth_uses_secret_key_as_username(self):
        assert self.service._auth() == ("sk_test_abc123", "")

    def test_default_currency_is_sar(self):
        svc = MoyasarPaymentService(secret_key="sk")
        assert svc._currency == "SAR"

    # -- webhook authentication ------------------------------------------

    def test_verify_webhook_accepts_matching_secret_token(self):
        payload = json.dumps({
            "type": "payment_paid",
            "secret_token": "whsec_shared_token",
            "data": {"id": "pay_1", "status": "paid", "metadata": {"order_id": "o1"}},
        }).encode()
        result = self.service.verify_webhook_signature(payload, "")
        assert result is not None
        assert result["data"]["id"] == "pay_1"

    def test_verify_webhook_rejects_wrong_secret_token(self):
        payload = json.dumps({
            "type": "payment_paid",
            "secret_token": "wrong",
            "data": {},
        }).encode()
        assert self.service.verify_webhook_signature(payload, "") is None

    def test_verify_webhook_rejects_missing_secret_token(self):
        payload = json.dumps({"type": "payment_paid", "data": {}}).encode()
        assert self.service.verify_webhook_signature(payload, "") is None

    def test_verify_webhook_fails_closed_when_no_secret_configured(self):
        svc = MoyasarPaymentService(secret_key="sk", webhook_secret=None)
        payload = json.dumps({"secret_token": "anything", "data": {}}).encode()
        # No configured secret and no signature override → cannot authenticate.
        assert svc.verify_webhook_signature(payload, "") is None

    def test_verify_webhook_accepts_signature_override_when_no_secret(self):
        svc = MoyasarPaymentService(secret_key="sk", webhook_secret=None)
        payload = json.dumps({"secret_token": "tok", "data": {"id": "p"}}).encode()
        assert svc.verify_webhook_signature(payload, "tok") is not None

    def test_verify_webhook_rejects_invalid_json(self):
        assert self.service.verify_webhook_signature(b"not json", "") is None
