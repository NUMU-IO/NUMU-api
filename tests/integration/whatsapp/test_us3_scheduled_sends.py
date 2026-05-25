"""Integration tests for US3 — scheduled WhatsApp sends + dispatcher.

Covers acceptance scenarios from spec.md (US3):
- AS-1 / T053: scheduled send fires within +-2 min of scheduled_for
- AS-2 / T054: explicit cancel before fire-time -> status='cancelled'
- AS-3 / T055: order cancel/refund cascades to all pending sends
  (FR-016 -> handle_order_status_for_scheduled_cancel)
- AS-4 / T056: opt-out flipped between schedule and dispatch -> row
  marks 'skipped' (NOT 'failed') with skip_reason='opt_out' (FR-017)
- AS-5 / T057: two concurrent dispatcher invocations process the same
  row exactly once (FOR UPDATE SKIP LOCKED)

Gated on ``NUMU_RUN_INTEGRATION_TESTS=1``; fixtures land in
``tests/integration/whatsapp/conftest.py``.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.application.use_cases.whatsapp.cancel_scheduled_send import (
    CancelScheduledSendUseCase,
)
from src.application.use_cases.whatsapp.schedule_send import (
    ScheduleSendError,
    ScheduleSendUseCase,
)
from src.core.events.order_events import OrderStatusChangedEvent
from src.core.interfaces.services.messaging_service import (
    MessageChannel,
    MessageResult,
    MessageStatus,
)
from src.infrastructure.events.handlers.whatsapp_scheduled_cancel_handler import (
    handle_order_status_for_scheduled_cancel,
)
from src.infrastructure.messaging.tasks.whatsapp_scheduled_send_dispatcher import (
    _dispatch_for_tenant,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("NUMU_RUN_INTEGRATION_TESTS", "0") != "1",
    reason="DB-backed integration tests; set NUMU_RUN_INTEGRATION_TESTS=1.",
)


# ── T053 / AS-1 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatcher_fires_due_send_within_two_minutes(
    db_session,
    seeded_store_with_active_optin,
    seeded_approved_template,  # APPROVED whatsapp_template
):
    """Create a pending row with scheduled_for in the past, run the
    dispatcher, assert the row transitions to 'sent' and the lag from
    scheduled_for to dispatch is <= 120 seconds.
    """
    store, customer, _optin = seeded_store_with_active_optin

    scheduled_for = datetime.now(UTC) - timedelta(seconds=5)
    # ScheduleSendUseCase rejects past timestamps at the API boundary;
    # we insert via the repo directly to simulate "row is due now".
    from src.infrastructure.repositories.whatsapp_scheduled_send_repository import (
        WhatsAppScheduledSendRepository,
    )

    repo = WhatsAppScheduledSendRepository(db_session)
    row = await repo.create(
        tenant_id=store.tenant_id,
        store_id=store.id,
        phone=customer.phone,
        scheduled_for=scheduled_for,
        template_id=seeded_approved_template.id,
        template_params={"order_number": "TEST-001"},
    )
    await db_session.commit()

    # Mock the dispatch send path so we don't hit Meta.
    with patch(
        "src.infrastructure.messaging.tasks.whatsapp_scheduled_send_dispatcher"
        ".get_whatsapp_service",
        new=AsyncMock(
            return_value=AsyncMock(
                send_text_message=AsyncMock(
                    return_value=MessageResult(
                        success=True,
                        message_id="wamid.fake",
                        channel=MessageChannel.WHATSAPP,
                        status=MessageStatus.SENT,
                    )
                )
            )
        ),
    ):
        stats = await _dispatch_for_tenant(store.tenant_id)

    assert stats["dispatched"] >= 1
    await db_session.refresh(row)
    assert row.status == "sent"
    assert row.dispatched_at is not None
    lag_seconds = (row.dispatched_at - scheduled_for).total_seconds()
    assert lag_seconds <= 120


# ── T054 / AS-2 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_explicit_cancel_blocks_dispatch(
    db_session, seeded_store, seeded_customer, seeded_approved_template
):
    """A row cancelled before scheduled_for must NOT be dispatched."""
    from src.infrastructure.repositories.whatsapp_scheduled_send_repository import (
        WhatsAppScheduledSendRepository,
    )

    repo = WhatsAppScheduledSendRepository(db_session)
    row = await repo.create(
        tenant_id=seeded_store.tenant_id,
        store_id=seeded_store.id,
        phone=seeded_customer.phone,
        scheduled_for=datetime.now(UTC) + timedelta(minutes=5),
        template_id=seeded_approved_template.id,
    )
    await db_session.commit()

    use_case = CancelScheduledSendUseCase(db_session)
    moved = await use_case.execute(row.id)
    assert moved is True
    await db_session.refresh(row)
    assert row.status == "cancelled"

    # Cancelling again should be a no-op (row already cancelled).
    moved_again = await use_case.execute(row.id)
    assert moved_again is False


# ── T055 / AS-3 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_order_cancel_cascades_to_pending_scheduled_sends(
    db_session,
    seeded_store,
    seeded_customer,
    seeded_approved_template,
):
    """OrderStatusChangedEvent(new_status='cancelled') must cascade-
    cancel ALL pending scheduled sends with related_order_id == order_id
    (FR-016)."""
    from src.infrastructure.repositories.whatsapp_scheduled_send_repository import (
        WhatsAppScheduledSendRepository,
    )

    order_id = uuid4()
    repo = WhatsAppScheduledSendRepository(db_session)

    rows = []
    for i in range(3):
        row = await repo.create(
            tenant_id=seeded_store.tenant_id,
            store_id=seeded_store.id,
            phone=seeded_customer.phone,
            scheduled_for=datetime.now(UTC) + timedelta(days=i + 1),
            template_id=seeded_approved_template.id,
            related_order_id=order_id,
        )
        rows.append(row)
    await db_session.commit()

    event = OrderStatusChangedEvent(
        order_id=order_id,
        order_number="A-CANCELLED-001",
        store_id=seeded_store.id,
        store_name=seeded_store.name,
        customer_id=seeded_customer.id,
        previous_status="confirmed",
        new_status="cancelled",
    )
    await handle_order_status_for_scheduled_cancel(event)

    for row in rows:
        await db_session.refresh(row)
        assert row.status == "cancelled"


@pytest.mark.asyncio
async def test_order_refund_also_cascades(
    db_session, seeded_store, seeded_customer, seeded_approved_template
):
    """Refunded is treated the same as cancelled (a refunded order's
    post-delivery review-request must not fire)."""
    from src.infrastructure.repositories.whatsapp_scheduled_send_repository import (
        WhatsAppScheduledSendRepository,
    )

    order_id = uuid4()
    repo = WhatsAppScheduledSendRepository(db_session)
    row = await repo.create(
        tenant_id=seeded_store.tenant_id,
        store_id=seeded_store.id,
        phone=seeded_customer.phone,
        scheduled_for=datetime.now(UTC) + timedelta(days=3),
        template_id=seeded_approved_template.id,
        related_order_id=order_id,
    )
    await db_session.commit()

    event = OrderStatusChangedEvent(
        order_id=order_id,
        order_number="A-REFUNDED-001",
        store_id=seeded_store.id,
        store_name=seeded_store.name,
        customer_id=seeded_customer.id,
        previous_status="delivered",
        new_status="refunded",
    )
    await handle_order_status_for_scheduled_cancel(event)

    await db_session.refresh(row)
    assert row.status == "cancelled"


@pytest.mark.asyncio
async def test_non_terminal_status_change_does_not_cascade(
    db_session, seeded_store, seeded_customer, seeded_approved_template
):
    """Status transitions like 'shipped' must NOT cascade-cancel."""
    from src.infrastructure.repositories.whatsapp_scheduled_send_repository import (
        WhatsAppScheduledSendRepository,
    )

    order_id = uuid4()
    repo = WhatsAppScheduledSendRepository(db_session)
    row = await repo.create(
        tenant_id=seeded_store.tenant_id,
        store_id=seeded_store.id,
        phone=seeded_customer.phone,
        scheduled_for=datetime.now(UTC) + timedelta(days=3),
        template_id=seeded_approved_template.id,
        related_order_id=order_id,
    )
    await db_session.commit()

    event = OrderStatusChangedEvent(
        order_id=order_id,
        order_number="A-001",
        store_id=seeded_store.id,
        store_name=seeded_store.name,
        customer_id=seeded_customer.id,
        previous_status="confirmed",
        new_status="shipped",
    )
    await handle_order_status_for_scheduled_cancel(event)

    await db_session.refresh(row)
    assert row.status == "pending"


# ── T056 / AS-4 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_opt_out_between_schedule_and_dispatch_skips_not_fails(
    db_session,
    seeded_store_with_active_optin,
    seeded_approved_template,
    seeded_customer_opted_out,
):
    """Schedule a send for a customer; flip opt-out before dispatch;
    dispatcher must transition the row to 'skipped' (NOT 'failed') with
    skip_reason='opt_out'. FR-017 — guard re-evaluated at dispatch time."""
    from src.infrastructure.repositories.whatsapp_scheduled_send_repository import (
        WhatsAppScheduledSendRepository,
    )

    store, _customer, _optin = seeded_store_with_active_optin
    repo = WhatsAppScheduledSendRepository(db_session)
    row = await repo.create(
        tenant_id=store.tenant_id,
        store_id=store.id,
        phone=seeded_customer_opted_out.phone,
        scheduled_for=datetime.now(UTC) - timedelta(seconds=5),
        template_id=seeded_approved_template.id,
    )
    await db_session.commit()

    with patch(
        "src.infrastructure.messaging.tasks.whatsapp_scheduled_send_dispatcher"
        ".get_whatsapp_service",
        new=AsyncMock(),
    ) as mock_get_service:
        await _dispatch_for_tenant(store.tenant_id)
        # Should NOT have invoked the messaging service since the guard
        # blocked the send before it could fire.
        mock_get_service.assert_not_called()

    await db_session.refresh(row)
    assert row.status == "skipped"
    assert row.skip_reason == "opt_out"


# ── T057 / AS-5 — concurrent dispatch (SKIP LOCKED) ─────────────────


@pytest.mark.asyncio
async def test_two_concurrent_dispatchers_fire_each_row_once(
    db_session,
    seeded_store_with_active_optin,
    seeded_approved_template,
):
    """Two parallel _dispatch_for_tenant invocations against the same
    due row should result in exactly ONE 'sent' transition — the second
    worker's SELECT...FOR UPDATE SKIP LOCKED returns an empty set."""
    from src.infrastructure.repositories.whatsapp_scheduled_send_repository import (
        WhatsAppScheduledSendRepository,
    )

    store, customer, _optin = seeded_store_with_active_optin
    repo = WhatsAppScheduledSendRepository(db_session)
    row = await repo.create(
        tenant_id=store.tenant_id,
        store_id=store.id,
        phone=customer.phone,
        scheduled_for=datetime.now(UTC) - timedelta(seconds=5),
        template_id=seeded_approved_template.id,
    )
    await db_session.commit()

    happy_mock = AsyncMock(
        return_value=AsyncMock(
            send_text_message=AsyncMock(
                return_value=MessageResult(
                    success=True,
                    message_id=f"wamid.concurrent-{uuid4()}",
                    channel=MessageChannel.WHATSAPP,
                    status=MessageStatus.SENT,
                )
            )
        )
    )

    with patch(
        "src.infrastructure.messaging.tasks.whatsapp_scheduled_send_dispatcher"
        ".get_whatsapp_service",
        new=happy_mock,
    ):
        stats_a, stats_b = await asyncio.gather(
            _dispatch_for_tenant(store.tenant_id),
            _dispatch_for_tenant(store.tenant_id),
        )

    # Exactly one of the two dispatchers must have dispatched the row.
    total_dispatched = stats_a["dispatched"] + stats_b["dispatched"]
    assert total_dispatched == 1

    await db_session.refresh(row)
    assert row.status == "sent"


