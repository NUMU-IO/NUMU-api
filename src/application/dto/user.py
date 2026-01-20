"""User DTOs."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from src.application.dto.base import BaseDTO
from src.core.entities.user import User, UserRole, UserStatus


@dataclass
class UserDTO(BaseDTO):
    """User data transfer object."""

    id: UUID
    email: str
    first_name: str
    last_name: str
    full_name: str
    role: str
    status: str
    phone: str | None
    avatar_url: str | None
    is_verified: bool
    created_at: datetime
    updated_at: datetime
    is_active: bool

    @classmethod
    def from_entity(cls, entity: User) -> "UserDTO":
        """Create DTO from User entity."""
        return cls(
            id=entity.id,
            email=str(entity.email),
            first_name=entity.first_name,
            last_name=entity.last_name,
            full_name=entity.full_name,
            role=entity.role.value,
            status=entity.status.value,
            phone=str(entity.phone) if entity.phone else None,
            avatar_url=entity.avatar_url,
            is_verified=entity.is_verified,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            is_active=entity.is_active,
        )


@dataclass
class CreateUserDTO(BaseDTO):
    """Create user data transfer object."""

    email: str
    password: str
    first_name: str
    last_name: str
    phone: str | None = None
    role: UserRole = UserRole.CUSTOMER


@dataclass
class UpdateUserDTO(BaseDTO):
    """Update user data transfer object."""

    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    avatar_url: str | None = None
