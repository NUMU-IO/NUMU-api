"""Unit tests for the pre-flight COD-eligibility endpoint (P2-1 backend enabler)."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.api.v1.routes.storefront.checkout_config import (
    CodEligibilityRequest,
    check_cod_eligibility,
)
from src.application.services.cod_trust_service import CodTrustDecision


class _Store:
    def __init__(self, settings):
        self.settings = settings


def _store_repo(settings):
    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=_Store(settings))
    return repo


@pytest.mark.asyncio
async def test_disabled_cod_trust_is_always_available():
    repo = _store_repo({"cod_trust": {"enabled": False}})
    resp = await check_cod_eligibility(
        uuid4(), CodEligibilityRequest(phone="+201001234567"), repo, AsyncMock()
    )
    assert resp.data["cod_available"] is True
    assert resp.data["fallback_payment_methods"] == []


@pytest.mark.asyncio
async def test_blocked_buyer_gets_prepaid_fallbacks(monkeypatch):
    async def _blocked(**_kw):
        return CodTrustDecision(allowed=False, reason="blocked_high_risk")

    monkeypatch.setattr(
        "src.application.services.cod_trust_service.check_customer_trust", _blocked
    )
    repo = _store_repo({"cod_trust": {"enabled": True, "threshold": 70}})
    resp = await check_cod_eligibility(
        uuid4(), CodEligibilityRequest(phone="+201001234567"), repo, AsyncMock()
    )
    assert resp.data["cod_available"] is False
    assert resp.data["fallback_payment_methods"] == ["paymob_card", "paymob_wallet"]


@pytest.mark.asyncio
async def test_allowed_buyer_has_no_fallbacks(monkeypatch):
    async def _allowed(**_kw):
        return CodTrustDecision(allowed=True, reason="below_threshold")

    monkeypatch.setattr(
        "src.application.services.cod_trust_service.check_customer_trust", _allowed
    )
    repo = _store_repo({"cod_trust": {"enabled": True}})
    resp = await check_cod_eligibility(
        uuid4(), CodEligibilityRequest(phone="+201001234567"), repo, AsyncMock()
    )
    assert resp.data["cod_available"] is True
    assert resp.data["fallback_payment_methods"] == []


@pytest.mark.asyncio
async def test_lookup_error_fails_open(monkeypatch):
    async def _boom(**_kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(
        "src.application.services.cod_trust_service.check_customer_trust", _boom
    )
    repo = _store_repo({"cod_trust": {"enabled": True}})
    resp = await check_cod_eligibility(
        uuid4(), CodEligibilityRequest(phone="+201001234567"), repo, AsyncMock()
    )
    assert resp.data["cod_available"] is True


@pytest.mark.asyncio
async def test_unknown_store_raises():
    from src.core.exceptions import EntityNotFoundError

    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=None)
    with pytest.raises(EntityNotFoundError):
        await check_cod_eligibility(uuid4(), CodEligibilityRequest(), repo, AsyncMock())
