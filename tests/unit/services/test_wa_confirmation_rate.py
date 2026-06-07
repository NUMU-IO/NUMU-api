"""Unit tests for the WhatsApp order-confirmation response rate (P1-4)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.infrastructure.messaging.tasks import risk_scoring_tasks as mod


class TestWaConfirmationResponseRate:
    @pytest.mark.asyncio
    async def test_returns_zero_without_tenant(self):
        assert await mod._wa_confirmation_response_rate(None, uuid4(), "+201") == 0.0

    @pytest.mark.asyncio
    async def test_returns_zero_without_phone(self):
        assert await mod._wa_confirmation_response_rate(uuid4(), uuid4(), None) == 0.0

    @pytest.mark.asyncio
    async def test_computes_responded_over_requested(self, monkeypatch):
        async def _noop_narrow(session, tenant_id):
            return None

        monkeypatch.setattr(
            "src.infrastructure.tenancy.rls.narrow_to_tenant", _noop_narrow
        )

        counts = iter([4, 3])  # requested=4, responded=3 → 75.0

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def scalar(self, *a, **k):
                return next(counts)

        monkeypatch.setattr(
            "src.infrastructure.database.connection.AsyncSessionLocal",
            lambda: _Session(),
        )

        rate = await mod._wa_confirmation_response_rate(
            uuid4(), uuid4(), "+201001234567"
        )
        assert rate == 75.0

    @pytest.mark.asyncio
    async def test_no_requests_is_zero(self, monkeypatch):
        async def _noop_narrow(session, tenant_id):
            return None

        monkeypatch.setattr(
            "src.infrastructure.tenancy.rls.narrow_to_tenant", _noop_narrow
        )

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def scalar(self, *a, **k):
                return 0  # no confirmation requests ever sent

        monkeypatch.setattr(
            "src.infrastructure.database.connection.AsyncSessionLocal",
            lambda: _Session(),
        )

        rate = await mod._wa_confirmation_response_rate(
            uuid4(), uuid4(), "+201001234567"
        )
        assert rate == 0.0

    @pytest.mark.asyncio
    async def test_error_is_graceful(self, monkeypatch):
        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(
            "src.infrastructure.database.connection.AsyncSessionLocal", _boom
        )
        rate = await mod._wa_confirmation_response_rate(
            uuid4(), uuid4(), "+201001234567"
        )
        assert rate == 0.0
