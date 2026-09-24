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
  3. the entitlement — some capabilities are sold, so the entitlement
     catalog decides (``EntitlementService``), not a plan name.

Deliberately NOT a new table. Per-store overrides ride on the existing
``stores.settings`` JSONB and the sold ones ask the entitlement catalog;
a ``store_capabilities`` table would be a third place for entitlement truth
to disagree with itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.entitlement_service import EntitlementService
from src.core.entities.store import Store
from src.infrastructure.database.models.public.tenant import TenantModel


@dataclass(frozen=True)
class Capability:
    """One governed piece of commerce behaviour."""

    key: str
    name: str
    name_ar: str
    implemented: bool
    default_on: bool
    #: The entitlement that sells it; None when every store may turn it on.
    entitlement: str | None = None


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
            entitlement="multi_warehouse",
        ),
        Capability(
            "subscriptions",
            "Subscriptions",
            "الاشتراكات",
            True,
            False,
            entitlement="product_subscriptions",
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
    """Resolves capabilities for a store against its entitlements and overrides."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.entitlements = EntitlementService(session)

    async def _entitled(self, store: Store) -> frozenset[str]:
        """The sold capabilities' entitlements this store's tenant holds."""
        tenant = (
            await self.session.get(TenantModel, store.tenant_id)
            if store.tenant_id
            else None
        )
        if tenant is None:
            return frozenset()
        return frozenset([
            key for key in _SOLD if await self.entitlements.has(tenant, key)
        ])

    @staticmethod
    def resolve(store: Store, entitled: frozenset[str], key: str) -> bool:
        """Resolve one capability without touching the database."""
        capability = CAPABILITIES.get(key)
        if capability is None or not capability.implemented:
            return False

        if capability.entitlement and capability.entitlement not in entitled:
            return False

        overrides = (store.settings or {}).get("capabilities") or {}
        override = overrides.get(key)
        if isinstance(override, bool):
            return override

        return capability.default_on

    @staticmethod
    def resolve_all(store: Store, entitled: frozenset[str]) -> dict[str, bool]:
        """Resolve every known capability for the store."""
        return {
            key: CapabilityService.resolve(store, entitled, key) for key in CAPABILITIES
        }

    async def has(self, store: Store, key: str) -> bool:
        """Resolve one capability, loading the store's entitlements."""
        return self.resolve(store, await self._entitled(store), key)

    async def all_for(self, store: Store) -> dict[str, bool]:
        """Resolve every capability, loading the store's entitlements."""
        return self.resolve_all(store, await self._entitled(store))

    async def min_plan(self, capability: Capability) -> str:
        """The cheapest plan that sells it, for "Requires the Pro plan" copy;
        "free" when it is not sold."""
        if not capability.entitlement:
            return "free"
        plans = await self.entitlements.available_via(capability.entitlement)
        return next((p for p in plans if not p.startswith("addon:")), "free")


_SOLD = frozenset(c.entitlement for c in CAPABILITIES.values() if c.entitlement)
