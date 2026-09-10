"""Capability resolution — one answer to "can this store do X?".

Business sector and capability are not the same thing: two coffee roasters
can want completely different behaviour. So nothing in the codebase should
branch on a sector. It should ask for a capability::

    if await caps.has(store, "multi_warehouse"): ...

A capability is resolved from three things, in order:

  1. ``implemented`` — a capability whose backing feature does not exist yet
     always resolves False. Without this, a flag flips and nothing happens,
     which is worse than no flag at all.
  2. an explicit per-store override in ``stores.settings["capabilities"]``
     (what a sector preset writes, and what the merchant can toggle);
  3. the plan floor — some capabilities are only sold above a tier.

Deliberately NOT a new table. Per-store overrides ride on the existing
``stores.settings`` JSONB and the plan floor reads the existing
``PLAN_LIMITS``; a ``store_capabilities`` table would be a third place for
entitlement truth to disagree with itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.store import Store
from src.infrastructure.tenancy.repository import TenantRepository

_PLAN_RANK: dict[str, int] = {
    "demo": 0,
    "free": 0,
    "trial": 1,
    "payg": 1,
    "starter": 1,
    "pro": 2,
    "enterprise": 3,
}


@dataclass(frozen=True)
class Capability:
    """One governed piece of commerce behaviour."""

    key: str
    name: str
    name_ar: str
    implemented: bool
    default_on: bool
    min_plan: str = "free"


CAPABILITIES: dict[str, Capability] = {
    c.key: c
    for c in [
        Capability("catalog", "Catalog", "الكتالوج", True, True),
        Capability("variants", "Variants", "المتغيرات", True, True),
        Capability("inventory", "Inventory", "المخزون", True, True),
        Capability("shipping", "Shipping", "الشحن", True, True),
        Capability("digital_delivery", "Digital delivery", "منتجات رقمية", True, False),
        Capability(
            "multi_warehouse",
            "Multiple locations",
            "فروع متعددة",
            True,
            False,
            min_plan="pro",
        ),
        Capability(
            "subscriptions",
            "Subscriptions",
            "الاشتراكات",
            True,
            False,
            min_plan="pro",
        ),
        Capability("gift_cards", "Gift cards", "كروت الهدايا", True, False),
        # Not built yet — see docs/omnichannel/… and the donations module
        # decision. These resolve False no matter what is written to
        # settings, so a preset can name them without lying to the merchant.
        Capability("donations", "Donations", "التبرعات", False, False),
        Capability("campaigns", "Campaigns", "الحملات", False, False),
        Capability("custom_amount", "Custom amount", "مبلغ مفتوح", False, False),
        Capability("expiry_tracking", "Expiry tracking", "تتبع الصلاحية", False, False),
        Capability("gift_message", "Gift message", "رسالة هدية", False, False),
    ]
}


class CapabilityService:
    """Resolves capabilities for a store against its plan and overrides."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._plan_cache: dict[UUID, str] = {}

    async def _plan_name(self, tenant_id: UUID | None) -> str:
        if tenant_id is None:
            return "free"
        if tenant_id not in self._plan_cache:
            tenant = await TenantRepository(self.session).get_by_id(tenant_id)
            self._plan_cache[tenant_id] = (tenant.plan if tenant else "free") or "free"
        return self._plan_cache[tenant_id]

    @staticmethod
    def resolve(store: Store, plan_name: str, key: str) -> bool:
        """Resolve one capability without touching the database."""
        capability = CAPABILITIES.get(key)
        if capability is None or not capability.implemented:
            return False

        if _PLAN_RANK.get(plan_name.lower(), 0) < _PLAN_RANK.get(
            capability.min_plan, 0
        ):
            return False

        overrides = (store.settings or {}).get("capabilities") or {}
        override = overrides.get(key)
        if isinstance(override, bool):
            return override

        return capability.default_on

    @staticmethod
    def resolve_all(store: Store, plan_name: str) -> dict[str, bool]:
        """Resolve every known capability for the store."""
        return {
            key: CapabilityService.resolve(store, plan_name, key)
            for key in CAPABILITIES
        }

    async def has(self, store: Store, key: str) -> bool:
        """Resolve one capability, loading the store's plan as needed."""
        plan_name = await self._plan_name(store.tenant_id)
        return self.resolve(store, plan_name, key)

    async def all_for(self, store: Store) -> dict[str, bool]:
        """Resolve every capability, loading the store's plan as needed."""
        plan_name = await self._plan_name(store.tenant_id)
        return self.resolve_all(store, plan_name)
