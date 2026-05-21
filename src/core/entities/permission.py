"""Permission entity representing available system permissions."""

from dataclasses import dataclass
from enum import StrEnum


class PermissionScopeType(StrEnum):
    """Scope type for permission resolution."""

    ALL = "all"
    OWN = "own"
    ASSIGNED = "assigned"
    RESOURCE = "resource"


class PermissionRiskLevel(StrEnum):
    """Risk level for permission sensitivity."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Permission:
    """Permission entity representing an available action in the system.

    Permissions are seeded per deploy and define what users can do.
    They include domain.action[.qualifier] code and scope_type for resolution.
    """

    id: str
    code: str
    domain: str
    action: str
    qualifier: str | None = None
    scope_type: PermissionScopeType = PermissionScopeType.ALL
    description: str | None = None
    dependencies: tuple[str, ...] = ()
    risk_level: PermissionRiskLevel = PermissionRiskLevel.LOW
    is_app: bool = False
    plugin_id: str | None = None

    @property
    def requires_step_up(self) -> bool:
        """Check if this permission requires elevated verification."""
        return self.risk_level in (
            PermissionRiskLevel.HIGH,
            PermissionRiskLevel.CRITICAL,
        )


PERMISSION_CATALOG: dict[str, Permission] = {}


def register_permission(permission: Permission) -> None:
    """Register a permission in the global catalog."""
    PERMISSION_CATALOG[permission.code] = permission


def get_permission(code: str) -> Permission | None:
    """Get a permission by code."""
    return PERMISSION_CATALOG.get(code)


def get_permissions_by_domain(domain: str) -> list[Permission]:
    """Get all permissions in a domain."""
    return [p for p in PERMISSION_CATALOG.values() if p.domain == domain]


def resolve_dependencies(code: str) -> set[str]:
    """Resolve all dependencies for a permission code recursively."""
    perm = get_permission(code)
    if not perm:
        return set()
    result = set(perm.dependencies)
    for dep_code in perm.dependencies:
        result |= resolve_dependencies(dep_code)
    return result


def is_sensitive_action(code: str) -> bool:
    """Check if a permission code requires step-up verification."""
    perm = get_permission(code)
    if not perm:
        return False
    return perm.requires_step_up


DEFAULT_PERMISSIONS = (
    Permission(
        id="1",
        code="orders.view",
        domain="orders",
        action="view",
        scope_type=PermissionScopeType.ALL,
        description="View orders",
        risk_level=PermissionRiskLevel.LOW,
    ),
    Permission(
        id="2",
        code="orders.create",
        domain="orders",
        action="create",
        scope_type=PermissionScopeType.ALL,
        description="Create orders",
        risk_level=PermissionRiskLevel.MEDIUM,
    ),
    Permission(
        id="3",
        code="orders.edit",
        domain="orders",
        action="edit",
        scope_type=PermissionScopeType.OWN,
        description="Edit orders",
        dependencies=("orders.view",),
        risk_level=PermissionRiskLevel.MEDIUM,
    ),
    Permission(
        id="4",
        code="orders.cancel",
        domain="orders",
        action="cancel",
        scope_type=PermissionScopeType.OWN,
        description="Cancel orders",
        dependencies=("orders.view",),
        risk_level=PermissionRiskLevel.MEDIUM,
    ),
    Permission(
        id="5",
        code="orders.refund",
        domain="orders",
        action="refund",
        scope_type=PermissionScopeType.ASSIGNED,
        description="Issue refunds",
        dependencies=("orders.view",),
        risk_level=PermissionRiskLevel.HIGH,
    ),
    Permission(
        id="6",
        code="orders.export",
        domain="orders",
        action="export",
        scope_type=PermissionScopeType.ALL,
        description="Export orders",
        dependencies=("orders.view",),
        risk_level=PermissionRiskLevel.HIGH,
    ),
    Permission(
        id="7",
        code="orders.fulfill",
        domain="orders",
        action="fulfill",
        scope_type=PermissionScopeType.OWN,
        description="Fulfill orders",
        dependencies=("orders.view",),
        risk_level=PermissionRiskLevel.MEDIUM,
    ),
    Permission(
        id="8",
        code="products.view",
        domain="products",
        action="view",
        scope_type=PermissionScopeType.ALL,
        description="View products",
        risk_level=PermissionRiskLevel.LOW,
    ),
    Permission(
        id="9",
        code="products.create",
        domain="products",
        action="create",
        scope_type=PermissionScopeType.ALL,
        description="Create products",
        risk_level=PermissionRiskLevel.MEDIUM,
    ),
    Permission(
        id="10",
        code="products.edit",
        domain="products",
        action="edit",
        scope_type=PermissionScopeType.OWN,
        description="Edit products",
        dependencies=("products.view",),
        risk_level=PermissionRiskLevel.MEDIUM,
    ),
    Permission(
        id="11",
        code="products.edit.price",
        domain="products",
        action="edit.price",
        scope_type=PermissionScopeType.OWN,
        description="Edit product prices",
        dependencies=("products.view", "products.edit"),
        risk_level=PermissionRiskLevel.HIGH,
    ),
    Permission(
        id="12",
        code="products.delete",
        domain="products",
        action="delete",
        scope_type=PermissionScopeType.OWN,
        description="Delete products",
        dependencies=("products.view",),
        risk_level=PermissionRiskLevel.HIGH,
    ),
    Permission(
        id="13",
        code="products.publish",
        domain="products",
        action="publish",
        scope_type=PermissionScopeType.OWN,
        description="Publish products",
        dependencies=("products.view",),
        risk_level=PermissionRiskLevel.MEDIUM,
    ),
    Permission(
        id="14",
        code="inventory.view",
        domain="inventory",
        action="view",
        scope_type=PermissionScopeType.ALL,
        description="View inventory",
        risk_level=PermissionRiskLevel.LOW,
    ),
    Permission(
        id="15",
        code="inventory.adjust",
        domain="inventory",
        action="adjust",
        scope_type=PermissionScopeType.ALL,
        description="Adjust inventory",
        risk_level=PermissionRiskLevel.MEDIUM,
    ),
    Permission(
        id="16",
        code="inventory.transfer",
        domain="inventory",
        action="transfer",
        scope_type=PermissionScopeType.ALL,
        description="Transfer inventory",
        risk_level=PermissionRiskLevel.MEDIUM,
    ),
    Permission(
        id="17",
        code="customers.view",
        domain="customers",
        action="view",
        scope_type=PermissionScopeType.ALL,
        description="View customers",
        risk_level=PermissionRiskLevel.LOW,
    ),
    Permission(
        id="18",
        code="customers.edit",
        domain="customers",
        action="edit",
        scope_type=PermissionScopeType.OWN,
        description="Edit customers",
        dependencies=("customers.view",),
        risk_level=PermissionRiskLevel.MEDIUM,
    ),
    Permission(
        id="19",
        code="customers.export",
        domain="customers",
        action="export",
        scope_type=PermissionScopeType.ALL,
        description="Export customers",
        dependencies=("customers.view",),
        risk_level=PermissionRiskLevel.HIGH,
    ),
    Permission(
        id="20",
        code="customers.delete",
        domain="customers",
        action="delete",
        scope_type=PermissionScopeType.ALL,
        description="Delete customers",
        dependencies=("customers.view",),
        risk_level=PermissionRiskLevel.CRITICAL,
    ),
    Permission(
        id="21",
        code="analytics.view",
        domain="analytics",
        action="view",
        scope_type=PermissionScopeType.ALL,
        description="View analytics",
        risk_level=PermissionRiskLevel.LOW,
    ),
    Permission(
        id="22",
        code="analytics.export",
        domain="analytics",
        action="export",
        scope_type=PermissionScopeType.ALL,
        description="Export analytics",
        dependencies=("analytics.view",),
        risk_level=PermissionRiskLevel.HIGH,
    ),
    Permission(
        id="23",
        code="analytics.financial.view",
        domain="analytics",
        action="financial.view",
        scope_type=PermissionScopeType.ALL,
        description="View financial analytics",
        dependencies=("analytics.view",),
        risk_level=PermissionRiskLevel.HIGH,
    ),
    Permission(
        id="24",
        code="marketing.campaigns.view",
        domain="marketing",
        action="campaigns.view",
        scope_type=PermissionScopeType.ALL,
        description="View marketing campaigns",
        risk_level=PermissionRiskLevel.LOW,
    ),
    Permission(
        id="25",
        code="marketing.campaigns.edit",
        domain="marketing",
        action="campaigns.edit",
        scope_type=PermissionScopeType.ALL,
        description="Edit marketing campaigns",
        dependencies=("marketing.campaigns.view",),
        risk_level=PermissionRiskLevel.MEDIUM,
    ),
    Permission(
        id="26",
        code="marketing.campaigns.send",
        domain="marketing",
        action="campaigns.send",
        scope_type=PermissionScopeType.ALL,
        description="Send marketing campaigns",
        dependencies=("marketing.campaigns.view",),
        risk_level=PermissionRiskLevel.HIGH,
    ),
    Permission(
        id="27",
        code="discounts.view",
        domain="discounts",
        action="view",
        scope_type=PermissionScopeType.ALL,
        description="View discounts",
        risk_level=PermissionRiskLevel.LOW,
    ),
    Permission(
        id="28",
        code="discounts.create",
        domain="discounts",
        action="create",
        scope_type=PermissionScopeType.ALL,
        description="Create discounts",
        risk_level=PermissionRiskLevel.MEDIUM,
    ),
    Permission(
        id="29",
        code="discounts.edit",
        domain="discounts",
        action="edit",
        scope_type=PermissionScopeType.OWN,
        description="Edit discounts",
        dependencies=("discounts.view",),
        risk_level=PermissionRiskLevel.MEDIUM,
    ),
    Permission(
        id="30",
        code="settings.general.view",
        domain="settings",
        action="general.view",
        scope_type=PermissionScopeType.OWN,
        description="View general settings",
        risk_level=PermissionRiskLevel.LOW,
    ),
    Permission(
        id="31",
        code="settings.general.edit",
        domain="settings",
        action="general.edit",
        scope_type=PermissionScopeType.OWN,
        description="Edit general settings",
        dependencies=("settings.general.view",),
        risk_level=PermissionRiskLevel.MEDIUM,
    ),
    Permission(
        id="32",
        code="settings.domain.edit",
        domain="settings",
        action="domain.edit",
        scope_type=PermissionScopeType.OWN,
        description="Edit domain settings",
        dependencies=("settings.general.view",),
        risk_level=PermissionRiskLevel.HIGH,
    ),
    Permission(
        id="33",
        code="settings.payments.view",
        domain="settings",
        action="payments.view",
        scope_type=PermissionScopeType.OWN,
        description="View payment settings",
        risk_level=PermissionRiskLevel.LOW,
    ),
    Permission(
        id="34",
        code="settings.payments.edit",
        domain="settings",
        action="payments.edit",
        scope_type=PermissionScopeType.OWN,
        description="Edit payment settings",
        dependencies=("settings.payments.view",),
        risk_level=PermissionRiskLevel.HIGH,
    ),
    Permission(
        id="35",
        code="settings.shipping.edit",
        domain="settings",
        action="shipping.edit",
        scope_type=PermissionScopeType.OWN,
        description="Edit shipping settings",
        dependencies=("settings.general.view",),
        risk_level=PermissionRiskLevel.MEDIUM,
    ),
    Permission(
        id="36",
        code="staff.view",
        domain="staff",
        action="view",
        scope_type=PermissionScopeType.ALL,
        description="View staff",
        risk_level=PermissionRiskLevel.LOW,
    ),
    Permission(
        id="37",
        code="staff.invite",
        domain="staff",
        action="invite",
        scope_type=PermissionScopeType.ALL,
        description="Invite staff",
        risk_level=PermissionRiskLevel.MEDIUM,
    ),
    Permission(
        id="38",
        code="staff.edit",
        domain="staff",
        action="edit",
        scope_type=PermissionScopeType.ALL,
        description="Edit staff",
        dependencies=("staff.view",),
        risk_level=PermissionRiskLevel.MEDIUM,
    ),
    Permission(
        id="39",
        code="staff.remove",
        domain="staff",
        action="remove",
        scope_type=PermissionScopeType.ALL,
        description="Remove staff",
        dependencies=("staff.view",),
        risk_level=PermissionRiskLevel.HIGH,
    ),
    Permission(
        id="40",
        code="staff.roles.edit",
        domain="staff",
        action="roles.edit",
        scope_type=PermissionScopeType.ALL,
        description="Edit staff roles",
        risk_level=PermissionRiskLevel.HIGH,
    ),
    Permission(
        id="41",
        code="billing.view",
        domain="billing",
        action="view",
        scope_type=PermissionScopeType.OWN,
        description="View billing",
        risk_level=PermissionRiskLevel.LOW,
    ),
    Permission(
        id="42",
        code="billing.manage",
        domain="billing",
        action="manage",
        scope_type=PermissionScopeType.OWN,
        description="Manage billing",
        dependencies=("billing.view",),
        risk_level=PermissionRiskLevel.HIGH,
    ),
    Permission(
        id="43",
        code="billing.transfer_ownership",
        domain="billing",
        action="transfer_ownership",
        scope_type=PermissionScopeType.OWN,
        description="Transfer billing ownership",
        dependencies=("billing.view",),
        risk_level=PermissionRiskLevel.CRITICAL,
    ),
    Permission(
        id="44",
        code="themes.view",
        domain="themes",
        action="view",
        scope_type=PermissionScopeType.ALL,
        description="View themes",
        risk_level=PermissionRiskLevel.LOW,
    ),
    Permission(
        id="45",
        code="themes.edit",
        domain="themes",
        action="edit",
        scope_type=PermissionScopeType.ALL,
        description="Edit themes",
        dependencies=("themes.view",),
        risk_level=PermissionRiskLevel.MEDIUM,
    ),
    Permission(
        id="46",
        code="themes.publish",
        domain="themes",
        action="publish",
        scope_type=PermissionScopeType.ALL,
        description="Publish themes",
        dependencies=("themes.view",),
        risk_level=PermissionRiskLevel.HIGH,
    ),
    Permission(
        id="47",
        code="apps.view",
        domain="apps",
        action="view",
        scope_type=PermissionScopeType.ALL,
        description="View apps",
        risk_level=PermissionRiskLevel.LOW,
    ),
    Permission(
        id="48",
        code="apps.install",
        domain="apps",
        action="install",
        scope_type=PermissionScopeType.ALL,
        description="Install apps",
        risk_level=PermissionRiskLevel.MEDIUM,
    ),
    Permission(
        id="49",
        code="apps.configure",
        domain="apps",
        action="configure",
        scope_type=PermissionScopeType.ALL,
        description="Configure apps",
        dependencies=("apps.view",),
        risk_level=PermissionRiskLevel.MEDIUM,
    ),
)


for perm in DEFAULT_PERMISSIONS:
    register_permission(perm)
