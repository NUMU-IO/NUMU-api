"""Core bugs the Order Tracking app's Phase A found and Phase C fixes
(docs/Plans/APPS/02-order-tracking/PHASES.md, "Core bugs found nearby").
The Mylerz one lives with its siblings in
``tests/unit/api/test_carrier_webhook_order_events.py``."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.core.entities.order import Order, OrderShippingAddress, OrderStatus
from src.core.entities.shipment import Shipment, ShipmentStatus
from src.core.events.base import DomainEvent, EventBus
from tests.unit import app_platform_rows as rows
from tests.unit.test_app_install_gate import _Session

# ─── The built-in page showed the OLDEST forward shipment ─────────────


async def test_the_tracking_page_shows_the_newest_forward_shipment():
    from src.api.v1.routes.storefront.order_tracking import _build_tracking_response

    store_id, tenant_id = uuid4(), uuid4()
    order = Order(
        store_id=store_id,
        tenant_id=tenant_id,
        customer_id=uuid4(),
        order_number="ORD-1",
        status=OrderStatus.SHIPPED,
        shipping_address=OrderShippingAddress(
            first_name="A", last_name="B", address_line1="x", city="Cairo", country="EG"
        ),
    )

    def shipment(tn, status):
        return Shipment(
            store_id=store_id,
            tenant_id=tenant_id,
            order_id=order.id,
            carrier="bosta",
            tracking_number=tn,
            status=status,
        )

    newest_first = [
        shipment("NEW-1", ShipmentStatus.OUT_FOR_DELIVERY),
        shipment("OLD-1", ShipmentStatus.CANCELLED),
    ]  # ShipmentRepository.get_by_order orders by created_at DESC
    out = await _build_tracking_response(
        order,
        SimpleNamespace(name="S", subdomain="s", logo_url=None, custom_domain=None),
        SimpleNamespace(get_by_ids=AsyncMock(return_value=[])),
        SimpleNamespace(get_by_order=AsyncMock(return_value=newest_first)),
    )
    assert out.data.shipment.tracking_number == "NEW-1"
    assert out.data.shipment.status == "out_for_delivery"


# ─── The Bosta poll could never set FAILED or PICKED_UP ───────────────


@pytest.mark.parametrize(
    ("state", "want"),
    [
        ("DELIVERY_FAILED", "failed"),
        ("PICKED_UP", "picked_up"),
        ("IN_TRANSIT", "in_transit"),
        ("OUT_FOR_DELIVERY", "out_for_delivery"),
    ],
)
async def test_bosta_tracking_reports_every_registry_state(state, want):
    from src.infrastructure.external_services.bosta.shipping_service import (
        BostaShippingService,
    )

    response = MagicMock(status_code=200)
    response.json.return_value = {"data": {"state": {"value": state}}}
    with patch("httpx.AsyncClient") as client_class:
        client = MagicMock(get=AsyncMock(return_value=response))
        client_class.return_value.__aenter__.return_value = client
        tracking = await BostaShippingService(api_key="k").track_shipment("Bosta", "B1")
    assert tracking.status == want


async def test_the_poll_records_a_failed_attempt(monkeypatch, test_session):
    """The poll applies a carrier's failure like the webhooks: FAILED, one
    more delivery attempt."""
    import src.application.services.carrier_resolver as resolver
    import src.infrastructure.database.connection as connection
    from src.core.interfaces.services.shipping_service import TrackingInfo
    from src.infrastructure.database.models.tenant.customer import CustomerModel
    from src.infrastructure.database.models.tenant.order import OrderModel
    from src.infrastructure.database.models.tenant.shipment import ShipmentModel
    from src.infrastructure.messaging.tasks import shipment_tasks

    owner = await rows.user(test_session)
    store = await rows.store(test_session, owner)
    customer = CustomerModel(
        id=uuid4(),
        tenant_id=store.tenant_id,
        store_id=store.id,
        email="c@example.com",
        first_name="M",
        last_name="S",
    )
    test_session.add(customer)
    order = OrderModel(
        id=uuid4(),
        tenant_id=store.tenant_id,
        store_id=store.id,
        customer_id=customer.id,
        order_number="ORD-9",
        status=OrderStatus.SHIPPED,
        shipping_address={},
        line_items=[],
    )
    test_session.add(order)
    await test_session.flush()
    shipment = ShipmentModel(
        id=uuid4(),
        tenant_id=store.tenant_id,
        store_id=store.id,
        order_id=order.id,
        carrier="bosta",
        tracking_number="B-9",
        status="out_for_delivery",
        status_history=[],
    )
    test_session.add(shipment)
    await test_session.flush()

    class Carrier:
        async def track_shipment(self, carrier, tracking_number):
            return TrackingInfo(
                carrier=carrier,
                tracking_number=tracking_number,
                status="failed",
                events=[],
            )

    async def service_for(carrier, settings, **kw):
        return Carrier()

    monkeypatch.setattr(connection, "AsyncSessionLocal", lambda: _Session(test_session))
    monkeypatch.setattr(resolver, "service_for_carrier", service_for)

    assert (await shipment_tasks._sync_shipments())["synced"] == 1
    await test_session.refresh(shipment)
    assert (shipment.status, shipment.delivery_attempts) == ("failed", 1)
    assert shipment.status_history[-1]["to"] == "failed"


# ─── Handlers of a Celery task's commits were left on its idle loop ───


class _Probe(DomainEvent):
    n: int = 0


def _strand_a_handler():
    """What a task does: commit (commit_watch schedules the handlers on the
    running loop), then return from ``run_until_complete``."""
    from src.core.events import base

    finished: list[int] = []

    async def handler(event):
        await asyncio.sleep(0.01)  # a real handler awaits its DB
        finished.append(event.n)

    bus = EventBus()
    loop = asyncio.new_event_loop()

    async def task_body():
        base._schedule_immediately(bus, _Probe(n=7), [handler])

    loop.run_until_complete(task_body())
    return loop, finished


def test_handlers_left_on_a_tasks_loop_finish_after_the_task():
    from src.core.events.base import finish_pending_handlers

    loop, finished = _strand_a_handler()
    try:
        assert finished == []  # still waiting when the task returned
        assert finish_pending_handlers() == 1
        assert finished == [7]
        assert finish_pending_handlers() == 0  # nothing left, returns at once
    finally:
        loop.close()


def test_celery_finishes_them_after_every_task():
    from celery.signals import task_postrun

    import src.infrastructure.messaging.celery_app  # noqa: F401 — connects it

    loop, finished = _strand_a_handler()
    try:
        task_postrun.send(sender=None, task_id="t", task=None, args=(), kwargs={})
        assert finished == [7]
    finally:
        loop.close()


def test_a_handler_that_hangs_is_given_up_on():
    from src.core.events import base

    hang = asyncio.Event()

    async def handler(event):
        await hang.wait()

    loop = asyncio.new_event_loop()

    async def task_body():
        base._schedule_immediately(EventBus(), _Probe(), [handler])

    try:
        loop.run_until_complete(task_body())
        started = datetime.now(UTC)
        assert base.finish_pending_handlers(timeout=0.05) == 0
        assert datetime.now(UTC) - started < timedelta(seconds=2)
    finally:
        for task in list(base._inflight):
            if task.get_loop() is loop:
                task.cancel()
        loop.run_until_complete(asyncio.sleep(0))
        loop.close()


# ─── A Partner App install did not clear the store-payload caches ─────


async def test_the_oauth_exchange_busts_the_store_caches(monkeypatch, test_session):
    from src.api.v1.routes import app_oauth
    from src.application.services.app_tokens import sha256_hex
    from src.infrastructure.database.models.public.app import (
        AppInstallationModel,
        AppOAuthClientModel,
        AppOAuthCodeModel,
    )

    owner = await rows.user(test_session)
    store = await rows.store(test_session, owner)
    made = await rows.app(test_session, slug="order-tracking")
    client = await test_session.scalar(
        select(AppOAuthClientModel).where(
            AppOAuthClientModel.client_id == made.client_id
        )
    )
    client.client_secret_hash = sha256_hex(rows.SECRET)
    install = await rows.install(test_session, store, made.app, status="pending_auth")
    test_session.add(
        AppOAuthCodeModel(
            installation_id=install.id,
            code_hash=sha256_hex("the-code"),
            redirect_uri="https://x.example/cb",
            scopes=["orders:read"],
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    await test_session.flush()

    calls: list[str] = []
    commit = test_session.commit

    async def recording_commit():
        calls.append("commit")
        await commit()

    async def revalidate(s):
        calls.append(f"revalidate:{s.id}")

    monkeypatch.setattr(test_session, "commit", recording_commit)
    monkeypatch.setattr(app_oauth, "_revalidate_app_settings", revalidate)

    await app_oauth.token(
        app_oauth.TokenRequest(
            client_id=made.client_id, client_secret=rows.SECRET, code="the-code"
        ),
        test_session,
    )
    assert (await test_session.get(AppInstallationModel, install.id)).status == "active"
    assert calls == ["commit", f"revalidate:{store.id}"]  # bust after the commit


async def test_a_dev_install_busts_the_store_caches(monkeypatch):
    from src.api.v1.routes import partner_apps

    store = SimpleNamespace(id=uuid4(), tenant_id=uuid4(), subdomain="dev-a")
    app = SimpleNamespace(id=uuid4(), slug="order-tracking", private_store_id=None)
    calls: list[str] = []
    found = MagicMock()
    found.scalar_one_or_none.return_value = store
    db = SimpleNamespace(
        execute=AsyncMock(return_value=found),
        commit=AsyncMock(side_effect=lambda: calls.append("commit")),
    )

    async def revalidate(s):
        calls.append(f"revalidate:{s.id}")

    monkeypatch.setattr(partner_apps, "_own_app", AsyncMock(return_value=app))
    monkeypatch.setattr(partner_apps, "cancel_purge", AsyncMock())
    monkeypatch.setattr(partner_apps, "_revalidate_app_settings", revalidate)

    await partner_apps.dev_install(
        app.id, partner_apps.DevInstallRequest(store_id=store.id), uuid4(), db
    )
    assert calls == ["commit", f"revalidate:{store.id}"]
