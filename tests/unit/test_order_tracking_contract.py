"""TRK-C-02 / C-03: the shapes the front and the host read.

Every merchant route (``/api/v1/apps/order-tracking``) and every shopper
route (``/storefront/store/{id}/apps/order-tracking``) runs on the test
database, and its JSON is compared, key by key and type by type, with
``tests/fixtures/order_tracking_api/<name>.json``. The shapes are
docs/Plans/APPS/02-order-tracking/CONTRACT.md; the front
(``yousefalimansour/numu-order-tracking``) and the host's check keep copies of
these files, so a change of shape fails here before it reaches them.

Error bodies are recorded as NUMU-api sends them: a raised ``HTTPException``
goes through the app's own exception handler (``{"success": false, "error":
{"code", "message"}}``), the shopper errors go through the real app over HTTP.

``TRK_WRITE_FIXTURES=1`` rewrites the files from this run; copy them to the
front and the host afterwards.
"""

from __future__ import annotations

import json
import os
import pathlib
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api.middleware import error_handler
from src.api.middleware.rate_limit import rate_limit_exceeded_response
from src.api.v1.routes.apps import order_tracking as merchant
from src.api.v1.routes.storefront import apps_order_tracking as shop
from src.application.services import order_tracking_service as svc
from src.infrastructure.database.models.tenant.whatsapp_template import (
    WhatsAppTemplateModel,
)
from src.infrastructure.repositories import order_tracking_repository as repo
from src.main import app as api
from tests.unit import test_order_tracking_app as app_tests
from tests.unit.test_back_in_stock_contract import _shape

#: The app tests' world, reused as is.
world = app_tests.world

FIXTURES = (
    pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "order_tracking_api"
)


def _check(name: str, out) -> None:
    if isinstance(out, JSONResponse):
        out = json.loads(out.body)
    data = json.loads(json.dumps(out, default=str))
    path = FIXTURES / f"{name}.json"
    if os.environ.get("TRK_WRITE_FIXTURES") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    recorded = json.loads(path.read_text(encoding="utf-8"))
    assert _shape(data) == _shape(recorded), f"{name}.json no longer matches"


async def _as_sent(exc: HTTPException, path: str) -> JSONResponse:
    """A raised HTTPException as the client gets it: the app's own handler."""
    scope = {"type": "http", "method": "GET", "path": path, "headers": []}
    return await api.exception_handlers[StarletteHTTPException](Request(scope), exc)


async def _templates(db, store) -> None:
    """The two templates as a store sees them a few days after the backfill."""
    states = {
        ("app_order_tracking_out_for_delivery_v1", "ar"): ("APPROVED", None),
        ("app_order_tracking_out_for_delivery_v1", "en"): ("PENDING", None),
        ("app_order_tracking_delivery_failed_v1", "ar"): ("APPROVED", None),
        ("app_order_tracking_delivery_failed_v1", "en"): (
            "REJECTED",
            "The link must not be the only content.",
        ),
    }
    for (name, lang), (status, reason) in states.items():
        db.add(
            WhatsAppTemplateModel(
                tenant_id=store.tenant_id,
                store_id=store.id,
                name=name,
                language=lang,
                category="UTILITY",
                status=status,
                rejection_reason=reason,
                body_text="…",
                is_system=True,
            )
        )
    await db.flush()


async def _messages(db, x, orders) -> None:
    """A month of messages: three sent, two skipped without access, one
    skipped at the daily cap."""
    rows = [
        (orders[0], svc.OUT_FOR_DELIVERY, 1, svc.SENT, None),
        (orders[1], svc.OUT_FOR_DELIVERY, 1, svc.SENT, None),
        (orders[1], svc.DELIVERY_FAILED, 1, svc.SENT, None),
        (orders[2], svc.OUT_FOR_DELIVERY, 1, svc.SKIPPED, "no_access"),
        (orders[2], svc.DELIVERY_FAILED, 1, svc.SKIPPED, "no_access"),
        (orders[0], svc.DELIVERY_FAILED, 1, svc.SKIPPED, "cap"),
    ]
    for order, kind, attempt, status, reason in rows:
        db.add(
            app_tests.Message(
                store_id=x.store.id,
                tenant_id=x.store.tenant_id,
                order_id=order.id,
                kind=kind,
                attempt=attempt,
                status=status,
                skip_reason=reason,
            )
        )
    await db.flush()


