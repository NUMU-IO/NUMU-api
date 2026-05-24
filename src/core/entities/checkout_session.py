"""CheckoutSession entity — a short-lived (30-min) token issued at the
checkout Contact step.

Used to authenticate phone-bound, anonymous storefront calls — initially
``POST /storefront/{store_slug}/whatsapp/opt-in`` (FR-007a). General-purpose:
future endpoints (abandoned-checkout recovery, push-token registration)
will reuse the same token.

Lives in Redis only — see ``CheckoutSessionRepository``.
"""

from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict

from src.core.entities.base import BaseEntity


class CheckoutSession(BaseEntity):
    """A phone-bound, short-lived storefront authorization token."""

    model_config = ConfigDict(
        validate_assignment=True,
        from_attributes=True,
        populate_by_name=True,
    )

    token: UUID
    """Opaque to the caller; the Redis key."""

    cart_session_id: str
    """Links to the customer's ``numu_cart_session`` cookie value."""

    store_id: UUID
    """Tenant the cart belongs to."""

    phone: str
    """E.164 phone the customer entered at the Contact step. Compared
    against the phone supplied to phone-bound endpoints (FR-007a)."""

    issued_at: datetime
    expires_at: datetime
