"""User entity representing an authenticated user in the system."""

from datetime import datetime
from enum import Enum
from uuid import UUID

from src.core.entities.base import BaseEntity
from src.core.value_objects.email import Email
from src.core.value_objects.phone import PhoneNumber


class UserRole(str, Enum):
    """User role enumeration."""

    CUSTOMER = "customer"
    STORE_OWNER = "store_owner"
    STORE_ADMIN = "store_admin"
    STORE_STAFF = "store_staff"
    SUPER_ADMIN = "super_admin"


class UserStatus(str, Enum):
    """User status enumeration."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    PENDING_VERIFICATION = "pending_verification"


class User(BaseEntity):
    """User entity representing an authenticated user."""

    def __init__(
        self,
        email: Email,
        hashed_password: str,
        first_name: str,
        last_name: str,
        role: UserRole = UserRole.CUSTOMER,
        status: UserStatus = UserStatus.PENDING_VERIFICATION,
        phone: PhoneNumber | None = None,
        avatar_url: str | None = None,
        email_verified_at: datetime | None = None,
        last_login_at: datetime | None = None,
        id: UUID | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> None:
        super().__init__(id=id, created_at=created_at, updated_at=updated_at)
        self.email = email
        self.hashed_password = hashed_password
        self.first_name = first_name
        self.last_name = last_name
        self.role = role
        self.status = status
        self.phone = phone
        self.avatar_url = avatar_url
        self.email_verified_at = email_verified_at
        self.last_login_at = last_login_at

    @property
    def full_name(self) -> str:
        """Get user's full name."""
        return f"{self.first_name} {self.last_name}"

    @property
    def is_verified(self) -> bool:
        """Check if user email is verified."""
        return self.email_verified_at is not None

    @property
    def is_active(self) -> bool:
        """Check if user is active."""
        return self.status == UserStatus.ACTIVE

    def verify_email(self) -> None:
        """Mark user email as verified."""
        self.email_verified_at = datetime.utcnow()
        self.status = UserStatus.ACTIVE
        self.updated_at = datetime.utcnow()

    def update_last_login(self) -> None:
        """Update last login timestamp."""
        self.last_login_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
