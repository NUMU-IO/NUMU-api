"""Wave 2 Phase 15 — tests for the WhatsApp-confirmation Meta CAPI Lead helper.

Pins the gating contract: ``_maybe_enqueue_meta_capi_whatsapp_lead``
only enqueues a Celery task when ALL three of:

  * ``whatsapp_lead_enabled`` is true on the store
  * ``capi_enabled`` is true
  * ``pixel_id`` is set

…are met. The handler is fail-open: an unhandled exception (missing
store, CAPI worker down, etc.) is swallowed so the WhatsApp
verification reply path is never broken.

Direct invocation of the helper (not the full ``apply_reply`` flow)
lets us focus assertions on the Phase 15 contract; integration with
``apply_reply`` is exercised by the existing 42 verification-reply
tests, which now exit through this code path on every confirmed reply.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


def _fake_session_returning_store(store) -> MagicMock:
    """Build a Mock async session whose ``.execute(...)`` resolves to
    a SQLAlchemy-style result wrapping ``store``."""
    session = MagicMock()
    scalar = MagicMock(return_value=store)
    result = MagicMock()
    result.scalar_one_or_none = scalar
    session.execute = AsyncMock(return_value=result)
    return session


def _store(*, meta_cfg: dict | None) -> SimpleNamespace:
    """Build a fake store with the given ``settings.tracking.meta`` cfg."""
    settings = {"tracking": {"meta": meta_cfg or {}}} if meta_cfg is not None else {}
    return SimpleNamespace(id=uuid4(), settings=settings)


class TestWhatsappLeadGating:
    """Helper enqueues only when all three gating flags align."""

    @pytest.mark.asyncio
    async def test_no_store_silently_skips(self):
        from src.application.use_cases.shopify.handle_verification_reply import (
            _maybe_enqueue_meta_capi_whatsapp_lead,
        )

        session = MagicMock()
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        session.execute = AsyncMock(return_value=result)

        with patch(
            "src.infrastructure.messaging.tasks.meta_capi.meta_capi_send_event"
        ) as mock_task:
            await _maybe_enqueue_meta_capi_whatsapp_lead(
                session=session,
                store_id=uuid4(),
                risk_assessment_id=uuid4(),
                phone="01001234567",
            )
        mock_task.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_capi_disabled_skips(self):
        from src.application.use_cases.shopify.handle_verification_reply import (
            _maybe_enqueue_meta_capi_whatsapp_lead,
        )

        store = _store(
            meta_cfg={
                "whatsapp_lead_enabled": True,
                "capi_enabled": False,
                "pixel_id": "111111111111111",
            }
        )
        session = _fake_session_returning_store(store)

        with patch(
            "src.infrastructure.messaging.tasks.meta_capi.meta_capi_send_event"
        ) as mock_task:
            await _maybe_enqueue_meta_capi_whatsapp_lead(
                session=session,
                store_id=store.id,
                risk_assessment_id=uuid4(),
                phone="01001234567",
            )
        mock_task.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_pixel_id_skips(self):
        from src.application.use_cases.shopify.handle_verification_reply import (
            _maybe_enqueue_meta_capi_whatsapp_lead,
        )

        store = _store(
            meta_cfg={
                "whatsapp_lead_enabled": True,
                "capi_enabled": True,
                "pixel_id": None,
            }
        )
        session = _fake_session_returning_store(store)

        with patch(
            "src.infrastructure.messaging.tasks.meta_capi.meta_capi_send_event"
        ) as mock_task:
            await _maybe_enqueue_meta_capi_whatsapp_lead(
                session=session,
                store_id=store.id,
                risk_assessment_id=uuid4(),
                phone="01001234567",
            )
        mock_task.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_whatsapp_lead_disabled_skips(self):
        # CAPI is on and pixel is set, but the merchant hasn't opted
        # into WhatsApp Lead firing — Phase 15 is opt-in to avoid
        # surprising existing stores with extra events.
        from src.application.use_cases.shopify.handle_verification_reply import (
            _maybe_enqueue_meta_capi_whatsapp_lead,
        )

        store = _store(
            meta_cfg={
                "whatsapp_lead_enabled": False,
                "capi_enabled": True,
                "pixel_id": "111111111111111",
            }
        )
        session = _fake_session_returning_store(store)

        with patch(
            "src.infrastructure.messaging.tasks.meta_capi.meta_capi_send_event"
        ) as mock_task:
            await _maybe_enqueue_meta_capi_whatsapp_lead(
                session=session,
                store_id=store.id,
                risk_assessment_id=uuid4(),
                phone="01001234567",
            )
        mock_task.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_flags_aligned_enqueues_with_correct_payload(self):
        from src.application.use_cases.shopify.handle_verification_reply import (
            _maybe_enqueue_meta_capi_whatsapp_lead,
        )

        store = _store(
            meta_cfg={
                "whatsapp_lead_enabled": True,
                "capi_enabled": True,
                "pixel_id": "111111111111111",
                "test_event_code": "TEST99999",
            }
        )
        session = _fake_session_returning_store(store)
        risk_id = uuid4()

        with patch(
            "src.infrastructure.messaging.tasks.meta_capi.meta_capi_send_event"
        ) as mock_task:
            await _maybe_enqueue_meta_capi_whatsapp_lead(
                session=session,
                store_id=store.id,
                risk_assessment_id=risk_id,
                phone="01001234567",
            )

        mock_task.delay.assert_called_once()
        kwargs = mock_task.delay.call_args.kwargs
        assert kwargs["pixel_id"] == "111111111111111"
        assert kwargs["event_name"] == "Lead"
        # event_id namespacing — Lead dedupes per-risk-assessment, not
        # per-order. Keeps Phase 15 Lead distinct from Phase 12 Lead.
        assert kwargs["event_id"] == f"whatsapp-lead-{risk_id}"
        # Phone is the only match key we have on a chat-source event.
        assert kwargs["user_data"]["phone"] == "01001234567"
        assert kwargs["user_data"]["customer_id"] == "01001234567"
        # test_event_code is threaded through so the merchant can
        # verify the fire in Meta Events Manager.
        assert kwargs["test_event_code"] == "TEST99999"
        # action_source must be ``chat`` per Meta's spec for non-website
        # events (lifts EMQ for WhatsApp-origin conversions).
        assert kwargs["action_source"] == "chat"

    @pytest.mark.asyncio
    async def test_handler_is_fail_open_on_exception(self):
        # If the Celery .delay() raises (broker down, etc.), the helper
        # must swallow it — a Meta CAPI failure must NEVER break the
        # customer's verification reply flow.
        from src.application.use_cases.shopify.handle_verification_reply import (
            _maybe_enqueue_meta_capi_whatsapp_lead,
        )

        store = _store(
            meta_cfg={
                "whatsapp_lead_enabled": True,
                "capi_enabled": True,
                "pixel_id": "111111111111111",
            }
        )
        session = _fake_session_returning_store(store)

        with patch(
            "src.infrastructure.messaging.tasks.meta_capi.meta_capi_send_event"
        ) as mock_task:
            mock_task.delay.side_effect = RuntimeError("redis is down")
            # MUST NOT RAISE — the test fails if an exception escapes.
            await _maybe_enqueue_meta_capi_whatsapp_lead(
                session=session,
                store_id=store.id,
                risk_assessment_id=uuid4(),
                phone="01001234567",
            )
