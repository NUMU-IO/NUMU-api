"""NUMU's own Kashier account: signed card-form orders for platform payments.

Wallet top-ups (``WTOP-``) and plan subscriptions (``SUB-``) are paid on the
platform Kashier account through the hub's own card form. The hub posts the
card straight to Kashier's Direct API; this module only signs the order, and
the platform webhook (``/webhooks/kashier/platform/callback``) settles it.

A plan payment can also save the card under a Kashier recurring agreement;
``PlatformKashierRecurring`` then charges it on each renewal.
"""

import json
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status

from src.config.settings import get_settings
from src.core.interfaces.services.payment_service import PaymentResult
from src.infrastructure.external_services.kashier import KashierPaymentService

# Generous caps on the agreement so price changes never block a renewal;
# the amount of each charge still comes from the server-side plan price.
_AGREEMENT_YEARS = 10
_AGREEMENT_MAX_AMOUNT_EGP = 100_000


def _platform_service() -> KashierPaymentService:
    s = get_settings()
    if not (s.platform_kashier_mid and s.platform_kashier_api_key):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Card payments are not configured.",
        )
    return KashierPaymentService(
        mid=s.platform_kashier_mid,
        api_key=s.platform_kashier_api_key,
        mode=s.platform_kashier_mode,
    )


def _webhook_url() -> str:
    api_base = get_settings().platform_api_base_url.rstrip("/")
    return f"{api_base}/api/v1/webhooks/kashier/platform/callback"


def _agreement(billing_cycle: str) -> dict:
    """Kashier recurring agreement for a saved plan card.

    Monthly uses the documented RECURRING/MONTHLY shape. Kashier documents no
    yearly frequency, so annual plans use UNSCHEDULED (card on file).
    """
    expiry = (datetime.now(UTC) + timedelta(days=365 * _AGREEMENT_YEARS)).date()
    if billing_cycle != "monthly":
        return {"type": "UNSCHEDULED", "expiryDate": expiry.isoformat()}
    return {
        "type": "RECURRING",
        "amountVariability": "VARIABLE",
        "paymentFrequency": "MONTHLY",
        "expiryDate": expiry.isoformat(),
        # Dunning retries a failed renewal within days.
        "minimumDaysBetweenPayments": 1,
        "maximumAmountPerPayment": _AGREEMENT_MAX_AMOUNT_EGP,
        "numberOfPayments": 12 * _AGREEMENT_YEARS,
    }


def platform_card_params(
    *,
    reference: str,
    amount_cents: int,
    description: str,
    redirect_url: str,
    save_for: tuple[str, str] | None = None,
) -> dict:
    """Signed order for NUMU's card page.

    ``save_for=(customer_reference, billing_cycle)`` also saves the card with
    a recurring agreement; the tenant id is the Kashier customer reference.
    """
    api_base = get_settings().platform_api_base_url.rstrip("/")
    customer_reference, card_extra = None, None
    if save_for:
        customer_reference, cycle = save_for
        card_extra = {"save": True, "agreement": _agreement(cycle)}
    params = _platform_service().direct_payment_params(
        reference=reference,
        amount_cents=amount_cents,
        currency="EGP",
        description=description,
        webhook_url=_webhook_url(),
        redirect_url=redirect_url,
        customer_reference=customer_reference,
        card_extra=card_extra,
    )
    # The page the hub frames to collect the card (platform_pay.py). Built
    # here so the card is typed on the API origin, never the hub's.
    params["page_url"] = f"{api_base}/api/v1/platform-pay/card"
    return params


def saved_card_secret(card_token: str, agreement_id: str | None, tenant_id: str) -> str:
    """What gets encrypted into ``tenants.kashier_card_token_encrypted``."""
    return json.dumps({"t": card_token, "a": agreement_id, "c": tenant_id})


class PlatformKashierRecurring:
    """Renewal charger for ``PaymobRecurringBillingService.charge_subscription``.

    That service decrypts the stored secret and calls ``charge_saved_token``;
    for Kashier the secret is ``saved_card_secret``'s JSON (token, agreement,
    customer reference), so the same dunning path serves both gateways.
    """

    async def charge_saved_token(
        self, card_token: str, amount: int, currency: str, order_id: str
    ) -> PaymentResult:
        saved = json.loads(card_token)
        return await _platform_service().charge_recurring_token(
            card_token=saved["t"],
            agreement_id=saved.get("a"),
            customer_reference=saved["c"],
            reference=order_id,
            amount_cents=amount,
            currency=currency,
            webhook_url=_webhook_url(),
        )