@pytest.mark.asyncio
async def test_every_merchant_route_keeps_its_shape(world, monkeypatch):
    r = await app_tests.rich_store(world)
    s, db = r.x.session, world.db
    await _templates(db, r.x.store)
    await _messages(db, r.x, [r.asked, r.failed, r.late])
    # Shipped by hand: a parcel with no carrier yet.
    by_hand = await world.order(r.x)
    await repo.track(db, by_hand.id)

    _check("settings", await merchant.get_settings(s, db))
    _check("status", await merchant.status(s, db))
    _check("board", await merchant.board(s, db))
    _check("parcels", await merchant.parcels(s, db))
    _check("parcel", await merchant.parcel(s, db, r.asked.id))
    _check("phone", await merchant.phone(s, db, r.asked.id))
    _check("analytics", await merchant.analytics(s, db))
    _check("messages", await merchant.messages(s, db))

    bosta = type("Bosta", (), {"update_delivery": AsyncMock(return_value={})})()
    monkeypatch.setattr(merchant, "service_for_shipment", AsyncMock(return_value=bosta))
    _check("courier_note", await merchant.courier_note(s, db, r.asked.id))
    _check("courier_note_conflict", await merchant.courier_note(s, db, r.asked.id))
    _check("handled", await merchant.handled(s, db, r.asked.id))
    _check(
        "settings_put",
        await merchant.put_settings(s, db, merchant.SettingsIn(daily_cap=150)),
    )
    with pytest.raises(HTTPException) as e:
        await merchant.parcel(s, db, r.x.customer.id)  # no such parcel
    path = f"/api/v1/apps/order-tracking/parcels/{r.x.customer.id}"
    _check("not_found", await _as_sent(e.value, path))


@pytest.mark.asyncio
async def test_every_shopper_route_keeps_its_shape(world, client, monkeypatch):
    x = await world.build()
    on_the_way = await world.order(x, zone_days=(1, 2))
    await world.ship(
        x,
        on_the_way,
        status="out_for_delivery",
        steps=("picked_up", "in_transit", "out_for_delivery"),
    )
    failed, _s = await app_tests._failed(world, x, zone_days=(1, 2))
    delivered = await world.order(
        x, status=app_tests.OrderStatus.DELIVERED, zone_days=(1, 2)
    )
    await world.ship(
        x,
        delivered,
        status="delivered",
        steps=("picked_up", "in_transit", "out_for_delivery", "delivered"),
        delivered_at=app_tests._now(),
    )
    returned = await world.order(x, status=app_tests.OrderStatus.RETURNED)
    await world.ship(
        x,
        returned,
        status="returned",
        steps=("picked_up", "out_for_delivery", "failed", "returned"),
    )
    cancelled = await world.order(x, status=app_tests.OrderStatus.CANCELLED)
    hidden = await world.order(x, status=app_tests.OrderStatus.DRAFT)

    async def view(order):
        return await shop.order_view(x.store.id, order.id, Response(), world.db)

    _check("shop_order", await view(on_the_way))
    _check("shop_order_failed", await view(failed))
    _check("shop_order_delivered", await view(delivered))
    _check("shop_order_returned", await view(returned))
    _check("shop_order_cancelled", await view(cancelled))
    tomorrow = (app_tests._today() + timedelta(days=1)).isoformat()
    body = shop.RetryIn(day=tomorrow, window="evening", note="جنب الصيدلية")
    _check("shop_retry", await shop.retry(x.store.id, failed.id, body, world.db))
    _check(
        "shop_retry_conflict", await shop.retry(x.store.id, failed.id, body, world.db)
    )
    await merchant.put_settings(
        x.session, world.db, merchant.SettingsIn(show_items=False, show_contact=False)
    )
    _check("shop_order_minimal", await view(on_the_way))

    # The answers that are not 200, over HTTP through the whole app, the way
    # the host's relay receives them. Production hides validation details on
    # the storefront (DEBUG off).
    monkeypatch.setattr(error_handler.settings, "debug", False)
    base = f"/api/v1/storefront/store/{x.store.id}/apps/order-tracking/orders"
    elsewhere = (await world.build(live=False)).store.id
    other = f"/api/v1/storefront/store/{elsewhere}/apps/order-tracking/orders"
    for name, method, path, payload, status in (
        ("shop_not_found", "GET", f"{base}/{hidden.id}", None, 404),
        ("shop_not_installed", "GET", f"{other}/{on_the_way.id}", None, 404),
        (
            "shop_retry_not_failed",
            "POST",
            f"{base}/{on_the_way.id}/retry",
            {"day": tomorrow, "window": "evening"},
            409,
        ),
        (
            "shop_retry_bad_day",
            "POST",
            f"{base}/{failed.id}/retry",
            {
                "day": (app_tests._today() + timedelta(days=5)).isoformat(),
                "window": "evening",
            },
            422,
        ),
        (
            "shop_retry_invalid",
            "POST",
            f"{base}/{failed.id}/retry",
            {"day": tomorrow, "window": "noon"},
            422,
        ),
    ):
        answer = await client.request(method, path, json=payload)
        assert answer.status_code == status, name
        _check(name, answer.json())
    # The middleware's 429 for the retry tier (rate_limit.py builds every 429).
    _check("shop_rate_limited", rate_limit_exceeded_response(60))


