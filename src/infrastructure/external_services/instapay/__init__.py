"""InstaPay — back-compat surface over the shared manual-transfer rails.

The implementation now lives in
:mod:`src.infrastructure.external_services.manual_transfer`, which
serves both InstaPay and Vodafone Cash from one code path. These
re-exports keep existing imports resolving.
"""

from src.infrastructure.external_services.manual_transfer.payment_service import (
    InstapayPaymentService,
    ManualTransferPaymentService,
    get_merchant_instapay_credentials,
    get_merchant_manual_credentials,
)

__all__ = [
    "InstapayPaymentService",
    "ManualTransferPaymentService",
    "get_merchant_instapay_credentials",
    "get_merchant_manual_credentials",
]
