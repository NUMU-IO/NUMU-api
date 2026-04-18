"""Product review repository interface."""

from abc import ABC, abstractmethod
from uuid import UUID

from src.core.entities.product_review import ProductReview


class ReviewStats:
    """Aggregate review statistics for a product."""

    def __init__(
        self,
        average: float,
        count: int,
        distribution: dict[int, int],
    ) -> None:
        self.average = average
        self.count = count
        # Distribution keyed by star rating (1..5) → count at that rating
        self.distribution = distribution


class IProductReviewRepository(ABC):
    """Repository for product reviews."""

    @abstractmethod
    async def create(self, review: ProductReview) -> ProductReview:
        """Persist a new review and return it with its id."""
        ...

    @abstractmethod
    async def list_for_product(
        self,
        product_id: UUID,
        approved_only: bool = True,
        skip: int = 0,
        limit: int = 50,
    ) -> list[ProductReview]:
        """List reviews for a product, newest first."""
        ...

    @abstractmethod
    async def stats_for_product(
        self,
        product_id: UUID,
        approved_only: bool = True,
    ) -> ReviewStats:
        """Return aggregate stats (average, count, star distribution)."""
        ...

    @abstractmethod
    async def customer_has_reviewed(
        self,
        product_id: UUID,
        customer_id: UUID,
    ) -> bool:
        """True if this customer already posted a review for this product."""
        ...
