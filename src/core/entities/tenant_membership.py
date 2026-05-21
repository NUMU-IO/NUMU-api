"""Tenant membership entity representing user × tenant joins with roles."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class MembershipStatus(StrEnum):
    """Status of tenant membership."""

    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


@dataclass
class TenantMembership:
    """Tenant membership entity.

    Connects users to tenants with roles, status, and permission version tracking.
    This is the core of the multi-tenant authorization system.
    """

    id: str
    user_id: str
    tenant_id: str
    status: MembershipStatus = MembershipStatus.INVITED
    is_owner: bool = False
    invited_by_id: str | None = None
    invited_at: datetime | None = None
    joined_at: datetime | None = None
    last_active_at: datetime | None = None
    permission_version: int = 1
    two_factor_required: bool = False
    deleted_at: datetime | None = None
    role_ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_active(self) -> bool:
        """Check if membership is active."""
        return self.status == MembershipStatus.ACTIVE

    @property
    def can_manage_staff(self) -> bool:
        """Check if can manage staff."""
        return self.is_owner or "staff.invite" in self.role_ids

    @property
    def is_suspended(self) -> bool:
        """Check if membership is suspended."""
        return self.status == MembershipStatus.SUSPENDED

    @property
    def is_revoked(self) -> bool:
        """Check if membership is revoked."""
        return self.status == MembershipStatus.REVOKED

    def activate(self) -> None:
        """Activate the membership."""
        self.status = MembershipStatus.ACTIVE
        self.joined_at = datetime.utcnow()

    def suspend(self) -> None:
        """Suspend the membership."""
        self.status = MembershipStatus.SUSPENDED

    def revoke(self) -> None:
        """Revoke the membership."""
        self.status = MembershipStatus.REVOKED

    def bump_version(self) -> None:
        """Bump permission version for cache invalidation."""
        self.permission_version += 1


@dataclass
class EffectivePermissions:
    """Effective permissions for a membership.

    Computed from roles, overrides, and temporary grants.
    Cached in Redis with permission_version key.
    """

    tenant_id: str
    user_id: str
    membership_id: str
    is_owner: bool
    allowed: frozenset[str]
    wildcards: frozenset[str]
    denied: frozenset[str]
    scopes: dict[str, list[dict]]
    version: int

    def has_permission(self, code: str) -> bool:
        """Check if permission is allowed."""
        if self.is_owner:
            return True
        if code in self.denied:
            return False
        if code in self.allowed:
            return True
        for wildcard in self.wildcards:
            if code.startswith(wildcard.rstrip("*")):
                return True
        return False

    def get_scope(self, code: str) -> list[dict] | None:
        """Get scope for a permission code."""
        return self.scopes.get(code)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "membership_id": self.membership_id,
            "is_owner": self.is_owner,
            "allowed": sorted(self.allowed),
            "wildcards": sorted(self.wildcards),
            "denied": sorted(self.denied),
            "scopes": self.scopes,
            "version": self.version,
        }