# ── Schedule-time validation guards ─────────────────────────────────


@pytest.mark.asyncio
async def test_schedule_send_rejects_past_scheduled_for(
    db_session, seeded_store, seeded_customer, seeded_approved_template
):
    use_case = ScheduleSendUseCase(db_session)
    with pytest.raises(ScheduleSendError) as exc:
        await use_case.execute(
            store_id=seeded_store.id,
            phone=seeded_customer.phone,
            scheduled_for=datetime.now(UTC) - timedelta(minutes=1),
            template_id=seeded_approved_template.id,
        )
    assert exc.value.code == "scheduled_in_past"


@pytest.mark.asyncio
async def test_schedule_send_rejects_non_approved_template(
    db_session,
    seeded_store,
    seeded_customer,
    seeded_pending_template,  # status='PENDING'
):
    use_case = ScheduleSendUseCase(db_session)
    with pytest.raises(ScheduleSendError) as exc:
        await use_case.execute(
            store_id=seeded_store.id,
            phone=seeded_customer.phone,
            scheduled_for=datetime.now(UTC) + timedelta(days=1),
            template_id=seeded_pending_template.id,
        )
    assert exc.value.code == "template_not_approved"


@pytest.mark.asyncio
async def test_schedule_send_rejects_payload_xor_violation(
    db_session, seeded_store, seeded_customer, seeded_approved_template
):
    use_case = ScheduleSendUseCase(db_session)

    # Neither template_id nor text_message
    with pytest.raises(ScheduleSendError) as exc:
        await use_case.execute(
            store_id=seeded_store.id,
            phone=seeded_customer.phone,
            scheduled_for=datetime.now(UTC) + timedelta(days=1),
        )
    assert exc.value.code == "payload_invalid"

    # Both
    with pytest.raises(ScheduleSendError) as exc:
        await use_case.execute(
            store_id=seeded_store.id,
            phone=seeded_customer.phone,
            scheduled_for=datetime.now(UTC) + timedelta(days=1),
            template_id=seeded_approved_template.id,
            text_message="hello",
        )
    assert exc.value.code == "payload_invalid"


# ── Fixtures expected at conftest level ─────────────────────────────
#   - db_session
#   - seeded_store, seeded_store_with_active_optin
#   - seeded_customer, seeded_customer_opted_out
#   - seeded_approved_template — WhatsAppTemplateModel with status='APPROVED'
#   - seeded_pending_template — WhatsAppTemplateModel with status='PENDING'
#
# These (plus the Batch-4 fixtures) land in
# tests/integration/whatsapp/conftest.py.
