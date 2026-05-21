"""Repository for InstapayIntent rows."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.instapay import InstapayIntent, InstapayIntentStatus
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.instapay_intent import (
    InstapayIntentModel,
)


class InstapayIntentRepository:
    """Persist and query InstaPay per-order intents."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        tid = get_tenant_id()
        if tid:
            return query.where(InstapayIntentModel.tenant_id == tid)
        return query

    def _to_entity(self, model: InstapayIntentModel) -> InstapayIntent:
        return InstapayIntent(
            id=model.id,
            tenant_id=model.tenant_id,
            store_id=model.store_id,
            order_id=model.order_id,
            reference_code=model.reference_code,
            display_ipa=model.display_ipa,
            display_phone=model.display_phone,
            amount_cents=model.amount_cents,
            expires_at=model.expires_at,
            qr_payload=model.qr_payload,
            status=model.status,
            created_at=model.created_at,
        )

    async def create(self, intent: InstapayIntent) -> InstapayIntent:
        model = InstapayIntentModel(
            id=intent.id,
            tenant_id=intent.tenant_id,
            store_id=intent.store_id,
            order_id=intent.order_id,
            reference_code=intent.reference_code,
            display_ipa=intent.display_ipa,
            display_phone=intent.display_phone,
            amount_cents=intent.amount_cents,
            expires_at=intent.expires_at,
            qr_payload=intent.qr_payload,
            status=intent.status,
        )
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def get_by_order_id(self, order_id: UUID) -> InstapayIntent | None:
        query = select(InstapayIntentModel).where(
            InstapayIntentModel.order_id == order_id
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_reference_code(self, reference_code: str) -> InstapayIntent | None:
        query = select(InstapayIntentModel).where(
            InstapayIntentModel.reference_code == reference_code
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def reference_code_exists(self, reference_code: str) -> bool:
        """Globally unique check — called during ref-code generation.

        Does NOT apply the tenant filter because the DB-side unique
        constraint spans all tenants (reference codes are opaque enough
        that a collision must be treated as a real collision).
        """
        query = select(InstapayIntentModel.id).where(
            InstapayIntentModel.reference_code == reference_code
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none() is not None

    async def update(self, intent: InstapayIntent) -> InstapayIntent:
        """Persist whatever the entity currently says — idiomatic path.

        The entity's ``mark_paid`` / ``mark_expired`` / ``mark_cancelled``
        methods set ``self.status``; callers pass the mutated entity in
        and the repo mirrors it. Cheaper than read-then-write because
        we touch a single row by PK with no SELECT first.
        """
        from sqlalchemy import update as sa_update

        await self.session.execute(
            sa_update(InstapayIntentModel)
            .where(InstapayIntentModel.id == intent.id)
            .values(status=intent.status, updated_at=datetime.now(UTC))
        )
        await self.session.flush()
        return intent

    async def update_status(
        self,
        intent_id: UUID,
        status: InstapayIntentStatus,
    ) -> None:
        """Legacy shortcut — kept for the sweeper which doesn't load entities.

        Prefer :meth:`update` from call sites that already hold the
        entity; this path issues one less SELECT but loses the
        entity-drives-its-own-state contract.
        """
        from sqlalchemy import update as sa_update

        await self.session.execute(
            sa_update(InstapayIntentModel)
            .where(InstapayIntentModel.id == intent_id)
            .values(status=status, updated_at=datetime.now(UTC))
        )
        await self.session.flush()

    async def list_expired_awaiting_payment(
        self, *, now: datetime | None = None, limit: int = 100
    ) -> list[InstapayIntent]:
        """Find intents past expiry that never received a proof.

        Used by the Celery beat sweeper. Intentionally does NOT call
        :meth:`_tenant_filter` — this runs in a cross-tenant context
        under RLS bypass (set up by the caller). A tenant filter here
        would silently return nothing under bypass and break the
        sweep. The caller narrows to each intent's tenant before any
        subsequent write so RLS still protects per-row mutations.
        """
        cutoff = now or datetime.now(UTC)
        query = (
            select(InstapayIntentModel)
            .where(
                InstapayIntentModel.status == InstapayIntentStatus.AWAITING_PAYMENT,
                InstapayIntentModel.expires_at <= cutoff,
            )
            .limit(limit)
        )
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def list_stuck_proof_received(
        self,
        *,
        grace_hours: int = 48,
        now: datetime | None = None,
        limit: int = 100,
    ) -> list[InstapayIntent]:
        """Find intents in PROOF_RECEIVED that have been sitting unreviewed.

        The customer uploaded a proof in time but the merchant never
        reviewed it. After ``grace_hours`` past the intent's original
        ``expires_at`` we escalate: the sweeper will auto-cancel the
        order so it doesn't stay PENDING forever. Merchants who want a
        looser policy can raise ``grace_hours`` via task kwargs.

        Like :meth:`list_expired_awaiting_payment`, this is a
        cross-tenant scan run under RLS bypass — no tenant filter.
        """
        from datetime import timedelta as _td

        cutoff = (now or datetime.now(UTC)) - _td(hours=grace_hours)
        query = (
            select(InstapayIntentModel)
            .where(
                InstapayIntentModel.status == InstapayIntentStatus.PROOF_RECEIVED,
                InstapayIntentModel.expires_at <= cutoff,
            )
            .limit(limit)
        )
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]
