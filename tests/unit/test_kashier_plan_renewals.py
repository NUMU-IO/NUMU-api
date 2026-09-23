"""Kashier saved-card plan renewals: signing, the CONTAUTH charge, the adapter.

The first plan payment saves the card under a recurring agreement, signed
with the tenant as Kashier customer reference; renewals charge the token
server-side with interactionSource CONTAUTH and no 3-D Secure.
"""

import asyncio
import hashlib
import hmac
import json
from unittest.mock import patch

from src.application.services import platform_kashier
from src.infrastructure.external_services.kashier import KashierPaymentService

KEY = "platform-test-key"
MID = "MID-1-2"


def _service() -> KashierPaymentService:
    return KashierPaymentService(mid=MID, api_key=KEY, mode="live")


def _hash(path: str) -> str:
    return hmac.new(KEY.encode(), path.encode(), hashlib.sha256).hexdigest()


def test_saving_a_card_signs_the_customer_reference_and_sets_the_agreement():
    params = _service().direct_payment_params(
        reference="SUB-ABC123",
        amount_cents=25000,
        currency="EGP",
        description="d",
        webhook_url="https://w",
        redirect_url="https://r",
        customer_reference="tenant-1",
        card_extra={"save": True, "agreement": {"type": "RECURRING"}},
    )
    assert params["hash"] == _hash(f"/?payment={MID}.SUB-ABC123.250.00.EGP.tenant-1")
    assert params["body"]["customer"] == {"reference": "tenant-1"}
    assert params["body"]["interactionSource"] == "RECURRING"
    assert params["card_extra"]["agreement"]["type"] == "RECURRING"


def test_monthly_plans_get_a_monthly_agreement_and_annual_ones_card_on_file():
    assert platform_kashier._agreement("monthly")["paymentFrequency"] == "MONTHLY"
    assert platform_kashier._agreement("annual")["type"] == "UNSCHEDULED"


class _Response:
    status_code = 200

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def _charge(reply: dict):
    sent = {}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, url, json, headers, timeout):
            sent.update(url=url, body=json, headers=headers)
            return _Response(reply)

    with patch(
        "src.infrastructure.external_services.kashier.payment_service.httpx.AsyncClient",
        _Client,
    ):
        result = asyncio.run(
            _service().charge_recurring_token(
                card_token="tok-1",
                agreement_id="agr-1",
                customer_reference="tenant-1",
                reference="REN-abc-20261023-0",
                amount_cents=25000,
                currency="EGP",
                webhook_url="https://w",
            )
        )
    return result, sent


def test_a_renewal_charges_the_token_merchant_initiated_without_3ds():
    result, sent = _charge({"response": {"status": "SUCCESS", "transactionId": "TX-9"}})
    card = sent["body"]["paymentMethod"]["card"]
    assert result.success and result.payment_id == "TX-9"
    assert sent["url"] == "https://fep.kashier.io/v3/orders/"
    assert sent["body"]["interactionSource"] == "CONTAUTH"
    assert card == {
        "cardToken": "tok-1",
        "enable3DS": False,
        "agreement": {"id": "agr-1"},
    }
    assert sent["headers"]["Kashier-Hash"] == _hash(
        f"/?payment={MID}.REN-abc-20261023-0.250.00.EGP.tenant-1"
    )


def test_a_declined_renewal_is_a_failure():
    result, _ = _charge({
        "response": {"status": "FAILURE"},
        "messages": {"en": "Declined"},
    })
    assert not result.success and result.error_message == "Declined"


def test_the_adapter_unpacks_the_stored_secret():
    calls = {}

    class _Svc:
        async def charge_recurring_token(self, **kwargs):
            calls.update(kwargs)
            return "ok"

    secret = platform_kashier.saved_card_secret("tok-1", "agr-1", "tenant-1")
    with patch.object(platform_kashier, "_platform_service", lambda: _Svc()):
        out = asyncio.run(
            platform_kashier.PlatformKashierRecurring().charge_saved_token(
                card_token=secret, amount=25000, currency="EGP", order_id="REN-1"
            )
        )
    assert out == "ok"
    assert calls["card_token"] == "tok-1"
    assert calls["agreement_id"] == "agr-1"
    assert calls["customer_reference"] == "tenant-1"
    assert calls["reference"] == "REN-1"
    assert json.loads(secret) == {"t": "tok-1", "a": "agr-1", "c": "tenant-1"}
