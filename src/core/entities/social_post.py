"""Social post entity representing a piece of content from a connected social account."""

from datetime import datetime
from uuid import UUID

from src.core.entities.base import BaseEntity


class SocialPost(BaseEntity):
    """Tracks a social media post and its import status."""

    social_connection_id: UUID
    store_id: UUID
    tenant_id: UUID | None = None
    platform_post_id: str
    image_url: str | None = None
    caption: str | None = None
    likes: int = 0
    comments: int = 0
    posted_at: datetime | None = None
    suggested_name: str | None = None
    suggested_name_ar: str | None = None
    suggested_price: int | None = None
    imported_at: datetime | None = None
    product_id: UUID | None = None

    @property
    def is_imported(self) -> bool:
        return self.imported_at is not None

    def mark_imported(self, product_id: UUID) -> None:
        """Mark this post as imported and link to the created product."""
        from datetime import UTC

        self.imported_at = datetime.now(UTC)
        self.product_id = product_id
        self.touch()
