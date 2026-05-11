"""Gift card entity — Phase 8.3.

Gift cards are **tender** (a payment method), NOT discount coupons:

* Sale of a gift card is **tax-exempt** — it's the purchase of a
  cash-equivalent instrument, not a taxable good.
* Redemption at checkout **reduces amount_due** without altering
  tax math — the customer still pays VAT on the underlying goods;
  the gift card just covers part of the bill.
* Tracked in a ledger (`GiftCardTransaction`) so audit, refund, and
  chargeback flows can trace every cents-level movement.

Codes are 16-character base32 (Crockford-safe, no `1`/`I` / `0`/`O`
confusion), generated server-side and stored hashed via SHA-256. The
plaintext is only ever visible at issuance time — every subsequent
lookup goes through `code_hash`. Last four chars stay in plaintext on
the row so the hub can render "GC-•••••-XYZW" in lists.

Lookup pattern (storefront balance check, checkout redemption):
    code → sha256(normalize(code)) → SELECT WHERE code_hash = ?
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class GiftCardStatus(StrEnum):
    ACTIVE = "active"
    DEPLETED = "depleted"  # current_balance reached zero
    EXPIRED = "expired"  # past expires_at
    VOIDED = "voided"  # merchant-issued cancellation (e.g. fraud)


def normalize_code(code: str) -> str:
    """Strip whitespace + dashes, uppercase. Customers type
    `GC-XXXX-YYYY-ZZZZ` and we match against `GCXXXXYYYYZZZZ`."""
    return "".join(c for c in code.upper() if c.isalnum())


def hash_code(code: str) -> str:
    """SHA-256 of normalized code. Stored alongside `last_four` so
    full plaintext is never persisted."""
    return hashlib.sha256(normalize_code(code).encode("utf-8")).hexdigest()


class GiftCard(BaseEntity):
    tenant_id: UUID
    store_id: UUID
    code_hash: str  # SHA-256 of normalized code
    last_four: str  # plaintext suffix for hub display
    initial_balance_cents: int = Field(ge=0)
    current_balance_cents: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    status: GiftCardStatus = GiftCardStatus.ACTIVE
    customer_id: UUID | None = None  # owner if issued to a specific person
    issued_by_user_id: UUID | None = None  # merchant staff member
    # Tied to the order that purchased it, when applicable. NULL when
    # the gift card was manually issued by the merchant (promo gift).
    issuing_order_id: UUID | None = None
    expires_at: datetime | None = None
    note: str | None = None  # merchant-facing memo

    def is_redeemable(self, now: datetime | None = None) -> bool:
        if self.status != GiftCardStatus.ACTIVE:
            return False
        if self.current_balance_cents <= 0:
            return False
        if self.expires_at is not None:
            check = now or datetime.utcnow()
            # Strip tz to compare naive against naive — both sides are UTC.
            ex = (
                self.expires_at.replace(tzinfo=None)
                if self.expires_at.tzinfo
                else self.expires_at
            )
            ch = check.replace(tzinfo=None) if check.tzinfo else check
            if ex <= ch:
                return False
        return True


class TransactionKind(StrEnum):
    ISSUE = "issue"  # initial credit when card is purchased / created
    REDEEM = "redeem"  # debit at checkout
    REFUND = "refund"  # credit back from an order refund
    ADJUST = "adjust"  # merchant-driven manual adjustment
    VOID = "void"  # zero out remaining balance (terminal)


class GiftCardTransaction(BaseEntity):
    """Append-only ledger of every cent movement on a gift card."""

    tenant_id: UUID
    store_id: UUID
    gift_card_id: UUID
    kind: TransactionKind
    # Signed delta in cents. Issue/refund/adjust-up = positive; redeem
    # / void / adjust-down = negative. Sum of deltas equals
    # current_balance_cents at any point in time.
    amount_cents: int
    # Order this movement was tied to (NULL for manual adjust/void).
    order_id: UUID | None = None
    # Whose hands the movement passed through (customer for redeem,
    # staff for issue/adjust/void). NULL for system-driven flows.
    actor_user_id: UUID | None = None
    actor_customer_id: UUID | None = None
    note: str | None = None
