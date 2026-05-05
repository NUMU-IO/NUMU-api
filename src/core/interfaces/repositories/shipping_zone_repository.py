"""Shipping zone repository interface.

Covers the full persistence surface for zones and rates:
    * Zone CRUD (create, get, list by store, update, soft-delete)
    * M2M governorate membership (set / list)
    * Rate CRUD (create, update, delete) scoped to a zone
    * Coverage queries (which governorates are covered by an active zone,
      cross-store conflicts)

The repository does NOT know about the resolver or the rate config
discriminated union — it returns raw JSONB for `rate.config` and trusts
the caller to parse/validate.
"""

from abc import ABC, abstractmethod
from uuid import UUID

from src.core.entities.shipping_rate import ShippingRate
from src.core.entities.shipping_zone import ShippingZone


class IShippingZoneRepository(ABC):
    """Persistence interface for merchant-defined shipping zones + rates."""

    # ─── Zone CRUD ──────────────────────────────────────────────────

    @abstractmethod
    async def create_zone(
        self, zone: ShippingZone, governorate_codes: list[str]
    ) -> ShippingZone:
        """Create a zone and its governorate memberships atomically.

        Raises:
            ValueError: if any governorate is already covered by another
                active zone in the same store.
        """
        ...

    @abstractmethod
    async def get_zone(self, zone_id: UUID) -> ShippingZone | None:
        """Fetch a zone by ID with its governorate_codes populated."""
        ...

    @abstractmethod
    async def list_zones_by_store(
        self, store_id: UUID, include_inactive: bool = False
    ) -> list[ShippingZone]:
        """List all zones for a store, ordered by sort_order."""
        ...

    @abstractmethod
    async def update_zone(
        self, zone: ShippingZone, governorate_codes: list[str] | None = None
    ) -> ShippingZone:
        """Update a zone's fields. If `governorate_codes` is provided,
        replaces the M2M membership atomically (validating no conflicts).
        """
        ...

    @abstractmethod
    async def delete_zone(self, zone_id: UUID) -> bool:
        """Soft-delete by setting is_active=False. Orders keep their FK."""
        ...

    @abstractmethod
    async def hard_delete_zone(self, zone_id: UUID) -> bool:
        """Actually delete the row (used by the preset undo flow)."""
        ...

    # ─── Rates ──────────────────────────────────────────────────────

    @abstractmethod
    async def create_rate(self, rate: ShippingRate) -> ShippingRate:
        """Create a rate on a zone."""
        ...

    @abstractmethod
    async def get_rate(self, rate_id: UUID) -> ShippingRate | None:
        """Fetch a single rate by ID."""
        ...

    @abstractmethod
    async def list_rates_by_zone(
        self, zone_id: UUID, include_inactive: bool = False
    ) -> list[ShippingRate]:
        """List all rates on a zone, ordered by sort_order."""
        ...

    @abstractmethod
    async def update_rate(self, rate: ShippingRate) -> ShippingRate:
        """Update a rate."""
        ...

    @abstractmethod
    async def delete_rate(self, rate_id: UUID) -> bool:
        """Soft-delete by setting is_active=False."""
        ...

    # ─── Coverage / resolver input ─────────────────────────────────

    @abstractmethod
    async def get_zone_for_governorate(
        self, store_id: UUID, governorate_code: str
    ) -> ShippingZone | None:
        """Return the single active zone covering `governorate_code` in
        `store_id`, or None if the governorate is uncovered.
        """
        ...

    @abstractmethod
    async def get_zones_with_rates_for_store(
        self, store_id: UUID, include_inactive: bool = False
    ) -> list[tuple[ShippingZone, list[ShippingRate]]]:
        """Return zones + their rates in a single query.

        Used by the resolver (active-only, feeds the Redis cache) and by
        the merchant dashboard zones list (include_inactive=True, so
        disabled zones still surface for re-enable / audit).
        """
        ...

    @abstractmethod
    async def get_covered_governorate_codes(self, store_id: UUID) -> set[str]:
        """Return the set of governorate codes covered by any active zone."""
        ...

    @abstractmethod
    async def has_active_zones(self, store_id: UUID) -> bool:
        """Cheap existence check — used by checkout to enforce rate selection.

        Once a store has any active zone, clients MUST pick a shipping
        option; checkout rejects payloads without a `selected_shipping_rate_id`.
        Before any zones exist, the legacy zero-shipping path is tolerated.
        """
        ...
