"""Manual ("push payment") rails — InstaPay and Vodafone Cash.

The rail-neutral home for the out-of-band payment flow. New code should
import from here; :mod:`src.infrastructure.external_services.instapay`
survives as a thin back-compat shim over the same objects.

``auto_approval`` is re-exported rather than moved: the rules engine was
already method-agnostic (it takes resolved facts, not a provider), so
relocating the file would churn imports without changing behaviour.
"""

from src.infrastructure.external_services.instapay.auto_approval import (
    AutoApprovalConfig,
    AutoApprovalDecision,
    AutoApprovalFacts,
    evaluate,
)
from src.infrastructure.external_services.manual_transfer.destinations import (
    InvalidDestinationError,
    mask_destination,
    normalize_destination,
    normalize_ipa,
    normalize_wallet_number,
)
from src.infrastructure.external_services.manual_transfer.payment_service import (
    DEFAULT_AUTO_APPROVE_DAILY_CAP_CENTS,
    DEFAULT_AUTO_APPROVE_DAILY_COUNT,
    DEFAULT_AUTO_APPROVE_THRESHOLD_CENTS,
    DEFAULT_EXPIRY_MINUTES,
    DEFAULT_VC_AMOUNT_TOLERANCE_BPS,
    MANUAL_TRANSFER_METHODS,
    InstapayPaymentService,
    ManualTransferPaymentService,
    default_amount_tolerance_bps,
    generate_reference_code,
    get_merchant_instapay_credentials,
    get_merchant_manual_credentials,
    human_name,
    resume_url,
    route_segment,
    settings_key,
)

__all__ = [
    "DEFAULT_AUTO_APPROVE_DAILY_CAP_CENTS",
    "DEFAULT_AUTO_APPROVE_DAILY_COUNT",
    "DEFAULT_AUTO_APPROVE_THRESHOLD_CENTS",
    "DEFAULT_EXPIRY_MINUTES",
    "DEFAULT_VC_AMOUNT_TOLERANCE_BPS",
    "MANUAL_TRANSFER_METHODS",
    "AutoApprovalConfig",
    "AutoApprovalDecision",
    "AutoApprovalFacts",
    "InstapayPaymentService",
    "InvalidDestinationError",
    "ManualTransferPaymentService",
    "default_amount_tolerance_bps",
    "evaluate",
    "generate_reference_code",
    "get_merchant_instapay_credentials",
    "get_merchant_manual_credentials",
    "human_name",
    "mask_destination",
    "normalize_destination",
    "normalize_ipa",
    "normalize_wallet_number",
    "resume_url",
    "route_segment",
    "settings_key",
]
