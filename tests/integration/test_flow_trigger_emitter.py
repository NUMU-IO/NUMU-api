"""Integration tests for backend-020 Flow Trigger Emitter.

Covers the dedup-key construction (pure-Python), the emission log
idempotency (DB-backed), and the event-bus subscription wiring.
The actual Shopify Admin GraphQL call is the v1 stub — assertion
on the persisted log row + the dedup behaviour is what catches
regressions in production.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.flow_trigger_dedup_keys import (
    TRIGGER_RECOVERY_ABANDONED,
    TRIGGER_RECOVERY_SUCCEEDED,
    TRIGGER_RISK_SCORE_CALCULATED,
    dedup_key_cod_verification,
    dedup_key_network_threshold,
    dedup_key_recovery_abandoned,
    dedup_key_recovery_succeeded,
    dedup_key_risk_score_calculated,
)
from src.core.events.recovery_events import (
    RecoveryAbandonedEvent,
    RecoverySucceededEvent,
)
from src.core.events.risk_events import RiskAssessmentFinalisedEvent
from src.infrastructure.database.models.tenant.flow_trigger_emission_log import (
    FlowTriggerEmissionLogModel,
)

# ---------------------------------------------------------------------------
# Dedup-key construction (pure Python — no DB)
# ---------------------------------------------------------------------------


class TestDedupKeyShape:
    """Backend-020 FR-003 — dedup keys are deterministic per the spec's contract."""

    def test_risk_score_calculated_includes_score_type(self):
        """Preliminary + final scores fire separately, not deduped together."""
        prelim = dedup_key_risk_score_calculated("123", "preliminary")
        final = dedup_key_risk_score_calculated("123", "final")
        assert prelim != final
        assert prelim == "123:preliminary"
        assert final == "123:final"

    def test_cod_verification_includes_transition(self):
        """Multiple verifications on the same order fire separately."""
        first = dedup_key_cod_verification("123", "t1")
        second = dedup_key_cod_verification("123", "t2")
        assert first != second
        assert first == "123:verification:t1"

    def test_network_threshold_per_period(self):
        """Same hashed phone × same threshold × same period = one fire."""
        period = date(2026, 5, 1)
        a = dedup_key_network_threshold("hash123", 3, period)
        b = dedup_key_network_threshold("hash123", 3, period)
        assert a == b
        assert a == "hash123:3:2026-05-01"

    def test_recovery_succeeded_dedup(self):
        """Constitution v1.2.0 FR-010 dedup pattern."""
        assert dedup_key_recovery_succeeded("ord-1") == "ord-1:recovery_succeeded"

    def test_recovery_abandoned_dedup(self):
        assert dedup_key_recovery_abandoned("ord-1") == "ord-1:recovery_abandoned"


# ---------------------------------------------------------------------------
# Emission-log unique-constraint behavior
# ---------------------------------------------------------------------------


