"""Delete coupon use case."""

from uuid import UUID

from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.coupon_repository import ICouponRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository


class DeleteCouponUseCase:
    """Use case for deleting a coupon."""

    def __init__(
        self,
        coupon_repository: ICouponRepository,
        store_repository: IStoreRepository,
    ) -> None:
        self.coupon_repository = coupon_repository
        self.store_repository = store_repository

    async def execute(self, coupon_id: UUID, user_id: UUID) -> bool:
        """Delete a coupon.

        Args:
            coupon_id: The coupon UUID.
            user_id: The user UUID (for authorization).

        Returns:
            True if deleted.

        Raises:
            EntityNotFoundError: If coupon or store not found.
            AuthorizationError: If user doesn't own the store.
        """
        coupon = await self.coupon_repository.get_by_id(coupon_id)
        if not coupon:
            raise EntityNotFoundError("Coupon", str(coupon_id))

        # Verify store ownership
        store = await self.store_repository.get_by_id(coupon.store_id)
        if not store or store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to delete this coupon"
            )

        return await self.coupon_repository.delete(coupon_id)
