"""Gate + FSM tests for the WhatsApp access-request feature.

Runs on the default in-memory SQLite harness (tests/conftest.py ``test_session``)
— no Postgres and no ``NUMU_RUN_INTEGRATION_TESTS`` needed.

The point being verified: the access gate keys off the per-store access ROW
status, completely independent of the WhatsApp connection mode. So it gates the
**NUMU shared number (platform_managed)** exactly the same as BYO — a
platform_managed store still cannot turn on notifications until it is APPROVED,
and a disabled store is blocked again. That is the primary flow (most merchants
use the NUMU number, never touching BYO).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.v1.routes.admin.whatsapp_access import _transition
from src.api.v1.routes.stores.whatsapp import _require_whatsapp_access_approved
from src.infrastructure.database.models.public.whatsapp_access import (
    WhatsAppAccessRequestModel,
    WhatsAppAccessStatus,
)
from src.infrastructure.database.models.tenant.store import StoreModel


class _StoreRef:
    """Minimal stand-in for the Store entity the gate helper reads (`.id`)."""

    def __init__(self, store_id):
        self.id = store_id


async def _make_store(session, *, name="NUMU Number Store") -> StoreModel:
    store = StoreModel(
        id=uuid4(),
        tenant_id=uuid4(),
        owner_id=uuid4(),
        name=name,
        slug=f"store-{uuid4().hex[:8]}",
        country="EG",
        default_language="ar",
        settings={},
    )
    session.add(store)
    await session.commit()
    return store


async def _access_row(session, store_id, status: WhatsAppAccessStatus):
    row = WhatsAppAccessRequestModel(
        store_id=store_id,
        tenant_id=uuid4(),
        requester_user_id=uuid4(),
        status=status,
    )
    session.add(row)
    await session.commit()
    return row


# ── The gate that fronts connect + notifications (works for the NUMU number) ──


@pytest.mark.asyncio
async def test_gate_blocks_when_no_access_row(test_session):
    """A brand-new store (no request) cannot turn WhatsApp on → 403."""
    store = _StoreRef(uuid4())
    with pytest.raises(HTTPException) as exc:
        await _require_whatsapp_access_approved(store, test_session)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "whatsapp_access_not_approved"


@pytest.mark.asyncio
async def test_gate_blocks_when_pending(test_session):
    store = _StoreRef(uuid4())
    await _access_row(test_session, store.id, WhatsAppAccessStatus.PENDING)
    with pytest.raises(HTTPException) as exc:
        await _require_whatsapp_access_approved(store, test_session)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_gate_allows_when_approved_numu_number(test_session):
    """Approved store passes the gate — this is what lets a platform_managed
    (NUMU shared number) store enable notifications. No BYO credential needed."""
    store = _StoreRef(uuid4())
    await _access_row(test_session, store.id, WhatsAppAccessStatus.APPROVED)
    # Must NOT raise.
    await _require_whatsapp_access_approved(store, test_session)


@pytest.mark.asyncio
async def test_gate_blocks_again_when_disabled(test_session):
    """Admin kill-switch: a disabled store is blocked again (403)."""
    store = _StoreRef(uuid4())
    await _access_row(test_session, store.id, WhatsAppAccessStatus.DISABLED)
    with pytest.raises(HTTPException) as exc:
        await _require_whatsapp_access_approved(store, test_session)
    assert exc.value.status_code == 403


# ── Admin FSM: approve → disable → enable, and an illegal transition ──────────


@pytest.mark.asyncio
async def test_admin_fsm_pending_to_approved_to_disabled_to_enabled(test_session):
    store = await _make_store(test_session)
    row = await _access_row(test_session, store.id, WhatsAppAccessStatus.PENDING)
    admin_id = uuid4()

    approved = await _transition(
        test_session, row.id, WhatsAppAccessStatus.APPROVED, admin_id, "ok"
    )
    assert approved.status == "approved"
    assert approved.reviewer_user_id == admin_id
    assert approved.store_name == "NUMU Number Store"

    disabled = await _transition(
        test_session, row.id, WhatsAppAccessStatus.DISABLED, admin_id, "kill"
    )
    assert disabled.status == "disabled"

    re_enabled = await _transition(
        test_session, row.id, WhatsAppAccessStatus.APPROVED, admin_id, "back on"
    )
    assert re_enabled.status == "approved"


@pytest.mark.asyncio
async def test_admin_fsm_rejects_illegal_transition(test_session):
    """disable is not reachable from rejected → 409 (FSM guard holds)."""
    store = await _make_store(test_session)
    row = await _access_row(test_session, store.id, WhatsAppAccessStatus.REJECTED)
    with pytest.raises(HTTPException) as exc:
        await _transition(
            test_session, row.id, WhatsAppAccessStatus.DISABLED, uuid4(), None
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_admin_fsm_unknown_request_404(test_session):
    with pytest.raises(HTTPException) as exc:
        await _transition(
            test_session, uuid4(), WhatsAppAccessStatus.APPROVED, uuid4(), None
        )
    assert exc.value.status_code == 404
