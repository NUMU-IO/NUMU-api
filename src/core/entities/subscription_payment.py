"""Subscription payment (InstaPay) domain constants.

Stored values are plain strings in the DB (billing.py convention — no PG
enums); these enums are the app-side vocabulary. The intent lifecycle is
the manual-only subset of the wallet ``TopupIntentStatus`` machine: there
is no gateway leg, so ``pending`` never occurs.
"""

from enum import StrEnum

# Plans a merchant can pay for via InstaPay. payg has no upfront price
# (commission-funded) and enterprise is a custom contract — both excluded.
INSTAPAY_PAYABLE_PLANS = frozenset({"starter", "pro"})

BILLING_CYCLES = frozenset({"monthly", "annual"})


class SubscriptionPaymentPurpose(StrEnum):
    """What a successful payment does to the tenant."""

    NEW_SUBSCRIPTION = "new_subscription"  # activate plan (trial/read_only → active)
    RENEWAL = "renewal"  # extend next_renewal_at by one cycle


class SubscriptionPaymentIntentStatus(StrEnum):
    """Intent lifecycle (manual InstaPay only — no gateway 'pending' leg)."""

    AWAITING_PROOF = "awaiting_proof"  # created, awaiting receipt upload
    UNDER_REVIEW = "under_review"  # receipt uploaded, admin review
    SUCCEEDED = "succeeded"  # verified → subscription activated/renewed
    FAILED = "failed"
    EXPIRED = "expired"


# Payment window: wallet top-ups use 30 min, subscriptions get 60 —
# amounts up to 4,990 EGP (Pro annual) exceed many default instant-
# transfer limits, so merchants may need to raise their bank-app limit
# mid-flow. Same 15-min expiry sweep either way.
SUBSCRIPTION_PAYMENT_EXPIRY_MINUTES = 60
