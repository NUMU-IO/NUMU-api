"""User entity representing an authenticated user in the system."""

from datetime import datetime
from enum import Enum

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
    """User entity representing an authenticated user.

    Users can have different roles:
    - CUSTOMER: End customers who browse and purchase
    - STORE_OWNER: Owners who manage stores and products
    - STORE_ADMIN: Administrators with elevated store permissions
    - STORE_STAFF: Staff members with limited permissions
    - SUPER_ADMIN: Platform-wide administrators
    """

    email: Email
    hashed_password: str
    first_name: str
    last_name: str
    role: UserRole = UserRole.CUSTOMER
    status: UserStatus = UserStatus.PENDING_VERIFICATION
    phone: PhoneNumber | None = None
    avatar_url: str | None = None
    email_verified_at: datetime | None = None
    last_login_at: datetime | None = None

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

    @property
    def is_store_owner(self) -> bool:
        """Check if user is a store owner."""
        return self.role == UserRole.STORE_OWNER

    @property
    def is_admin(self) -> bool:
        """Check if user has admin privileges."""
        return self.role in (UserRole.SUPER_ADMIN, UserRole.STORE_ADMIN)

    @property
    def is_super_admin(self) -> bool:
        """Check if user is a super admin."""
        return self.role == UserRole.SUPER_ADMIN

    def verify_email(self) -> None:
        """Mark user email as verified and activate account."""
        self.email_verified_at = datetime.utcnow()
        self.status = UserStatus.ACTIVE
        self.touch()

    def update_last_login(self) -> None:
        """Update last login timestamp."""
        self.last_login_at = datetime.utcnow()
        self.touch()

    def suspend(self, reason: str | None = None) -> None:
        """Suspend the user account."""
        self.status = UserStatus.SUSPENDED
        self.touch()

    def activate(self) -> None:
        """Activate the user account."""
        self.status = UserStatus.ACTIVE
        self.touch()

    def deactivate(self) -> None:
        """Deactivate the user account."""
        self.status = UserStatus.INACTIVE
        self.touch()

    def can_manage_store(self) -> bool:
        """Check if user can manage stores."""
        return self.role in (
            UserRole.STORE_OWNER,
            UserRole.STORE_ADMIN,
            UserRole.SUPER_ADMIN,
        )

    def has_permission(self, required_role: UserRole) -> bool:
        """Check if user has at least the required role level.

        Role hierarchy (lowest to highest):
        CUSTOMER < STORE_STAFF < STORE_ADMIN < STORE_OWNER < SUPER_ADMIN
        """
        role_hierarchy = {
            UserRole.CUSTOMER: 0,
            UserRole.STORE_STAFF: 1,
            UserRole.STORE_ADMIN: 2,
            UserRole.STORE_OWNER: 3,
            UserRole.SUPER_ADMIN: 4,
        }
        return role_hierarchy.get(self.role, 0) >= role_hierarchy.get(required_role, 0)
