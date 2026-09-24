"""App webhook events for customers, refunds, shipments, stock and abandoned
checkouts, and private (custom) apps bound to one store."""

import asyncio
import copy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.api.v1.routes import app_oauth, partner_apps
from src.api.v1.routes.admin import apps as admin_apps
from src.application.services.app_manifest import (
    EVENT_SCOPES,
    ManifestV1,
    PrivateManifestV1,
    app_subscriptions,
)
from src.core.entities.app import AppStatus
from src.core.entities.refund import RefundReason, RefundStatus, RefundType
from src.core.entities.webhook import SUBSCRIBABLE_EVENT_TYPES
from src.core.events.base import EventBus
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.database.models.tenant.refund import RefundModel
from src.infrastructure.database.models.tenant.shipment import ShipmentModel
from src.infrastructure.events import commit_watch
from src.infrastructure.messaging.tasks import abandoned_cart_tasks
from tests.unit.test_app_manifest import GOOD

NEW_EVENTS = {
    "customer.created": "customers:read",
    "customer.updated": "customers:read",
    "refund.created": "orders:read",
    "refund.completed": "orders:read",
    "shipment.created": "orders:read",
    "shipment.status_changed": "orders:read",
    "inventory.level_changed": "catalog:read",
    "checkout.abandoned": "orders:read",
}


# ─── Scopes ───────────────────────────────────────────────────────


def test_every_new_event_is_subscribable_and_gated_by_its_read_scope():
    subscribable = {e.value for e in SUBSCRIBABLE_EVENT_TYPES}
    for event, scope in NEW_EVENTS.items():
        assert event in subscribable
        assert EVENT_SCOPES[event] == scope


def test_an_install_only_gets_the_events_its_scopes_allow():
    hooks = [{"event": e, "url": "https://a.example.com/h"} for e in NEW_EVENTS]
    got = app_subscriptions(hooks, ["orders:read"])["https://a.example.com/h"]
    assert set(got) == {e for e, s in NEW_EVENTS.items() if s == "orders:read"}
    got = app_subscriptions(hooks, ["customers:read", "catalog:read"])
    assert set(got["https://a.example.com/h"]) == {
        "customer.created",
        "customer.updated",
        "inventory.level_changed",
    }
    assert app_subscriptions(hooks, []) == {}


def test_a_manifest_must_request_the_scope_of_each_new_event():
    m = copy.deepcopy(GOOD)
    m["webhooks"].append({
        "event": "customer.created",
        "url": "https://a.example.com/h",
    })
    with pytest.raises(ValidationError, match="customer.created needs customers:read"):
        ManifestV1.model_validate(m)
    m["oauth"]["scopes"].append("customers:read")
    ManifestV1.model_validate(m)


# ─── One event per committed change ───────────────────────────────


@pytest.fixture
def captured(monkeypatch):
    commit_watch.install(EventBus())
    events = []
    monkeypatch.setattr(
        commit_watch, "_schedule_immediately", lambda _bus, e, _h: events.append(e)
    )
    monkeypatch.setattr(
        commit_watch._bus, "_handlers", {n: [object()] for n in _EVENT_NAMES}
    )
    return events


_EVENT_NAMES = (
    "CustomerCreatedEvent",
    "CustomerUpdatedEvent",
    "RefundCreatedEvent",
    "RefundCompletedEvent",
    "ShipmentCreatedEvent",
    "ShipmentStatusChangedEvent",
    "InventoryLevelChangedEvent",
)


def _names(events):
    return [e.event_type for e in events]


@pytest.mark.asyncio
async def test_customer_created_then_updated_once_each(test_session, captured):
    store_id = uuid4()
    c = CustomerModel(
        tenant_id=uuid4(),
        store_id=store_id,
        email="a@example.com",
        first_name="A",
        last_name="B",
    )
    test_session.add(c)
    await test_session.flush()
    c.first_name = "Z"
    await test_session.flush()
    await test_session.commit()
    assert _names(captured) == ["CustomerCreatedEvent"]
    assert captured[0].store_id == store_id and captured[0].customer_id == c.id

    captured.clear()
    c.last_name = "Y"
    await test_session.flush()
    c.phone = "+201000000000"
    await test_session.commit()
    assert _names(captured) == ["CustomerUpdatedEvent"]
    assert captured[0].phone == "+201000000000"

    captured.clear()
    c.last_name = "Y"
    await test_session.commit()
    assert captured == []


@pytest.mark.asyncio
async def test_a_rolled_back_change_sends_nothing(test_session, captured):
    test_session.add(
        CustomerModel(
            tenant_id=uuid4(),
            store_id=uuid4(),
            email="a@example.com",
            first_name="A",
            last_name="B",
        )
    )
    await test_session.flush()
    await test_session.rollback()
    await test_session.commit()
    assert captured == []


@pytest.mark.asyncio
async def test_refund_created_then_completed_once(test_session, captured):
    r = RefundModel(
        tenant_id=uuid4(),
        store_id=uuid4(),
        order_id=uuid4(),
        refund_number="R-1",
        refund_type=RefundType.FULL,
        reason=RefundReason.DEFECTIVE,
        status=RefundStatus.REQUESTED,
        amount=1500,
    )
    test_session.add(r)
    await test_session.commit()
    assert _names(captured) == ["RefundCreatedEvent"]
    assert captured[0].amount_cents == 1500 and captured[0].status == "requested"

    captured.clear()
    r.status = RefundStatus.APPROVED
    await test_session.commit()
    assert captured == []

    r.status = RefundStatus.COMPLETED
    await test_session.commit()
    r.reason_note = "done"
    await test_session.commit()
    assert _names(captured) == ["RefundCompletedEvent"]
    assert captured[0].refund_id == r.id


