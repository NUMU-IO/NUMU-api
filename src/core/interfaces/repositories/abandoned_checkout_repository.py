"""AbandonedCheckout repository interface."""

from abc import abstractmethod
from datetime import datetime
from uuid import UUID

from src.core.entities.abandoned_checkout import AbandonedCheckout
from src.core.interfaces.repositories.base import BaseRepository


class IAbandonedCheckoutRepository(BaseRepository[AbandonedCheckout]):
    """Repository contract for persisted abandoned checkouts."""

    @abstractmethod
    async def list_by_store(
        self,
        store_id: UUID,
        skip: int = 0,
        limit: int = 50,
        abandoned_only: bool = True,
        recovered_only: bool | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        has_contact: bool | None = None,
    ) -> tuple[list[AbandonedCheckout], int]:
        """Return (items, total_count) for a store's checkouts, newest first.

        `has_contact=True` filters to rows with email OR phone set (the
        recoverable subset — Shopify's "abandoned checkouts" semantics).
        `has_contact=False` filters to rows with NEITHER (browse-only cart
        adds). `None` returns everything.
        """
        ...

    @abstractmethod
    async def mark_recovery_email_sent(
        self, checkout_id: UUID, when: datetime
    ) -> AbandonedCheckout:
        """Stamp `recovery_email_sent_at`. Idempotent overwrite."""
        ...

    @abstractmethod
    async def mark_recovered(
        self,
        checkout_id: UUID,
        order_id: UUID | None = None,
        when: datetime | None = None,
    ) -> AbandonedCheckout:
        """Mark a cart as recovered (manually or after a conversion)."""
        ...

    @abstractmethod
    async def find_active_for_session(
        self,
        store_id: UUID,
        session_fingerprint: str | None,
        email: str | None,
    ) -> AbandonedCheckout | None:
        """Return the most recent un-recovered cart for the (session, email).

        Used by the storefront /cart/track upsert path and by the
        /checkout success path (to flip `recovered_at`).
        """
        ...

    @abstractmethod
    async def mark_stale_as_abandoned(
        self, store_id: UUID, threshold_seconds: int
    ) -> int:
        """Flip `abandoned_at` on rows whose `last_activity_at` is older
        than `threshold_seconds` AND `abandoned_at` is still null AND the
        cart is not already recovered. Returns the number of rows updated.

        Called lazily by the merchant list endpoint so the merchant sees
        recently-abandoned rows without us running a separate Celery job.
        """
        ...