class TestEmissionLogIdempotency:
    """Backend-020 FR-002 — unique constraint enforces emission idempotency."""

    @pytest.mark.asyncio
    async def test_duplicate_emission_log_row_blocked(self, test_session: AsyncSession):
        """Inserting two rows with the same (store_id, dedup_key, trigger_handle)
        violates the constraint — the unique-index gate Backend-020 FR-002 relies on.
        """
        store_id = uuid4()
        tenant_id = uuid4()

        first = FlowTriggerEmissionLogModel(
            tenant_id=tenant_id,
            store_id=store_id,
            source_event_id="evt-1",
            trigger_handle=TRIGGER_RECOVERY_SUCCEEDED,
            dedup_key="ord-A:recovery_succeeded",
            status="succeeded",
            attempted_at=datetime.now(UTC),
            payload_snapshot={"order": {"id": "ord-A"}},
        )
        test_session.add(first)
        await test_session.flush()

        # Same key tuple → second insert fails.
        second = FlowTriggerEmissionLogModel(
            tenant_id=tenant_id,
            store_id=store_id,
            source_event_id="evt-2",  # Different source event but same dedup key
            trigger_handle=TRIGGER_RECOVERY_SUCCEEDED,
            dedup_key="ord-A:recovery_succeeded",
            status="pending",
            attempted_at=datetime.now(UTC),
            payload_snapshot={"order": {"id": "ord-A"}},
        )
        test_session.add(second)
        with pytest.raises(IntegrityError):
            await test_session.flush()

    @pytest.mark.asyncio
    async def test_on_conflict_do_nothing_pattern(self, test_session: AsyncSession):
        """Mirrors the actual Celery task pattern — INSERT ... ON CONFLICT DO NOTHING."""
        store_id = uuid4()
        tenant_id = uuid4()
        common = {
            "tenant_id": tenant_id,
            "store_id": store_id,
            "source_event_id": "evt-X",
            "trigger_handle": TRIGGER_RISK_SCORE_CALCULATED,
            "dedup_key": "ord-X:final",
            "status": "succeeded",
            "attempted_at": datetime.now(UTC),
            "payload_snapshot": {"score": 75},
        }

        stmt = (
            pg_insert(FlowTriggerEmissionLogModel)
            .values(**common)
            .on_conflict_do_nothing(constraint="uq_flow_trigger_dedup")
            .returning(FlowTriggerEmissionLogModel.id)
        )
        first = await test_session.execute(stmt)
        first_id = first.scalar_one_or_none()
        assert first_id is not None  # Inserted

        second = await test_session.execute(stmt)
        second_id = second.scalar_one_or_none()
        assert second_id is None  # Conflict → no row returned

        # Exactly one row exists.
        count_q = select(FlowTriggerEmissionLogModel).where(
            FlowTriggerEmissionLogModel.dedup_key == "ord-X:final",
        )
        result = await test_session.execute(count_q)
        rows = list(result.scalars().all())
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Different (store, trigger, dedup) tuples can coexist
# ---------------------------------------------------------------------------


class TestEmissionLogDistinctness:
    @pytest.mark.asyncio
    async def test_same_dedup_different_store_allowed(self, test_session: AsyncSession):
        """Two stores can independently emit for the same Shopify order id."""
        store_a = uuid4()
        store_b = uuid4()
        tenant_id = uuid4()
        for sid in (store_a, store_b):
            row = FlowTriggerEmissionLogModel(
                tenant_id=tenant_id,
                store_id=sid,
                source_event_id="evt",
                trigger_handle=TRIGGER_RECOVERY_SUCCEEDED,
                dedup_key="ord-Z:recovery_succeeded",
                status="succeeded",
                attempted_at=datetime.now(UTC),
                payload_snapshot={},
            )
            test_session.add(row)
        await test_session.flush()  # Both inserts succeed.

    @pytest.mark.asyncio
    async def test_different_trigger_handles_allowed(self, test_session: AsyncSession):
        """Same (store, dedup) but different trigger handle → independent emissions."""
        store_id = uuid4()
        tenant_id = uuid4()
        for handle in (TRIGGER_RECOVERY_SUCCEEDED, TRIGGER_RECOVERY_ABANDONED):
            row = FlowTriggerEmissionLogModel(
                tenant_id=tenant_id,
                store_id=store_id,
                source_event_id="evt",
                trigger_handle=handle,
                dedup_key="ord-Y:something",
                status="succeeded",
                attempted_at=datetime.now(UTC),
                payload_snapshot={},
            )
            test_session.add(row)
        await test_session.flush()


# ---------------------------------------------------------------------------
# Event-bus subscription wiring smoke test
# ---------------------------------------------------------------------------


class TestFlowTriggerHandlerWiring:
    """Catches accidental removal of the flow-trigger handlers in setup.py."""

    def test_flow_trigger_handlers_subscribed(self):
        from src.infrastructure.events.handlers.flow_trigger_handler import (
            handle_recovery_abandoned_for_flow_trigger,
            handle_recovery_succeeded_for_flow_trigger,
            handle_risk_finalised_for_flow_trigger,
        )
        from src.infrastructure.events.setup import create_event_bus

        bus = create_event_bus()

        risk_handlers = bus._handlers.get(RiskAssessmentFinalisedEvent.__name__, [])
        succeeded_handlers = bus._handlers.get(RecoverySucceededEvent.__name__, [])
        abandoned_handlers = bus._handlers.get(RecoveryAbandonedEvent.__name__, [])

        assert handle_risk_finalised_for_flow_trigger in risk_handlers
        assert handle_recovery_succeeded_for_flow_trigger in succeeded_handlers
        assert handle_recovery_abandoned_for_flow_trigger in abandoned_handlers
