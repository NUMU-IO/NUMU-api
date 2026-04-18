"""Role entity representing a bundle of permissions."""

from dataclasses import dataclass, field
from enum import StrEnum


class RoleTemplate(StrEnum):
    """System role templates."""

    OWNER = "owner"
    ADMIN = "admin"
    MANAGER = "manager"
    SUPPORT = "support"
    MARKETING = "marketing"
    ACCOUNTANT = "accountant"
    INVENTORY = "inventory"
    CUSTOM = "custom"


@dataclass
class Role:
    """Role entity representing a bundle of permissions.

    Roles bundle permissions and can be:
    - System templates (seeded per deploy)
    - Tenant-owned (created by tenants)
    - Owner role (special implicit role via is_owner flag)
    """

    id: str
    name: str
    slug: str
    tenant_id: str | None = None
    description: str | None = None
    is_system: bool = False
    is_owner: bool = False
    is_locked: bool = False
    version: int = 1
    cloned_from_id: str | None = None
    created_by_id: str | None = None
    permission_ids: tuple[str, ...] = field(default_factory=tuple)
    deleted_at: bool = False

    @property
    def is_template(self) -> bool:
        """Check if this is a system template."""
        return self.is_system and not self.tenant_id


SYSTEM_ROLE_TEMPLATES: dict[RoleTemplate, Role] = {
    RoleTemplate.OWNER: Role(
        id="system-owner",
        name="Owner",
        slug="owner",
        description="Full access to all features",
        is_system=True,
        is_owner=True,
        is_locked=True,
    ),
    RoleTemplate.ADMIN: Role(
        id="system-admin",
        name="Admin",
        slug="admin",
        description="All permissions except ownership transfer and owner removal",
        is_system=True,
        is_locked=True,
        permission_ids=(
            "orders.view",
            "orders.create",
            "orders.edit",
            "orders.cancel",
            "orders.refund",
            "orders.export",
            "orders.fulfill",
            "products.view",
            "products.create",
            "products.edit",
            "products.edit.price",
            "products.delete",
            "products.publish",
            "inventory.view",
            "inventory.adjust",
            "inventory.transfer",
            "customers.view",
            "customers.edit",
            "customers.export",
            "analytics.view",
            "analytics.export",
            "analytics.financial.view",
            "marketing.campaigns.view",
            "marketing.campaigns.edit",
            "marketing.campaigns.send",
            "discounts.view",
            "discounts.create",
            "discounts.edit",
            "settings.general.view",
            "settings.general.edit",
            "settings.payments.view",
            "settings.payments.edit",
            "settings.shipping.edit",
            "staff.view",
            "staff.invite",
            "staff.edit",
            "staff.roles.edit",
            "billing.view",
            "billing.manage",
            "themes.view",
            "themes.edit",
            "themes.publish",
            "apps.view",
            "apps.install",
            "apps.configure",
        ),
    ),
    RoleTemplate.MANAGER: Role(
        id="system-manager",
        name="Manager",
        slug="manager",
        description="Orders, products, inventory, customers, analytics, discounts",
        is_system=True,
        permission_ids=(
            "orders.view",
            "orders.create",
            "orders.edit",
            "orders.cancel",
            "orders.refund",
            "orders.fulfill",
            "products.view",
            "products.create",
            "products.edit",
            "products.edit.price",
            "products.publish",
            "inventory.view",
            "inventory.adjust",
            "inventory.transfer",
            "customers.view",
            "customers.edit",
            "analytics.view",
            "discounts.view",
            "discounts.create",
            "discounts.edit",
        ),
    ),
    RoleTemplate.SUPPORT: Role(
        id="system-support",
        name="Support",
        slug="support",
        description="Assigned orders view/refund, customers",
        is_system=True,
        permission_ids=(
            "orders.view",
            "orders.refund",
            "customers.view",
            "customers.edit",
        ),
    ),
    RoleTemplate.MARKETING: Role(
        id="system-marketing",
        name="Marketing",
        slug="marketing",
        description="Marketing, discounts, themes, customers, analytics",
        is_system=True,
        permission_ids=(
            "marketing.campaigns.view",
            "marketing.campaigns.edit",
            "marketing.campaigns.send",
            "discounts.view",
            "discounts.create",
            "discounts.edit",
            "themes.view",
            "themes.edit",
            "themes.publish",
            "customers.view",
            "analytics.view",
        ),
    ),
    RoleTemplate.ACCOUNTANT: Role(
        id="system-accountant",
        name="Accountant",
        slug="accountant",
        description="Analytics, orders, billing, customers",
        is_system=True,
        permission_ids=(
            "analytics.view",
            "analytics.export",
            "analytics.financial.view",
            "orders.view",
            "orders.export",
            "billing.view",
            "billing.manage",
            "customers.view",
        ),
    ),
    RoleTemplate.INVENTORY: Role(
        id="system-inventory",
        name="Inventory",
        slug="inventory",
        description="Inventory, products, fulfill orders",
        is_system=True,
        permission_ids=(
            "inventory.view",
            "inventory.adjust",
            "inventory.transfer",
            "products.view",
            "products.edit",
            "orders.view",
            "orders.fulfill",
        ),
    ),
    RoleTemplate.CUSTOM: Role(
        id="system-custom",
        name="Custom",
        slug="custom",
        description="Starting point for custom roles",
        is_system=True,
        permission_ids=(),
    ),
}


def get_role_template(template: RoleTemplate) -> Role | None:
    """Get a system role template."""
    return SYSTEM_ROLE_TEMPLATES.get(template)


def get_all_templates() -> list[Role]:
    """Get all system role templates."""
    return list(SYSTEM_ROLE_TEMPLATES.values())


def is_owner_permission(code: str) -> bool:
    """Check if a permission code is owner-only."""
    return code in (
        "settings.domain.edit",
        "billing.transfer_ownership",
        "billing.manage",
    )
