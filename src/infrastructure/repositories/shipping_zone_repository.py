"""Shipping zone + rate repository implementation.

Defense in depth:
    * Every query includes an explicit `tenant_id` filter alongside RLS.
    * The one-active-zone-per-governorate-per-store invariant is
      enforced here (Postgres partial unique indexes can't express it
      because they'd need a cross-table EXISTS predicate). To make the
      check race-free under concurrent zone edits, membership writes
      take a per-store Postgres advisory lock — serialized at the
      transaction level, released at commit.
"""

from uuid import UUID

from sqlalchemy import and_, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.entities.shipping_rate import RateType, ShippingRate
from src.core.entities.shipping_zone import ShippingZone
from src.core.interfaces.repositories.shipping_zone_repository import (
    IShippingZoneRepository,
)
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.shipping_rate import ShippingRateModel
from src.infrastructure.database.models.tenant.shipping_zone import ShippingZoneModel
from src.infrastructure.database.models.tenant.shipping_zone_governorate import (
    ShippingZoneGovernorateModel,
)


class ShippingZoneRepository(IShippingZoneRepository):
    """SQLAlchemy implementation of IShippingZoneRepository."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ─── Tenant filter helpers ────────────────────────────────────

    def _tenant_filter_zone(self, query):
        tid = get_tenant_id()
        if tid:
            return query.where(ShippingZoneModel.tenant_id == tid)
        return query

    def _tenant_filter_rate(self, query):
        tid = get_tenant_id()
        if tid:
            return query.where(ShippingRateModel.tenant_id == tid)
        return query

    def _tenant_filter_gov(self, query):
        tid = get_tenant_id()
        if tid:
            return query.where(ShippingZoneGovernorateModel.tenant_id == tid)
        return query

    # ─── Conversion helpers ───────────────────────────────────────

    def _zone_to_entity(
        self,
        model: ShippingZoneModel,
        governorate_codes: list[str] | None = None,
    ) -> ShippingZone:
        return ShippingZone(
            id=model.id,
            tenant_id=model.tenant_id,
            store_id=model.store_id,
            name=model.name,
            name_ar=model.name_ar,
            estimated_days_min=model.estimated_days_min,
            estimated_days_max=model.estimated_days_max,
            cod_enabled=model.cod_enabled,
            cod_fee_cents=model.cod_fee_cents,
            is_active=model.is_active,
            sort_order=model.sort_order,
            governorate_codes=governorate_codes
            if governorate_codes is not None
            else sorted(g.governorate_code for g in (model.governorates or [])),
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _rate_to_entity(self, model: ShippingRateModel) -> ShippingRate:
        return ShippingRate(
            id=model.id,
            tenant_id=model.tenant_id,
            zone_id=model.zone_id,
            rate_type=RateType(model.rate_type),
            label=model.label,
            label_ar=model.label_ar,
            config=model.config or {},
            is_active=model.is_active,
            sort_order=model.sort_order,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    # ─── Invariant check ──────────────────────────────────────────

    async def _lock_store_for_zone_writes(self, store_id: UUID) -> None:
        """Take an xact-scoped advisory lock keyed on the store.

        Serializes concurrent zone-membership writes for a single store
        so the governorate-uniqueness check and subsequent write happen
        atomically. Released automatically when the transaction commits
        or rolls back.

        We use the two-int form (`pg_advisory_xact_lock(int, int)`) so
        the lock namespace is explicit — `hashtext` on a prefixed string
        would also work but collides globally; the (namespace_id,
        store_hash) pair keeps shipping locks isolated from other
        advisory-lock users.
        """
        # 0x5C0FE5 is an arbitrary but stable tag for shipping-zone
        # membership writes — picked once, never change it (existing
        # locks in transit during a deploy would reorder otherwise).
        SHIPPING_LOCK_NAMESPACE = 0x5C0FE5
        # Postgres advisory lock keys are int4/int8 — we hash the UUID
        # down to int4 via text() to keep the call portable.
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:ns, hashtext(:sid)::int)"),
            {"ns": SHIPPING_LOCK_NAMESPACE, "sid": str(store_id)},
        )

    async def _check_governorate_conflicts(
        self,
        store_id: UUID,
        governorate_codes: list[str],
        exclude_zone_id: UUID | None = None,
    ) -> list[str]:
        """Return any governorate codes already covered by another
        active zone in the same store.

        Called before create_zone / update_zone membership writes to
        preserve the resolver's unambiguous lookup invariant.
        """
        if not governorate_codes:
            return []
        query = (
            select(ShippingZoneGovernorateModel.governorate_code)
            .join(
                ShippingZoneModel,
                ShippingZoneModel.id == ShippingZoneGovernorateModel.zone_id,
            )
            .where(
                ShippingZoneGovernorateModel.store_id == store_id,
                ShippingZoneGovernorateModel.governorate_code.in_(governorate_codes),
                ShippingZoneModel.is_active.is_(True),
            )
        )
        if exclude_zone_id is not None:
            query = query.where(ShippingZoneGovernorateModel.zone_id != exclude_zone_id)
        result = await self.session.execute(self._tenant_filter_gov(query))
        return [row[0] for row in result.all()]

    # ─── Zone CRUD ────────────────────────────────────────────────

    async def create_zone(
        self, zone: ShippingZone, governorate_codes: list[str]
    ) -> ShippingZone:
        # Serialize all zone-membership writes for this store so the
        # conflict check below can't race with a concurrent create/update.
        await self._lock_store_for_zone_writes(zone.store_id)
        conflicts = await self._check_governorate_conflicts(
            zone.store_id, governorate_codes
        )
        if conflicts:
            raise ValueError(
                "Governorates already covered by another active zone: "
                + ", ".join(sorted(conflicts))
            )
        zone_model = ShippingZoneModel(
            id=zone.id,
            tenant_id=zone.tenant_id,
            store_id=zone.store_id,
            name=zone.name,
            name_ar=zone.name_ar,
            estimated_days_min=zone.estimated_days_min,
            estimated_days_max=zone.estimated_days_max,
            cod_enabled=zone.cod_enabled,
            cod_fee_cents=zone.cod_fee_cents,
            is_active=zone.is_active,
            sort_order=zone.sort_order,
        )
        self.session.add(zone_model)
        await self.session.flush()

        for code in governorate_codes:
            self.session.add(
                ShippingZoneGovernorateModel(
                    zone_id=zone_model.id,
                    governorate_code=code,
                    tenant_id=zone.tenant_id,
                    store_id=zone.store_id,
                )
            )
        await self.session.flush()
        await self.session.refresh(zone_model)
        return self._zone_to_entity(zone_model, governorate_codes=governorate_codes)

    async def get_zone(self, zone_id: UUID) -> ShippingZone | None:
        query = (
            select(ShippingZoneModel)
            .options(selectinload(ShippingZoneModel.governorates))
            .where(ShippingZoneModel.id == zone_id)
        )
        result = await self.session.execute(self._tenant_filter_zone(query))
        model = result.scalar_one_or_none()
        return self._zone_to_entity(model) if model else None

    async def list_zones_by_store(
        self, store_id: UUID, include_inactive: bool = False
    ) -> list[ShippingZone]:
        query = (
            select(ShippingZoneModel)
            .options(selectinload(ShippingZoneModel.governorates))
            .where(ShippingZoneModel.store_id == store_id)
            .order_by(ShippingZoneModel.sort_order, ShippingZoneModel.name)
        )
        if not include_inactive:
            query = query.where(ShippingZoneModel.is_active.is_(True))
        result = await self.session.execute(self._tenant_filter_zone(query))
        return [self._zone_to_entity(m) for m in result.scalars().all()]

    async def update_zone(
        self, zone: ShippingZone, governorate_codes: list[str] | None = None
    ) -> ShippingZone:
        # Lock even on field-only edits: flipping is_active changes which
        # zones the conflict-check sees, so two concurrent updates could
        # otherwise both claim the same governorate via the is_active path.
        await self._lock_store_for_zone_writes(zone.store_id)
        query = select(ShippingZoneModel).where(ShippingZoneModel.id == zone.id)
        result = await self.session.execute(self._tenant_filter_zone(query))
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"Zone {zone.id} not found")

        model.name = zone.name
        model.name_ar = zone.name_ar
        model.estimated_days_min = zone.estimated_days_min
        model.estimated_days_max = zone.estimated_days_max
        model.cod_enabled = zone.cod_enabled
        model.cod_fee_cents = zone.cod_fee_cents
        model.is_active = zone.is_active
        model.sort_order = zone.sort_order

        if governorate_codes is not None:
            # Only check conflicts for zones remaining / becoming active.
            if zone.is_active:
                conflicts = await self._check_governorate_conflicts(
                    zone.store_id,
                    governorate_codes,
                    exclude_zone_id=zone.id,
                )
                if conflicts:
                    raise ValueError(
                        "Governorates already covered by another active zone: "
                        + ", ".join(sorted(conflicts))
                    )
            # Replace membership atomically.
            await self.session.execute(
                delete(ShippingZoneGovernorateModel).where(
                    ShippingZoneGovernorateModel.zone_id == zone.id
                )
            )
            for code in governorate_codes:
                self.session.add(
                    ShippingZoneGovernorateModel(
                        zone_id=zone.id,
                        governorate_code=code,
                        tenant_id=zone.tenant_id,
                        store_id=zone.store_id,
                    )
                )
        await self.session.flush()
        await self.session.refresh(model)
        return self._zone_to_entity(
            model,
            governorate_codes=governorate_codes
            if governorate_codes is not None
            else None,
        )

    async def delete_zone(self, zone_id: UUID) -> bool:
        query = select(ShippingZoneModel).where(ShippingZoneModel.id == zone_id)
        result = await self.session.execute(self._tenant_filter_zone(query))
        model = result.scalar_one_or_none()
        if model is None:
            return False
        model.is_active = False
        await self.session.flush()
        return True

    async def hard_delete_zone(self, zone_id: UUID) -> bool:
        query = select(ShippingZoneModel).where(ShippingZoneModel.id == zone_id)
        result = await self.session.execute(self._tenant_filter_zone(query))
        model = result.scalar_one_or_none()
        if model is None:
            return False
        await self.session.delete(model)
        await self.session.flush()
        return True

    # ─── Rates ────────────────────────────────────────────────────

    async def create_rate(self, rate: ShippingRate) -> ShippingRate:
        model = ShippingRateModel(
            id=rate.id,
            tenant_id=rate.tenant_id,
            zone_id=rate.zone_id,
            rate_type=rate.rate_type.value,
            label=rate.label,
            label_ar=rate.label_ar,
            config=rate.config,
            is_active=rate.is_active,
            sort_order=rate.sort_order,
        )
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._rate_to_entity(model)

    async def get_rate(self, rate_id: UUID) -> ShippingRate | None:
        query = select(ShippingRateModel).where(ShippingRateModel.id == rate_id)
        result = await self.session.execute(self._tenant_filter_rate(query))
        model = result.scalar_one_or_none()
        return self._rate_to_entity(model) if model else None

    async def list_rates_by_zone(
        self, zone_id: UUID, include_inactive: bool = False
    ) -> list[ShippingRate]:
        query = (
            select(ShippingRateModel)
            .where(ShippingRateModel.zone_id == zone_id)
            .order_by(ShippingRateModel.sort_order, ShippingRateModel.label)
        )
        if not include_inactive:
            query = query.where(ShippingRateModel.is_active.is_(True))
        result = await self.session.execute(self._tenant_filter_rate(query))
        return [self._rate_to_entity(m) for m in result.scalars().all()]

    async def update_rate(self, rate: ShippingRate) -> ShippingRate:
        query = select(ShippingRateModel).where(ShippingRateModel.id == rate.id)
        result = await self.session.execute(self._tenant_filter_rate(query))
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"Rate {rate.id} not found")
        model.rate_type = rate.rate_type.value
        model.label = rate.label
        model.label_ar = rate.label_ar
        model.config = rate.config
        model.is_active = rate.is_active
        model.sort_order = rate.sort_order
        await self.session.flush()
        await self.session.refresh(model)
        return self._rate_to_entity(model)

    async def delete_rate(self, rate_id: UUID) -> bool:
        query = select(ShippingRateModel).where(ShippingRateModel.id == rate_id)
        result = await self.session.execute(self._tenant_filter_rate(query))
        model = result.scalar_one_or_none()
        if model is None:
            return False
        model.is_active = False
        await self.session.flush()
        return True

    # ─── Coverage / resolver input ───────────────────────────────

    async def get_zone_for_governorate(
        self, store_id: UUID, governorate_code: str
    ) -> ShippingZone | None:
        query = (
            select(ShippingZoneModel)
            .options(selectinload(ShippingZoneModel.governorates))
            .join(
                ShippingZoneGovernorateModel,
                ShippingZoneModel.id == ShippingZoneGovernorateModel.zone_id,
            )
            .where(
                ShippingZoneModel.store_id == store_id,
                ShippingZoneModel.is_active.is_(True),
                ShippingZoneGovernorateModel.governorate_code == governorate_code,
            )
            .limit(1)
        )
        result = await self.session.execute(self._tenant_filter_zone(query))
        model = result.scalars().first()
        return self._zone_to_entity(model) if model else None

    async def get_zones_with_rates_for_store(
        self, store_id: UUID, include_inactive: bool = False
    ) -> list[tuple[ShippingZone, list[ShippingRate]]]:
        zone_query = (
            select(ShippingZoneModel)
            .options(
                selectinload(ShippingZoneModel.governorates),
                selectinload(ShippingZoneModel.rates),
            )
            .where(ShippingZoneModel.store_id == store_id)
            .order_by(ShippingZoneModel.sort_order, ShippingZoneModel.name)
        )
        if not include_inactive:
            zone_query = zone_query.where(ShippingZoneModel.is_active.is_(True))
        result = await self.session.execute(self._tenant_filter_zone(zone_query))
        out: list[tuple[ShippingZone, list[ShippingRate]]] = []
        for zm in result.scalars().all():
            # Honour include_inactive at the rate level too — merchants
            # editing a disabled zone still need to see its disabled rates.
            rate_models = zm.rates or []
            if not include_inactive:
                rate_models = [r for r in rate_models if r.is_active]
            rates = [self._rate_to_entity(r) for r in rate_models]
            rates.sort(key=lambda r: (r.sort_order, r.label))
            out.append((self._zone_to_entity(zm), rates))
        return out

    async def get_covered_governorate_codes(self, store_id: UUID) -> set[str]:
        query = (
            select(ShippingZoneGovernorateModel.governorate_code)
            .join(
                ShippingZoneModel,
                ShippingZoneModel.id == ShippingZoneGovernorateModel.zone_id,
            )
            .where(
                and_(
                    ShippingZoneGovernorateModel.store_id == store_id,
                    ShippingZoneModel.is_active.is_(True),
                )
            )
        )
        result = await self.session.execute(self._tenant_filter_gov(query))
        return {row[0] for row in result.all()}

    async def has_active_zones(self, store_id: UUID) -> bool:
        # Single-row existence check — LIMIT 1 + `scalar_one_or_none`
        # so we don't pay for a COUNT(*) or hydrate a full row.
        query = (
            select(ShippingZoneModel.id)
            .where(
                ShippingZoneModel.store_id == store_id,
                ShippingZoneModel.is_active.is_(True),
            )
            .limit(1)
        )
        result = await self.session.execute(self._tenant_filter_zone(query))
        return result.scalar_one_or_none() is not None
