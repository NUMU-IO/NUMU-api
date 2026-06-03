"""Unit tests for the COD recovery WhatsApp offer scheduler (recover flow 2/2)."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.application.services.cod_recovery_service import schedule_cod_recovery_offer


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Tmpl:
    def __init__(self, status="APPROVED"):
        self.id = uuid4()
        self.status = status


def _order():
    o = type("O", (), {})()
    o.id = uuid4()
    o.store_id = uuid4()
    o.tenant_id = uuid4()
    o.customer_id = uuid4()
    o.order_number = "ORD-1"
    o.total = 25000
    o.currency = "EGP"
    o.line_items = []
    return o


def _store(promo=None, subdomain="acme"):
    s = type("S", (), {})()
    s.name = "Acme"
    s.subdomain = subdomain
    s.default_language = "ar"
    s.settings = {"cod_trust": {"recovery_promo": promo} if promo else {}}
    return s


def _customer(phone="+201001234567"):
    c = type("C", (), {})()
    c.phone = phone
    c.first_name = "Sara"
    c.last_name = "M"
    return c


def _patch_repo(monkeypatch):
    repo = AsyncMock()  # .create is an AsyncMock
    monkeypatch.setattr(
        "src.infrastructure.repositories.whatsapp_scheduled_send_repository."
        "WhatsAppScheduledSendRepository",
        lambda session: repo,
    )
    return repo


@pytest.mark.asyncio
async def test_skips_without_phone():
    session = AsyncMock()
    ok = await schedule_cod_recovery_offer(
        session, order=_order(), store=_store(), customer=_customer(phone=None)
    )
    assert ok is False
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_skips_without_template(monkeypatch):
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_Result([]))
    _patch_repo(monkeypatch)
    ok = await schedule_cod_recovery_offer(
        session, order=_order(), store=_store(), customer=_customer()
    )
    assert ok is False


@pytest.mark.asyncio
async def test_schedules_with_template_and_promo(monkeypatch):
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_Result([_Tmpl("APPROVED")]))
    repo = _patch_repo(monkeypatch)
    ok = await schedule_cod_recovery_offer(
        session, order=_order(), store=_store(promo="10% off"), customer=_customer()
    )
    assert ok is True
    repo.create.assert_awaited_once()
    params = repo.create.await_args.kwargs["template_params"]
    assert params["promo"] == "10% off"
    assert params["pay_payload"].startswith("acme/")
    assert params["order_number"] == "ORD-1"


@pytest.mark.asyncio
async def test_error_is_graceful():
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=RuntimeError("db down"))
    ok = await schedule_cod_recovery_offer(
        session, order=_order(), store=_store(), customer=_customer()
    )
    assert ok is False
