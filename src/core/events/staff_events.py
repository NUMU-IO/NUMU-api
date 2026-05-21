"""Staff-related events for the event bus."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class StaffInvitedEvent:
    """Event fired when a staff invitation is created."""

    invitation_id: str
    tenant_id: str
    email: str
    role_ids: list[str]
    invited_by_id: str

    @property
    def event_type(self) -> str:
        return "staff.invited"


@dataclass
class StaffActivatedEvent:
    """Event fired when a staff member accepts invitation and activates."""

    membership_id: str
    user_id: str
    tenant_id: str
    role_ids: list[str]

    @property
    def event_type(self) -> str:
        return "staff.activated"


@dataclass
class StaffRoleAssignedEvent:
    """Event fired when a role is assigned to a membership."""

    membership_id: str
    role_id: str
    assigned_by_id: str

    @property
    def event_type(self) -> str:
        return "staff.role_assigned"


@dataclass
class StaffRoleRevokedEvent:
    """Event fired when a role is revoked from a membership."""

    membership_id: str
    role_id: str
    revoked_by_id: str

    @property
    def event_type(self) -> str:
        return "staff.role_revoked"


@dataclass
class StaffAccessRevokedEvent:
    """Event fired when a staff member's access is revoked."""

    membership_id: str
    reason: str
    revoked_by_id: str

    @property
    def event_type(self) -> str:
        return "staff.access_revoked"


@dataclass
class StaffPermissionsChangedEvent:
    """Event fired when permissions change for a membership."""

    membership_id: str
    added: list[str]
    removed: list[str]

    @property
    def event_type(self) -> str:
        return "staff.permissions_changed"


@dataclass
class StaffSessionRevokedEvent:
    """Event fired when a staff session is revoked."""

    session_id: str
    user_id: str
    reason: str

    @property
    def event_type(self) -> str:
        return "staff.session_revoked"


@dataclass
class StaffSuspiciousActivityEvent:
    """Event fired when suspicious activity is detected."""

    membership_id: str
    signal: str
    details: dict

    @property
    def event_type(self) -> str:
        return "staff.suspicious_activity"


@dataclass
class RoleCreatedEvent:
    """Event fired when a role is created."""

    role_id: str
    tenant_id: str | None
    diff: dict

    @property
    def event_type(self) -> str:
        return "role.created"


@dataclass
class RoleUpdatedEvent:
    """Event fired when a role is updated."""

    role_id: str
    tenant_id: str | None
    diff: dict

    @property
    def event_type(self) -> str:
        return "role.updated"


@dataclass
class RoleDeletedEvent:
    """Event fired when a role is deleted."""

    role_id: str
    tenant_id: str | None

    @property
    def event_type(self) -> str:
        return "role.deleted"


@dataclass
class AccessRequestCreatedEvent:
    """Event fired when an access request is created."""

    request_id: str
    tenant_id: str
    requester_user_id: str
    requested_role_ids: list[str]
    requested_permissions: list[str]

    @property
    def event_type(self) -> str:
        return "access_request.created"


@dataclass
class AccessRequestApprovedEvent:
    """Event fired when an access request is approved."""

    request_id: str
    tenant_id: str
    requester_user_id: str
    reviewer_user_id: str
    approved_role_ids: list[str]
    approved_permissions: list[str]

    @property
    def event_type(self) -> str:
        return "access_request.approved"


@dataclass
class AccessRequestDeniedEvent:
    """Event fired when an access request is denied."""

    request_id: str
    tenant_id: str
    requester_user_id: str
    reviewer_user_id: str
    reason: str | None

    @property
    def event_type(self) -> str:
        return "access_request.denied"


@dataclass
class PermissionOverrideSetEvent:
    """Event fired when a permission override is set."""

    membership_id: str
    permission_id: str
    effect: str
    granted_by_id: str | None

    @property
    def event_type(self) -> str:
        return "permission_override.set"


@dataclass
class PermissionOverrideClearedEvent:
    """Event fired when a permission override is cleared."""

    membership_id: str
    permission_id: str

    @property
    def event_type(self) -> str:
        return "permission_override.cleared"


@dataclass
class TemporaryAccessGrantedEvent:
    """Event fired when temporary access is granted."""

    membership_id: str
    grant_id: str
    permission_ids: list[str]
    requester_user_id: str
    expires_at: datetime

    @property
    def event_type(self) -> str:
        return "temporary_access.granted"


@dataclass
class TemporaryAccessRevokedEvent:
    """Event fired when temporary access is revoked."""

    membership_id: str
    grant_id: str
    permission_ids: list[str]
    revoked_by_id: str

    @property
    def event_type(self) -> str:
        return "temporary_access.revoked"


@dataclass
class StaffRemovedEvent:
    """Event fired when a staff member is removed."""

    membership_id: str
    removed_by_id: str
    reason: str | None

    @property
    def event_type(self) -> str:
        return "staff.removed"


@dataclass
class TenantOwnershipTransferredEvent:
    """Event fired when tenant ownership is transferred."""

    tenant_id: str
    old_owner_id: str
    new_owner_id: str
    transferred_by_id: str

    @property
    def event_type(self) -> str:
        return "tenant.ownership_transferred"


STAFF_EVENT_TYPES = {
    StaffInvitedEvent,
    StaffActivatedEvent,
    StaffRoleAssignedEvent,
    StaffRoleRevokedEvent,
    StaffAccessRevokedEvent,
    StaffRemovedEvent,
    StaffPermissionsChangedEvent,
    StaffSessionRevokedEvent,
    StaffSuspiciousActivityEvent,
    RoleCreatedEvent,
    RoleUpdatedEvent,
    RoleDeletedEvent,
    AccessRequestCreatedEvent,
    AccessRequestApprovedEvent,
    AccessRequestDeniedEvent,
    PermissionOverrideSetEvent,
    PermissionOverrideClearedEvent,
    TemporaryAccessGrantedEvent,
    TemporaryAccessRevokedEvent,
    TenantOwnershipTransferredEvent,
}
