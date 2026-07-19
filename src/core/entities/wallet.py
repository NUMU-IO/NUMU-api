"""Merchant wallet domain constants (pay-as-you-go commission tier).

The wallet is a platform-level prepaid balance NUMU debits its per-order
commission from. Stored values are plain strings in the DB (billing.py
convention — no PG enums), these enums are the app-side vocabulary.
"""

from enum import StrEnum


class WalletStatus(StrEnum):
    """Lifecycle of a merchant wallet."""

    ACTIVE = "active"
    SUSPENDED = "suspended"  # admin-frozen: no debits/credits accepted
    EXEMPT = "exempt"  # internal/whitelabel tenants: no commission, no gate


class WalletTransactionKind(StrEnum):
    """Ledger entry kinds. amount_cents is signed per kind."""

    TOPUP = "topup"  # +
    COMMISSION = "commission"  # -
    COMMISSION_REVERSAL = "commission_reversal"  # +
    ADJUSTMENT = "adjustment"  # +/- (admin, audited via actor_user_id)


class TopupMethod(StrEnum):
    """How a top-up intent is paid.

    ``CARD`` is gateway-backed (Kashier hosted session on NUMU's platform
    account — instant webhook credit). ``VODAFONE_CASH`` and ``INSTAPAY``
    are manual: the merchant transfers to NUMU's own wallet number / IPA
    and uploads a receipt (auto-verified, else held for admin review).
    """

    CARD = "card"
    VODAFONE_CASH = "vodafone_cash"
    INSTAPAY = "instapay"


# Manual (proof-upload) methods share the receipt/auto-approval pipeline.
MANUAL_TOPUP_METHODS = frozenset({
    TopupMethod.VODAFONE_CASH.value,
    TopupMethod.INSTAPAY.value,
})


class TopupIntentStatus(StrEnum):
    """Top-up intent lifecycle."""

    PENDING = "pending"  # Paymob: awaiting gateway webhook
    AWAITING_PROOF = "awaiting_proof"  # InstaPay: awaiting receipt upload
    UNDER_REVIEW = "under_review"  # InstaPay: soft-blocked, admin review
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"


# Warning-ladder levels persisted on merchant_wallets.last_warning_level.
# 0 = healthy, 1 = low balance, 2 = negative, 3 = below allowance (blocked).
WARNING_LEVEL_NONE = 0
WARNING_LEVEL_LOW = 1
WARNING_LEVEL_NEGATIVE = 2
WARNING_LEVEL_BLOCKED = 3
