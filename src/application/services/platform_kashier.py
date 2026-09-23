"""NUMU's own Kashier account: signed card-form orders for platform payments.

Wallet top-ups (``WTOP-``) and plan subscriptions (``SUB-``) are paid on the
platform Kashier account through the hub's own card form. The hub posts the
card straight to Kashier's Direct API; this module only signs the order, and
the platform webhook (``/webhooks/kashier/platform/callback``) settles it.
"""

from fastapi import HTTPException, status

from src.config.settings import get_settings
from src.infrastructure.external_services.kashier import KashierPaymentService


def platform_card_params(
    *, reference: str, amount_cents: int, description: str, redirect_url: str
) -> dict:
    s = get_settings()
    if not (s.platform_kashier_mid and s.platform_kashier_api_key):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Card payments are not configured.",
        )
    service = KashierPaymentService(
        mid=s.platform_kashier_mid,
        api_key=s.platform_kashier_api_key,
        mode=s.platform_kashier_mode,
    )
    api_base = s.platform_api_base_url.rstrip("/")
    params = service.direct_payment_params(
        reference=reference,
        amount_cents=amount_cents,
        currency="EGP",
        description=description,
        webhook_url=f"{api_base}/api/v1/webhooks/kashier/platform/callback",
        redirect_url=redirect_url,
    )
    # The page the hub frames to collect the card (platform_pay.py). Built
    # here so the card is typed on the API origin, never the hub's.
    params["page_url"] = f"{api_base}/api/v1/platform-pay/card"
    return params
