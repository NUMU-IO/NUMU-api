"""Domain events for the InstaPay proof-verification flow.

Kept in a separate module from ``order_events`` because they're
lifecycle events of a *payment proof* rather than the order itself —
they fire alongside the existing order events (approval also fires
``OrderPaidEvent``), but carry the proof-specific payload that
downstream email/WhatsApp handlers need.
"""

from __future__ import annotations

from uuid import UUID

from src.core.events.base import DomainEvent


class PaymentProofApprovedEvent(DomainEvent):
    """Emitted when a payment proof is approved (auto or manual).

    Fires *in addition to* ``OrderPaidEvent`` so handlers can send a
    short "payment received" confirmation email without waiting for
    (or depending on) invoice generation — if PDF rendering fails, the
    customer still gets a signal that their money arrived.

    Carries the IDs the handler needs to resolve customer/store from
    the DB on its own session; we intentionally don't pre-fetch email
    here so the publisher (use case) stays lean.
    """

    proof_id: UUID
    order_id: UUID
    order_number: str
    tenant_id: UUID
    store_id: UUID
    customer_id: UUID
    reference_code: str
    amount_cents: int
    currency: str = "EGP"
    auto_approved: bool = False


class PaymentProofRejectedEvent(DomainEvent):
    """Emitted when a merchant rejects a customer-uploaded proof.

    Drives the customer notification: the proof is rejected, here's why,
    and — if the intent hasn't expired yet — a CTA to re-upload. The
    ``can_retry`` flag tells handlers which copy variant to render.
    """

    proof_id: UUID
    order_id: UUID
    order_number: str
    tenant_id: UUID
    store_id: UUID
    customer_id: UUID
    reference_code: str
    rejection_reason: str
    can_retry: bool = True
    retry_url: str | None = None
