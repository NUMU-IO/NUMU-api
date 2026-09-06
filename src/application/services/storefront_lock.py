"""Billing lock for storefronts whose tenant never converted.

A tenant that does not pay lands in ``read_only``: the trial expiry task
moves it there at 30 days, and so does a cancellation or a renewal that
exhausted its dunning retries (see ``TenantLifecycleState``). Until now
nothing enforced that state — ``require_writable_tenant`` exists but has
no call sites, so an expired trial kept serving shoppers indefinitely.

The lock is **derived** from the lifecycle column rather than stored as a
second flag. A stored flag would need its own sweep to set it, its own
hooks to clear it, and would drift out of step with the lifecycle state
the rest of billing already trusts. Deriving it means every existing
transition works unchanged: ``SubscribeUseCase`` already moves
``read_only → active`` when a merchant pays and their InstaPay proof
clears OCR, and the storefront unlocks on the next payload fetch.

Enforcement reuses the merchant's own pre-launch password gate: while the
lock holds, the public store payload reports the storefront as
password-protected. The storefront renders the gate it already has, so
this costs zero storefront changes. The merchant cannot switch this one
off — their hub writes ``settings.password_protected``, and the lock is
not read from there.

The password is derived from the store id and the platform secret rather
than stored, so there is no column to migrate, nothing to seed for the
stores that are already read-only, and the hub can show the merchant
their password at any time by asking for it.
"""

from __future__ import annotations

import hashlib
import hmac
from uuid import UUID

from src.config import settings

AWAITING_TOPUP = "awaiting_topup"
AWAITING_SUBSCRIPTION = "awaiting_subscription"

# ponytail: derived, so it cannot be rotated — a merchant who leaks it
# cannot pick a new one. Acceptable for a gate on a store that is not
# open for business yet; when rotation is wanted, store an override in
# `settings.password_protected` (the merchant's own gate) and prefer it.
_PASSWORD_CHARS = 10


def lock_reason(tenant) -> str | None:
    """Why this tenant's storefront is locked, or ``None`` if it is not.

    PAYG merchants fund the store with wallet top-ups instead of a
    subscription, so they are told to top up; everyone else is told to
    pay for their plan.
    """
    if tenant is None or tenant.is_writable:
        return None
    return (
        AWAITING_TOPUP
        if (tenant.plan or "").lower() == "payg"
        else AWAITING_SUBSCRIPTION
    )


def lock_password(store_id: UUID | str) -> str:
    """The password that opens a billing-locked storefront.

    Stable for the life of the store, and unguessable without the
    platform secret. Alphanumeric and lowercase — this gets read off a
    screen and typed on a phone.
    """
    digest = hmac.new(
        settings.session_secret_key.encode(),
        f"storefront-lock:{store_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return digest[:_PASSWORD_CHARS]


def lock_password_hash(store_id: UUID | str) -> str:
    """SHA-256 of :func:`lock_password`, matching the merchant gate's scheme."""
    return hashlib.sha256(lock_password(store_id).encode()).hexdigest()
