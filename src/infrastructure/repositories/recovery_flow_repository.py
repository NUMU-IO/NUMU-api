"""Repository for the recovery-flow aggregate (backend-021).

Owns persistence + idempotent-create + state transition + rollup-ledger
gating per spec 009 CL-006. The aggregate's business invariants live on
the entity (``RecoveryFlow.transition_to``); the repository handles
serialisation + the concurrency-control patterns the entity can't
express on its own (``ON CONFLICT DO NOTHING``, advisory locks, atomic
INSERT-or-UPDATE on the rollup).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.recovery_flow import (
    RecoveryFlow,
    RecoveryFlowState,
    RecoveryMonthlyRollup,
    RecoveryRollupLedgerEventType,
    RecoveryStep,
)
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.recovery_flow import (
    RecoveryFlowModel,
    RecoveryMonthlyRollupModel,
    RecoveryRollupLedgerModel,
    RecoveryStepModel,
)


class RecoveryFlowRepository:
    """Persist, query, and update recovery flows + their child steps + rollup."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Tenant-filter helper (RLS belt-and-suspenders)
    # ------------------------------------------------------------------

    def _tenant_filter(self, query, model_cls):
        tid = get_tenant_id()
        if tid:
            return query.where(model_cls.tenant_id == tid)
        return query

    # ------------------------------------------------------------------
    # Entity ↔ Model mapping
    # ------------------------------------------------------------------

    def _flow_to_entity(self, m: RecoveryFlowModel) -> RecoveryFlow:
        return RecoveryFlow(
            id=m.id,
            tenant_id=m.tenant_id,
            store_id=m.store_id,
            shopify_order_id=m.shopify_order_id,
            state=m.state,
            cadence=m.cadence or [],
            current_step_index=m.current_step_index,
            payment_link_session_id=m.payment_link_session_id,
            recovered_amount_cents=m.recovered_amount_cents,
            recovered_via_rail=m.recovered_via_rail,
            refunded_at=m.refunded_at,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )

    def _step_to_entity(self, m: RecoveryStepModel) -> RecoveryStep:
        return RecoveryStep(
            id=m.id,
            flow_id=m.flow_id,
            step_index=m.step_index,
            template_key=m.template_key,
            channel=m.channel,
            scheduled_for=m.scheduled_for,
            sent_at=m.sent_at,
            opened_at=m.opened_at,
            delivered_at=m.delivered_at,
            failed_reason=m.failed_reason,
        )

    # ------------------------------------------------------------------
    # Flow CRUD
    # ------------------------------------------------------------------

    async def create_if_not_exists(
        self, flow: RecoveryFlow
    ) -> tuple[RecoveryFlow, bool]:
        """Idempotent flow creation per spec 009 CL-006.

        Returns ``(flow, created)`` — ``created`` is ``True`` if a new row
        was inserted, ``False`` if a flow already existed for the same
        ``(store_id, shopify_order_id)`` (in which case the existing
        flow is returned).
        """
        stmt = (
            pg_insert(RecoveryFlowModel)
            .values(
                id=flow.id,
                tenant_id=flow.tenant_id,
                store_id=flow.store_id,
                shopify_order_id=flow.shopify_order_id,
                state=flow.state,
                cadence=flow.cadence,
                current_step_index=flow.current_step_index,
                payment_link_session_id=flow.payment_link_session_id,
                recovered_amount_cents=flow.recovered_amount_cents,
                recovered_via_rail=flow.recovered_via_rail,
                refunded_at=flow.refunded_at,
            )
            .on_conflict_do_nothing(constraint="uq_recovery_flow_per_order")
            .returning(RecoveryFlowModel.id)
        )
        result = await self.session.execute(stmt)
        inserted_id = result.scalar_one_or_none()
        if inserted_id is None:
            existing = await self.get_by_store_and_order(
                flow.store_id, flow.shopify_order_id
            )
            if existing is None:
                # Edge case: ON CONFLICT DO NOTHING returned nothing AND the row is
                # missing under the current tenant filter — likely a tenant-scope
                # mismatch. Surface explicitly so the caller can debug.
                raise RuntimeError(
                    "Recovery flow upsert returned no row and existing lookup found nothing — "
                    "check tenant context / RLS configuration."
                )
            return existing, False
        await self.session.flush()
        # Refetch to pick up DB defaults (server_default timestamps, etc.)
        created = await self.get_by_id(inserted_id)
        assert created is not None
        return created, True

    async def get_by_id(self, flow_id: UUID) -> RecoveryFlow | None:
        query = select(RecoveryFlowModel).where(RecoveryFlowModel.id == flow_id)
        result = await self.session.execute(
            self._tenant_filter(query, RecoveryFlowModel)
        )
        m = result.scalar_one_or_none()
        return self._flow_to_entity(m) if m else None

    async def get_by_store_and_order(
        self, store_id: UUID, shopify_order_id: str
    ) -> RecoveryFlow | None:
        query = select(RecoveryFlowModel).where(
            and_(
                RecoveryFlowModel.store_id == store_id,
                RecoveryFlowModel.shopify_order_id == shopify_order_id,
            )
        )
        result = await self.session.execute(
            self._tenant_filter(query, RecoveryFlowModel)
        )
        m = result.scalar_one_or_none()
        return self._flow_to_entity(m) if m else None

    async def list_by_store(
        self,
        store_id: UUID,
        *,
        state: RecoveryFlowState | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RecoveryFlow]:
        query = (
            select(RecoveryFlowModel)
            .where(RecoveryFlowModel.store_id == store_id)
            .order_by(RecoveryFlowModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if state is not None:
            query = query.where(RecoveryFlowModel.state == state)
        result = await self.session.execute(
            self._tenant_filter(query, RecoveryFlowModel)
        )
        return [self._flow_to_entity(m) for m in result.scalars().all()]

    async def update_state(self, flow: RecoveryFlow) -> RecoveryFlow:
        """Persist the entity's current state + bookkeeping fields.

        Matches the InstapayIntentRepository pattern of "the entity drives
        its own state; the repo mirrors it." Issues a single UPDATE by PK
        with no SELECT first.
        """
        from sqlalchemy import update as sa_update

        await self.session.execute(
            sa_update(RecoveryFlowModel)
            .where(RecoveryFlowModel.id == flow.id)
            .values(
                state=flow.state,
                current_step_index=flow.current_step_index,
                payment_link_session_id=flow.payment_link_session_id,
                recovered_amount_cents=flow.recovered_amount_cents,
                recovered_via_rail=flow.recovered_via_rail,
                refunded_at=flow.refunded_at,
                updated_at=datetime.now(UTC),
            )
        )
        await self.session.flush()
        return flow

    # ------------------------------------------------------------------
    # Step CRUD
    # ------------------------------------------------------------------

    async def insert_step(self, step: RecoveryStep, *, tenant_id: UUID) -> RecoveryStep:
        """Insert a new step row.

        Will raise IntegrityError if a row with the same (flow_id, step_index)
        already exists — caller catches this as the idempotency signal.
        """
        model = RecoveryStepModel(
            id=step.id,
            tenant_id=tenant_id,
            flow_id=step.flow_id,
            step_index=step.step_index,
            template_key=step.template_key,
            channel=step.channel,
            scheduled_for=step.scheduled_for,
            sent_at=step.sent_at,
            opened_at=step.opened_at,
            delivered_at=step.delivered_at,
            failed_reason=step.failed_reason,
        )
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._step_to_entity(model)

    async def list_steps_for_flow(self, flow_id: UUID) -> list[RecoveryStep]:
        query = (
            select(RecoveryStepModel)
            .where(RecoveryStepModel.flow_id == flow_id)
            .order_by(RecoveryStepModel.step_index)
        )
        result = await self.session.execute(
            self._tenant_filter(query, RecoveryStepModel)
        )
        return [self._step_to_entity(m) for m in result.scalars().all()]

    # ------------------------------------------------------------------
    # Rollup ledger + monthly rollup (spec 009 CL-006 idempotency triple)
    # ------------------------------------------------------------------

    async def apply_to_rollup(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        shopify_order_id: str,
        event_type: RecoveryRollupLedgerEventType,
        amount_cents: int,
        store_local_month: date,
    ) -> bool:
        """Atomically gate-then-update the monthly rollup.

        Returns ``True`` if this call applied the increment; ``False`` if
        a prior call had already applied it (the idempotency hit per spec
        009 CL-006). The caller MUST treat ``False`` as success — the
        rollup is already in the correct state.

        Per the spec, the Shopify additive-mutation path is NOT in this
        transaction — that lives in a separate outbox-pattern worker so
        a Shopify 5xx retry never re-triggers the rollup write.
        """
        # Step 1: try to insert into the dedup ledger.
        ledger_stmt = (
            pg_insert(RecoveryRollupLedgerModel)
            .values(
                tenant_id=tenant_id,
                store_id=store_id,
                shopify_order_id=shopify_order_id,
                event_type=event_type,
                applied_at=datetime.now(UTC),
                applied_amount_cents=amount_cents,
            )
            .on_conflict_do_nothing(
                index_elements=["store_id", "shopify_order_id", "event_type"],
            )
            .returning(RecoveryRollupLedgerModel.applied_at)
        )
        result = await self.session.execute(ledger_stmt)
        applied = result.scalar_one_or_none()
        if applied is None:
            # Already applied — idempotency hit.
            return False

        # Step 2: ledger insert succeeded → safe to mutate the rollup.
        delta_cents = (
            -amount_cents
            if event_type == RecoveryRollupLedgerEventType.REFUNDED
            else amount_cents
        )
        delta_count = (
            1
            if event_type
            in (
                RecoveryRollupLedgerEventType.SUCCEEDED,
                RecoveryRollupLedgerEventType.SUCCEEDED_DEPOSIT,
            )
            else 0
        )

        rollup_stmt = (
            pg_insert(RecoveryMonthlyRollupModel)
            .values(
                tenant_id=tenant_id,
                store_id=store_id,
                month_key=store_local_month,
                recovered_cents=delta_cents,
                recovered_count=delta_count,
            )
            .on_conflict_do_update(
                index_elements=["store_id", "month_key"],
                set_={
                    "recovered_cents": (
                        RecoveryMonthlyRollupModel.recovered_cents + delta_cents
                    ),
                    "recovered_count": (
                        RecoveryMonthlyRollupModel.recovered_count + delta_count
                    ),
                    "updated_at": datetime.now(UTC),
                },
            )
        )
        await self.session.execute(rollup_stmt)
        await self.session.flush()
        return True

    async def get_rollup(
        self, store_id: UUID, month_key: date
    ) -> RecoveryMonthlyRollup | None:
        query = select(RecoveryMonthlyRollupModel).where(
            and_(
                RecoveryMonthlyRollupModel.store_id == store_id,
                RecoveryMonthlyRollupModel.month_key == month_key,
            )
        )
        result = await self.session.execute(
            self._tenant_filter(query, RecoveryMonthlyRollupModel)
        )
        m = result.scalar_one_or_none()
        if m is None:
            return None
        return RecoveryMonthlyRollup(
            store_id=m.store_id,
            month_key=datetime.combine(m.month_key, datetime.min.time(), tzinfo=UTC),
            recovered_cents=m.recovered_cents,
            recovered_count=m.recovered_count,
            updated_at=m.updated_at,
        )
