"""Update profile use case."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from src.core.exceptions import EntityNotFoundError
from src.core.interfaces.repositories.user_repository import IUserRepository
from src.core.value_objects.phone import PhoneNumber


@dataclass
class UpdateProfileDTO:
    """Update profile data transfer object."""

    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    avatar_url: str | None = None


@dataclass
class UserProfileDTO:
    """User profile data transfer object."""

    id: UUID
    email: str
    first_name: str
    last_name: str
    full_name: str
    phone: str | None
    role: str
    status: str
    avatar_url: str | None
    is_verified: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UpdateProfileUseCase:
    """Use case for updating user profile."""

    def __init__(self, user_repository: IUserRepository) -> None:
        self.user_repository = user_repository

    async def execute(
        self,
        user_id: UUID,
        dto: UpdateProfileDTO,
    ) -> UserProfileDTO:
        """Update user profile."""
        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise EntityNotFoundError("User", str(user_id))

        # Update fields if provided
        if dto.first_name is not None:
            user.first_name = dto.first_name

        if dto.last_name is not None:
            user.last_name = dto.last_name

        if dto.phone is not None:
            user.phone = PhoneNumber(value=dto.phone) if dto.phone else None

        if dto.avatar_url is not None:
            user.avatar_url = dto.avatar_url if dto.avatar_url else None

        user.touch()

        # Save updated user
        updated_user = await self.user_repository.update(user)

        return UserProfileDTO(
            id=updated_user.id,
            email=updated_user.email.value,
            first_name=updated_user.first_name,
            last_name=updated_user.last_name,
            full_name=updated_user.full_name,
            phone=updated_user.phone.value if updated_user.phone else None,
            role=updated_user.role.value,
            status=updated_user.status.value,
            avatar_url=updated_user.avatar_url,
            is_verified=updated_user.is_verified,
            is_active=updated_user.is_active,
            created_at=updated_user.created_at,
            updated_at=updated_user.updated_at,
        )
