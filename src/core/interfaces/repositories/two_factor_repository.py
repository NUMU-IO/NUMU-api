"""Two-Factor Authentication repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.two_factor import TwoFactorAuth
from src.core.interfaces.repositories.base import BaseRepository


class ITwoFactorRepository(BaseRepository[TwoFactorAuth]):
    """Repository interface for Two-Factor Authentication entities.

    This repository handles CRUD operations for TwoFactorAuth entities,
    with additional methods for looking up 2FA by user ID.
    """

    @abstractmethod
    async def get_by_user_id(self, user_id: UUID) -> TwoFactorAuth | None:
        """Get 2FA configuration by user ID.

        Args:
            user_id: The UUID of the user

        Returns:
            TwoFactorAuth entity if found, None otherwise
        """
        ...

    @abstractmethod
    async def delete_by_user_id(self, user_id: UUID) -> bool:
        """Delete 2FA configuration by user ID.

        Args:
            user_id: The UUID of the user

        Returns:
            True if deleted, False if not found
        """
        ...

    @abstractmethod
    async def user_has_2fa_enabled(self, user_id: UUID) -> bool:
        """Check if a user has 2FA enabled.

        Args:
            user_id: The UUID of the user

        Returns:
            True if user has 2FA enabled, False otherwise
        """
        ...
