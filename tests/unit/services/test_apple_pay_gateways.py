"""Apple Pay wiring in the Paymob + Kashier payment services.

Constructs the REAL service classes (Paymob Intention API / Kashier Payment
Sessions v3) and asserts that the Apple Pay method reaches the gateway request
payload only when the merchant configured it. httpx is mocked, so no network.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infrastructure.external_services.kashier.payment_service import (
    KashierPaymentService,
)
from src.infrastructure.external_services.paymob.payment_service import (
    PaymobPaymentService,
)


def _patched_post(response_json: dict):
    """Patch httpx.AsyncClient and return (context_manager, post_mock).

    Inspect ``post_mock.call_args.kwargs["json"]`` after the call to read the
    payload the service sent to the gateway.
    """
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = response_json
    post = AsyncMock(return_value=mock_response)

    cm = patch("httpx.AsyncClient")
    return cm, post


class TestPaymobApplePay:
    @pytest.mark.asyncio
    async def test_apple_pay_integration_id_appended_to_payment_methods(self):
        service = PaymobPaymentService(
            secret_key="sk",
            public_key="pk",
            hmac_secret="hm",
            card_integration_id="111",
            wallet_integration_id="222",
            apple_pay_integration_id="333",
        )
        cm, post = _patched_post({"id": "intent_1", "client_secret": "cs_1"})
        with cm as mock_client:
            mock_client.return_value.__aenter__.return_value.post = post
            await service.create_payment_intent(
                amount=10000, currency="EGP", metadata={"order_id": "o1"}
            )
        payload = post.call_args.kwargs["json"]
        assert payload["payment_methods"] == [111, 222, 333]

    @pytest.mark.asyncio
    async def test_no_apple_pay_when_not_configured(self):
        service = PaymobPaymentService(
            secret_key="sk",
            public_key="pk",
            hmac_secret="hm",
            card_integration_id="111",
            wallet_integration_id="222",
        )
        cm, post = _patched_post({"id": "i", "client_secret": "c"})
        with cm as mock_client:
            mock_client.return_value.__aenter__.return_value.post = post
            await service.create_payment_intent(
                amount=100, currency="EGP", metadata={"order_id": "o"}
            )
        payload = post.call_args.kwargs["json"]
        assert payload["payment_methods"] == [111, 222]


class TestKashierApplePay:
    @pytest.mark.asyncio
    async def test_apple_pay_token_appended_when_enabled(self):
        service = KashierPaymentService(mid="m", api_key="k", apple_pay_enabled=True)
        cm, post = _patched_post({"_id": "s1", "sessionUrl": "https://pay/x"})
        with cm as mock_client:
            mock_client.return_value.__aenter__.return_value.post = post
            await service.create_payment_intent(
                amount=10000, currency="EGP", metadata={"order_id": "o1"}
            )
        payload = post.call_args.kwargs["json"]
        assert payload["allowedMethods"] == "card,wallet,applepay"

    @pytest.mark.asyncio
    async def test_no_apple_pay_token_when_disabled(self):
        service = KashierPaymentService(mid="m", api_key="k", apple_pay_enabled=False)
        cm, post = _patched_post({"_id": "s1", "sessionUrl": "https://pay/x"})
        with cm as mock_client:
            mock_client.return_value.__aenter__.return_value.post = post
            await service.create_payment_intent(
                amount=10000, currency="EGP", metadata={"order_id": "o1"}
            )
        payload = post.call_args.kwargs["json"]
        assert payload["allowedMethods"] == "card,wallet"
