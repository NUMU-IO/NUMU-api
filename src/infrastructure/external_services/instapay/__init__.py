"""InstaPay (Egypt) payment integration — manual IPA + proof verification."""

from src.infrastructure.external_services.instapay.payment_service import (
    InstapayPaymentService,
    get_merchant_instapay_credentials,
)

__all__ = ["InstapayPaymentService", "get_merchant_instapay_credentials"]