def test_the_fixtures_match_the_contract_vocabulary():
    """Spot checks that tie the recorded files to CONTRACT.md's words."""

    def load(name: str):
        return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))

    board = load("board")
    assert set(board["counts"]) == set(svc.TILES)
    assert {q["reason"] for q in board["queue"]} == {
        "retry_request",
        "failed",
        "delayed",
    }
    assert None in {
        p["carrier"] and p["carrier"]["key"] for p in load("parcels")["items"]
    }
    order = load("shop_order")
    assert [s["key"] for s in order["steps"]] == list(svc.LINE)
    assert {"items", "total", "contact"} <= set(order)
    minimal = load("shop_order_minimal")
    assert not {"items", "total", "contact"} & set(minimal)
    for name, key in (
        ("shop_order_returned", "returned"),
        ("shop_order_cancelled", "cancelled"),
    ):
        steps = load(name)["steps"]
        assert len(steps) == 6, name
        assert [s["key"] for s in steps if s["state"] == "current"] == [key], name
    messages = load("messages")
    assert set(messages["preview"]) == {svc.OUT_FOR_DELIVERY, svc.DELIVERY_FAILED}
    assert messages["skip_reasons"] == {"no_access": 2, "cap": 1}
    # NUMU-api's two error shapes: the app's own codes flat, everything the
    # framework raises in the envelope.
    assert load("shop_retry_conflict")["code"] == "already_sent"
    assert load("shop_retry_not_failed")["code"] == "not_failed"
    assert load("courier_note_conflict")["code"] == "already_sent"
    assert load("shop_retry_bad_day") == {
        "detail": "The day must be within the next 3 days."
    }
    assert load("shop_not_found")["error"] == {
        "code": "HTTP_ERROR",
        "message": "not found",
    }
    assert load("shop_not_installed")["error"]["message"] == "not installed"
    assert load("not_found")["error"]["code"] == "HTTP_ERROR"
    assert load("shop_retry_invalid")["error"] == {
        "code": "VALIDATION_ERROR",
        "message": "Request validation failed",
    }
    assert load("shop_rate_limited")["code"] == "RATE_LIMIT_EXCEEDED"