@pytest.mark.asyncio
async def test_shipment_created_then_status_and_tracking(test_session, captured):
    s = ShipmentModel(
        tenant_id=uuid4(), store_id=uuid4(), order_id=uuid4(), carrier="bosta"
    )
    test_session.add(s)
    await test_session.commit()
    assert _names(captured) == ["ShipmentCreatedEvent"]

    captured.clear()
    s.status = "in_transit"
    s.tracking_number = "TRK1"
    await test_session.commit()
    s.cod_collected = True
    await test_session.commit()
    assert _names(captured) == ["ShipmentStatusChangedEvent"]
    assert captured[0].previous_status == "pending"
    assert captured[0].status == "in_transit"
    assert captured[0].tracking_number == "TRK1"


@pytest.mark.asyncio
async def test_stock_change_once_per_transaction(test_session, captured):
    p = ProductModel(
        tenant_id=uuid4(), store_id=uuid4(), name="P", slug="p", quantity=5
    )
    test_session.add(p)
    await test_session.commit()
    assert captured == []

    p.quantity = 4
    await test_session.flush()
    p.quantity = 3
    await test_session.commit()
    assert _names(captured) == ["InventoryLevelChangedEvent"]
    assert captured[0].product_id == p.id and captured[0].variant_id is None


@pytest.mark.asyncio
async def test_abandoned_checkout_is_sent_with_store_and_no_phone(monkeypatch):
    sent = []

    async def handler(event):
        sent.append(event)

    monkeypatch.setattr(
        "src.infrastructure.events.handlers.webhook_handler."
        "handle_webhook_checkout_abandoned",
        handler,
    )
    store_id, checkout_id = uuid4(), uuid4()
    await abandoned_cart_tasks._publish_abandoned(
        store_id=store_id,
        checkout_id=checkout_id,
        items_count=2,
        total_cents=9900,
        currency="EGP",
    )
    assert len(sent) == 1
    data = sent[0].model_dump()
    assert data["store_id"] == store_id and data["checkout_id"] == checkout_id
    assert "phone" not in data


# ─── Private apps ─────────────────────────────────────────────────


def _private_manifest(**over):
    m = copy.deepcopy(GOOD)
    for key in ("tagline", "description", "category", "pricing", "screenshots"):
        m.pop(key, None)
    return {**m, **over}


def test_a_private_manifest_needs_no_listing():
    m = PrivateManifestV1.model_validate(_private_manifest())
    assert m.pricing.model == "free"
    assert m.tagline == m.name
    with pytest.raises(ValidationError):
        ManifestV1.model_validate(_private_manifest())


def test_a_private_app_cannot_be_billed():
    for pricing in (
        {"model": "recurring", "price_cents": 1000, "cycle": "monthly"},
        {"model": "external", "label": {"ar": "حسب الاتفاق", "en": "As agreed"}},
    ):
        with pytest.raises(ValidationError, match="must be free"):
            PrivateManifestV1.model_validate(_private_manifest(pricing=pricing))


def _private_app(store_id):
    return SimpleNamespace(
        id=uuid4(),
        status=AppStatus.PUBLISHED,
        developer_id=uuid4(),
        private_store_id=store_id,
        manifest={
            "app": {
                "oauth": {
                    "redirect_urls": ["https://a.example.com/cb"],
                    "scopes": ["orders:read"],
                }
            }
        },
        listing_flags={},
    )


def _consent(monkeypatch, app, store):
    async def client_app(_db, _cid):
        return app

    async def enabled(_db):
        return True

    class _Db:
        async def get(self, _model, _id):
            return store

    monkeypatch.setattr(app_oauth, "_client_app", client_app)
    monkeypatch.setattr(app_oauth, "partner_apps_enabled", enabled)
    return asyncio.run(
        app_oauth._consentable(
            _Db(),
            user_id=store.owner_id,
            client_id="numu_ci_x",
            store_id=store.id,
            scope="",
            redirect_uri="https://a.example.com/cb",
            state="s",
        )
    )


def test_a_private_app_installs_only_on_its_store(monkeypatch):
    own = SimpleNamespace(id=uuid4(), owner_id=uuid4(), tenant_id=uuid4())
    other = SimpleNamespace(id=uuid4(), owner_id=uuid4(), tenant_id=uuid4())
    app = _private_app(own.id)
    assert _consent(monkeypatch, app, own)[1] is own
    with pytest.raises(HTTPException) as exc:
        _consent(monkeypatch, app, other)
    assert "another store" in exc.value.detail


def test_a_private_app_is_never_reviewed(monkeypatch):
    app = _private_app(uuid4())

    async def own_app(_db, _uid, _aid):
        return app

    monkeypatch.setattr(partner_apps, "_own_app", own_app)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(partner_apps.submit_version(app.id, uuid4(), uuid4(), None))
    assert exc.value.status_code == 409


def test_an_admin_cannot_list_a_private_app(monkeypatch):
    app = _private_app(uuid4())

    class _Db:
        async def get(self, _model, _id):
            return app

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            admin_apps.set_listing_flags(
                app.id, admin_apps.ListingFlags(catalog_visible=True), _Db(), uuid4()
            )
        )
    assert exc.value.status_code == 409
    assert app.listing_flags == {}


def test_the_store_catalog_excludes_private_apps():
    import inspect

    from src.api.v1.routes.stores import apps as store_apps

    assert "AppModel.private_store_id.is_(None)" in inspect.getsource(
        store_apps.list_catalog
    )
