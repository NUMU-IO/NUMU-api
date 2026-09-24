"""NUMU's own Kashier account: signed card-form orders for platform payments.

Wallet top-ups (``WTOP-``) and plan subscriptions (``SUB-``) are paid on the
platform Kashier account through the hub's own card form. The hub posts the
card straight to Kashier's Direct API; this module only signs the order, and
the platform webhook (``/webhooks/kashier/platform/callback``) settles it.

A plan payment can also save the card; ``PlatformKashierRecurring`` then
charges it on each renewal.
"""

import json

from fastapi import HTTPException, status

from src.config.settings import get_settings
from src.core.interfaces.services.payment_service import PaymentResult
from src.infrastructure.external_services.kashier import KashierPaymentService


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


def platform_card_params(
    *,
    reference: str,
    amount_cents: int,
    description: str,
    redirect_url: str,
    save_for: str | None = None,
) -> dict:
    """Signed order for NUMU's card page.

    ``save_for`` (the tenant id, used as Kashier customer reference) also
    saves the card for renewals. No recurring ``agreement`` is sent: Kashier
    rejects it for this account with "invalid credentials" (verified live
    2026-09-24); a plain saved card works.
    """
    api_base = get_settings().platform_api_base_url.rstrip("/")
    card_extra = {"save": True} if save_for else None
    params = _platform_service().direct_payment_params(
        reference=reference,
        amount_cents=amount_cents,
        currency="EGP",
        description=description,
        webhook_url=_webhook_url(),
        redirect_url=redirect_url,
        customer_reference=save_for,
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
