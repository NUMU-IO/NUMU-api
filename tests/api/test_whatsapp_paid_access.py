"""WhatsApp access is sold, not granted — and the gate holds.

The channel costs NUMU money at Meta on every template message, so the access
row an admin used to approve for free now carries a price, a paid period and a
message allowance. Three things must hold, and each one costs real money if it
does not:

* a store whose period has lapsed cannot send templates;
* a store that has burned its allowance cannot send templates;
* a store that never paid cannot send templates on either transport, whichever
  path the send came from.

Pure-logic tests plus a fake session; no Postgres.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.application.services.whatsapp_entitlement import (
    Entitlement,
    activate_paid_access,
    entitlement,
    open_payment,
    period_start,
)
from src.core.interfaces.services.messaging_service import (
    MessageContent,
    MessageRecipient,
    MessageType,
)
from src.infrastructure.database.models.public.whatsapp_access import (
    WhatsAppAccessStatus,
)
from src.infrastructure.external_services.whatsapp.gowa_provider import GowaProvider
from src.infrastructure.external_services.whatsapp.messaging_service import (
    WhatsAppMessagingService,
)


def _content():
    return MessageContent(
        type=MessageType.ORDER_CONFIRMATION,
        recipient=MessageRecipient(phone="+201000000000", language="ar"),
        template_params={},
    )


@pytest.mark.asyncio
async def test_unpaid_store_cannot_send_a_template_on_meta(monkeypatch):
    service = WhatsAppMessagingService(access_token="t", phone_number_id="p")
    service.enabled = True
    service.access_active = False
    service.access_blocked_reason = "awaiting_payment"

    result = await service.send_message(_content())

    assert result.success is False
    assert result.error_code == "whatsapp_access_not_active"


@pytest.mark.asyncio
async def test_unpaid_store_cannot_send_a_template_on_gowa():
    """GOWA costs NUMU nothing at Meta, but the feature is still what is sold."""
    provider = GowaProvider(device_id="dev-1", base_url="http://gowa", basic_auth="a:b")
    provider.access_active = False

    result = await provider.send_message(_content())

    assert result.success is False
    assert result.error_code == "whatsapp_access_not_active"


def test_bare_transports_default_to_sending():
    """Webhook signature checks and tests build transports by hand."""
    assert WhatsAppMessagingService().access_active is True
    assert GowaProvider(device_id="").access_active is True


@pytest.mark.asyncio
async def test_resolver_stamps_every_transport_with_the_entitlement(monkeypatch):
    """Every send path resolves its transport here — that is what makes one
    lookup cover digests, nudges, campaigns and OTPs alike."""
    import src.infrastructure.external_services.whatsapp as wa

    transport = WhatsAppMessagingService()

    async def fake_resolve(*_args, **_kwargs):
        return transport

    async def fake_entitlement(_db, _store_id):
        return Entitlement(active=False, status="none", reason="not_requested")

    monkeypatch.setattr(wa, "_resolve_transport", fake_resolve)
    monkeypatch.setattr(
        "src.application.services.whatsapp_entitlement.entitlement", fake_entitlement
    )

    service = await wa.get_whatsapp_service(uuid4(), object())

    assert service.access_active is False
    assert service.access_blocked_reason == "not_requested"


class _Result:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeSession:
    def __init__(self, row=None, message_count=0):
        self.row = row
        self.message_count = message_count

    async def execute(self, _query):
        return _Result(self.row)

    async def scalar(self, _query):
        return self.message_count


def _row(**overrides):
    row = SimpleNamespace(
        store_id=uuid4(),
        status=WhatsAppAccessStatus.APPROVED,
        active_until=datetime.now(UTC) + timedelta(days=10),
        message_allowance=300,
        billing_cycle="monthly",
        payment_intent_id=None,
        reviewed_at=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


@pytest.mark.asyncio
async def test_no_row_means_no_access():
    live = await entitlement(_FakeSession(None), uuid4())
    assert live.active is False
    assert live.reason == "not_requested"


@pytest.mark.asyncio
async def test_awaiting_payment_is_not_access():
    row = _row(status=WhatsAppAccessStatus.AWAITING_PAYMENT)
    live = await entitlement(_FakeSession(row), row.store_id)
    assert live.active is False
    assert live.reason == "awaiting_payment"


@pytest.mark.asyncio
async def test_lapsed_period_blocks_before_the_sweep_runs():
    """The sweep is cosmetic; the read is what stops the money going out."""
    row = _row(active_until=datetime.now(UTC) - timedelta(minutes=1))
    live = await entitlement(_FakeSession(row), row.store_id)
    assert live.active is False
    assert live.reason == "expired"


@pytest.mark.asyncio
async def test_allowance_exhausted_blocks():
    row = _row(message_allowance=300)
    live = await entitlement(_FakeSession(row, message_count=300), row.store_id)
    assert live.active is False
    assert live.reason == "allowance_exhausted"
    assert live.remaining == 0


@pytest.mark.asyncio
async def test_inside_allowance_sends_and_reports_what_is_left():
    row = _row(message_allowance=300)
    live = await entitlement(_FakeSession(row, message_count=120), row.store_id)
    assert live.active is True
    assert live.used == 120
    assert live.remaining == 180


@pytest.mark.asyncio
async def test_legacy_free_grant_keeps_working():
    """An APPROVED row from before pricing existed has no expiry, and stays on."""
    row = _row(active_until=None, message_allowance=None)
    live = await entitlement(_FakeSession(row), row.store_id)
    assert live.active is True
    assert live.remaining is None


@pytest.mark.asyncio
async def test_paying_early_adds_to_the_time_left():
    """Renewing with days remaining must never throw those days away."""
    now = datetime.now(UTC)
    row = _row(active_until=now + timedelta(days=10))
    session = _FakeSession(row)

    await activate_paid_access(
        session, store_id=row.store_id, billing_cycle="monthly", now=now
    )

    assert row.active_until == now + timedelta(days=40)
    assert row.status == WhatsAppAccessStatus.APPROVED


@pytest.mark.asyncio
async def test_paying_after_lapsing_starts_from_today():
    now = datetime.now(UTC)
    row = _row(
        status=WhatsAppAccessStatus.EXPIRED,
        active_until=now - timedelta(days=5),
    )
    session = _FakeSession(row)

    await activate_paid_access(
        session, store_id=row.store_id, billing_cycle="monthly", now=now
    )

    assert row.active_until == now + timedelta(days=30)
    assert row.status == WhatsAppAccessStatus.APPROVED


def test_usage_period_is_anchored_to_the_paid_period():
    """Not the calendar month: the allowance resets on the date they paid."""
    now = datetime.now(UTC)
    row = _row(active_until=now + timedelta(days=4), billing_cycle="monthly")
    assert period_start(row, now) == row.active_until - timedelta(days=30)


def test_remaining_is_none_when_uncapped():
    assert Entitlement(active=True, status="approved").remaining is None


class _Intent(SimpleNamespace):
    pass


class _BillSession(_FakeSession):
    def __init__(self, row, intent):
        super().__init__(row)
        self.intent = intent
        self.added = []

    async def get(self, _model, _id):
        return self.intent

    def add(self, obj):
        self.added.append(obj)


@pytest.mark.asyncio
async def test_coming_back_to_pay_shows_the_same_reference():
    """A merchant who wrote the code in a transfer note must find it again."""
    now = datetime.now(UTC)
    open_bill = _Intent(
        amount_cents=5000,
        billing_cycle="monthly",
        status="awaiting_proof",
        expires_at=now + timedelta(days=3),
        special_reference="SUB-ABC123",
    )
    row = _row(
        status=WhatsAppAccessStatus.AWAITING_PAYMENT,
        amount_cents=5000,
        payment_intent_id=uuid4(),
    )
    session = _BillSession(row, open_bill)

    intent = await open_payment(session, row, created_by_user_id=None, now=now)

    assert intent is open_bill
    assert session.added == []
