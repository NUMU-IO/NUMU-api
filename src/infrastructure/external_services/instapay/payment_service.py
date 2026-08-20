"""Back-compat shim — the implementation moved to ``manual_transfer``.

InstaPay was the first out-of-band rail NUMU supported; Vodafone Cash is
the second and uses the identical shape (publish a destination, customer
pushes funds, uploads a screenshot, OCR/merchant verifies). Rather than
clone this module the implementation was generalized into
:mod:`src.infrastructure.external_services.manual_transfer`.

Everything below is a re-export of the very same objects — there is one
implementation, not two. Prefer importing from ``manual_transfer`` in
new code; this module exists so the InstaPay-era imports scattered
across routes, use cases and tests keep resolving.
"""

from src.infrastructure.external_services.manual_transfer.payment_service import (
    DEFAULT_AUTO_APPROVE_DAILY_CAP_CENTS,
    DEFAULT_AUTO_APPROVE_DAILY_COUNT,
    DEFAULT_AUTO_APPROVE_THRESHOLD_CENTS,
    DEFAULT_EXPIRY_MINUTES,
    InstapayPaymentService,
    ManualTransferPaymentService,
    generate_reference_code,
    get_merchant_instapay_credentials,
    get_merchant_manual_credentials,
)

__all__ = [
    "DEFAULT_AUTO_APPROVE_DAILY_CAP_CENTS",
    "DEFAULT_AUTO_APPROVE_DAILY_COUNT",
    "DEFAULT_AUTO_APPROVE_THRESHOLD_CENTS",
    "DEFAULT_EXPIRY_MINUTES",
    "InstapayPaymentService",
    "ManualTransferPaymentService",
    "generate_reference_code",
    "get_merchant_instapay_credentials",
    "get_merchant_manual_credentials",
]
