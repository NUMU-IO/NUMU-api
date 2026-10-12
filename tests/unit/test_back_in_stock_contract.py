"""BIS-C-01: the shapes the app's front reads from ``/api/v1/apps/back-in-stock``.

Every merchant route runs on the test database, and its JSON is compared,
key by key and type by type, with ``tests/fixtures/back_in_stock_api/<route>.json``.
The front (``yousefalimansour/numu-back-in-stock``) keeps a copy of the same
files in ``test/fixtures/api/`` and its vitest suite renders from them, so a
change of shape fails here before it reaches the front.

``BIS_WRITE_FIXTURES=1`` rewrites the files from this run; copy them to the
front afterwards.
"""

from __future__ import annotations

import json
import os
import pathlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.api.v1.routes.apps import back_in_stock as merchant
from src.application.services import back_in_stock as bis
from src.application.services import back_in_stock_whatsapp as wa
from src.infrastructure.database.models.tenant.theme import (
    StoreThemeModel,
    ThemeModel,
    ThemeVersionModel,
)
from src.infrastructure.database.models.tenant.whatsapp_template import (
    WhatsAppTemplateModel,
)
from tests.unit import test_back_in_stock_app as app_tests
from tests.unit.test_back_in_stock_app import _subscribe, _waiters

#: The app tests' store fixture, reused as is.
world = app_tests.world

FIXTURES = (
    pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "back_in_stock_api"
)


def _shape(value):
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(value[0])] if value else []
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    return "null" if value is None else "string"


def _check(name: str, out: dict) -> None:
    data = json.loads(json.dumps(out, default=str))
    path = FIXTURES / f"{name}.json"
    if os.environ.get("BIS_WRITE_FIXTURES") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    recorded = json.loads(path.read_text(encoding="utf-8"))
    assert _shape(data) == _shape(recorded), f"{name}.json no longer matches"


async def _store_with_history(world):
    """A store as the dashboard sees it after a few weeks: a slot-ready theme,
    the template waiting for Meta, one shopper waiting, one alerted who bought,
    one whose alert failed."""
    x = await world.build()
    db = world.db
    theme = ThemeModel(
        id=uuid4(), name="Vionne", slug=f"vionne-{uuid4().hex[:6]}", type="internal"
    )
    db.add(theme)
    await db.flush()
    version = ThemeVersionModel(
        id=uuid4(),
        theme_id=theme.id,
        version="0.11.18",
        bundle_url="https://cdn.numueg.app/vionne-v3/0.11.18/theme.js",
        checksum="0" * 64,
        manifest={"id": "vionne-v3", "version": "0.11.18"},
    )
    db.add(version)
    await db.flush()
    db.add(
        StoreThemeModel(
            id=uuid4(),
            tenant_id=x.store.tenant_id,
            store_id=x.store.id,
            theme_id=theme.id,
            theme_version_id=version.id,
            is_active=True,
        )
    )
    for lang in ("ar", "en"):
        db.add(
            WhatsAppTemplateModel(
                tenant_id=x.store.tenant_id,
                store_id=x.store.id,
                name=bis.TEMPLATE_NAME,
                language=lang,
                category="UTILITY",
                status="PENDING",
                body_text="…",
                is_system=True,
            )
        )
    await db.flush()

    await _subscribe(world, x, phone="01012345678")
    await _subscribe(world, x, phone="01112345678", locale="en")
    await _subscribe(world, x, phone=None, email="mona@gmail.com")
    waiting, bought, failed = sorted(
        await _waiters(db, x.store.id), key=lambda w: w.contact
    )
    now = datetime.now(UTC)
    bought.status, bought.notified_at = bis.NOTIFIED, now - timedelta(days=2)
    bought.message_id, bought.clicked_at = "wamid.1", now - timedelta(days=2)
    bought.purchased_at, bought.order_id = now - timedelta(days=1), uuid4()
    bought.revenue = 25000
    failed.status, failed.fail_reason = bis.FAILED, "email_failed"
    await db.flush()
    return x, waiting


@pytest.mark.asyncio
async def test_every_merchant_route_keeps_its_shape(world, monkeypatch):
    x, waiting = await _store_with_history(world)
    session, db = x.session, world.db

    _check("status", await merchant.status(session, db))
    _check("overview", await merchant.overview(session, db))
    _check("waitlist", await merchant.waitlist(session, db))
    _check(
        "waiters",
        await merchant.waiters(session, db, x.product.id, variant_id=x.medium.id),
    )
    _check("activity", await merchant.activity(session, db))
    _check("settings", await merchant.get_settings(session, db))
    _check(
        "notify_now",
        await merchant.notify_now(
            session,
            db,
            merchant.NotifyNow(product_id=x.product.id, variant_id=x.small.id),
        ),
    )
    x.owner.phone = "01098765432"
    await db.flush()
    monkeypatch.setattr(wa, "send_alert", AsyncMock(return_value=("wamid.t", None)))
    _check(
        "test_send",
        await merchant.test_send(session, db, merchant.TestSend(channel="whatsapp")),
    )
    _check("delete_waiter", await merchant.delete_waiter(session, db, waiting.id))
