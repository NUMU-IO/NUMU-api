"""Integration tests for US5 — template submission to Meta + status updates.

Covers acceptance scenarios:
- T079 BYO mode + valid payload → POSTs to Meta + persists local PENDING
- T080 platform-managed mode → 403 ``template_submission_requires_byo``
  (no local row, no Meta call)
- T081 Meta rejects (4xx) → 422 with sanitized error + no local row
  (FR-027)
- T082 webhook ``message_template_status_update`` → local row updates
  within window
- T083 same webhook payload twice → idempotent (no duplicate writes)
  (TASK-SEC-008)
- T084 polling sync queries Meta + updates PENDING rows
- T085 send guard refuses non-APPROVED template — covered by the
  existing send-guard unit tests (test_send_guard.py
  ``test_non_approved_template_rejected``)
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.application.use_cases.whatsapp.submit_template import (
    SubmitTemplateUseCase,
    TemplateDuplicateLocal,
    TemplateSubmissionForbidden,
    TemplateSubmissionRejected,
)
from src.infrastructure.external_services.meta.whatsapp_template_status_webhook import (
    handle_template_status_update,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("NUMU_RUN_INTEGRATION_TESTS", "0") != "1",
    reason="DB-backed integration tests; set NUMU_RUN_INTEGRATION_TESTS=1.",
)


# ── T079 — BYO happy path ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_template_byo_happy_path(
    db_session, seeded_store_with_byo_credentials
):
    """BYO mode + valid payload → POSTed to Meta + persisted PENDING with
    meta_template_id set."""
    store = seeded_store_with_byo_credentials

    fake_client = MagicMock()
    fake_client.submit_template = AsyncMock(
        return_value={"id": "meta_tpl_123", "status": "PENDING"}
    )
    fake_client.close = AsyncMock()

    with patch(
        "src.application.use_cases.whatsapp.submit_template.WhatsAppClient",
        return_value=fake_client,
    ):
        use_case = SubmitTemplateUseCase(db_session)
        row = await use_case.execute(
            store_id=store.id,
            tenant_id=store.tenant_id,
            name="shipping_eta_byo",
            language="en",
            category="UTILITY",
            body_text="Order {{1}} estimated delivery: {{2}}",
        )
        await db_session.commit()

    assert row.status == "PENDING"
    assert row.meta_template_id == "meta_tpl_123"
    assert row.is_system is False
    fake_client.submit_template.assert_awaited_once()
    fake_client.close.assert_awaited()


# ── T080 — Platform-managed mode → 403 (EDIT-C / FR-026) ────────────


@pytest.mark.asyncio
async def test_submit_template_platform_managed_forbidden(
    db_session, seeded_store_platform_managed
):
    """Platform-managed store cannot submit custom templates."""
    store = seeded_store_platform_managed
    use_case = SubmitTemplateUseCase(db_session)
    with pytest.raises(TemplateSubmissionForbidden) as exc:
        await use_case.execute(
            store_id=store.id,
            tenant_id=store.tenant_id,
            name="custom_promo_pm",
            language="ar",
            category="MARKETING",
            body_text="Promo {{1}}",
        )
    assert exc.value.code == "template_submission_requires_byo"


# ── T081 — Meta 4xx → 422 + no local row (FR-027) ───────────────────


@pytest.mark.asyncio
async def test_submit_template_meta_rejects_no_local_row(
    db_session, seeded_store_with_byo_credentials
):
    """Meta returns 400 (e.g., name collision at Meta-side) → use-case
    raises TemplateSubmissionRejected with sanitized error; NO local row."""
    store = seeded_store_with_byo_credentials

    bad_response = MagicMock()
    bad_response.status_code = 400
    bad_response.json.return_value = {
        "error": {
            "code": 100,
            "message": "Template name already exists",
            "type": "OAuthException",
            "fbtrace_id": "TRACE_DO_NOT_LEAK",
        }
    }
    fake_client = MagicMock()
    fake_client.submit_template = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "400", request=MagicMock(), response=bad_response
        )
    )
    fake_client.close = AsyncMock()

    with patch(
        "src.application.use_cases.whatsapp.submit_template.WhatsAppClient",
        return_value=fake_client,
    ):
        use_case = SubmitTemplateUseCase(db_session)
        with pytest.raises(TemplateSubmissionRejected) as exc:
            await use_case.execute(
                store_id=store.id,
                tenant_id=store.tenant_id,
                name="collision_name",
                language="en",
                category="UTILITY",
                body_text="x",
            )

    # Sanitized — fbtrace_id stripped (TASK-SEC-009)
    assert exc.value.meta_error is not None
    assert "fbtrace_id" not in exc.value.meta_error
    assert exc.value.meta_error.get("code") == 100

    # No local row written (FR-027)
    from sqlalchemy import select

    from src.infrastructure.database.models.tenant.whatsapp_template import (
        WhatsAppTemplateModel,
    )

    rows = (
        (
            await db_session.execute(
                select(WhatsAppTemplateModel).where(
                    WhatsAppTemplateModel.store_id == store.id,
                    WhatsAppTemplateModel.name == "collision_name",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 0


# ── Local duplicate check fires before Meta call ────────────────────


@pytest.mark.asyncio
async def test_submit_template_local_duplicate_409(
    db_session, seeded_store_with_byo_credentials, seeded_approved_template
):
    """A pre-existing local row with the same (name, language) makes the
    use-case raise TemplateDuplicateLocal BEFORE any Meta call."""
    store = seeded_store_with_byo_credentials
    fake_client = MagicMock()
    fake_client.submit_template = AsyncMock()
    fake_client.close = AsyncMock()
    with patch(
        "src.application.use_cases.whatsapp.submit_template.WhatsAppClient",
        return_value=fake_client,
    ):
        use_case = SubmitTemplateUseCase(db_session)
        with pytest.raises(TemplateDuplicateLocal):
            await use_case.execute(
                store_id=store.id,
                tenant_id=store.tenant_id,
                name=seeded_approved_template.name,
                language=seeded_approved_template.language,
                category="UTILITY",
                body_text="x",
            )
    # Meta NOT called
    fake_client.submit_template.assert_not_awaited()


# ── T082 + T083 — template-status webhook ───────────────────────────


@pytest.mark.asyncio
async def test_template_status_webhook_updates_local_row(
    db_session, seeded_store_with_byo_credentials, seeded_pending_template
):
    """Meta webhook with event=APPROVED → local row.status becomes
    'APPROVED', approved_at populated, rejection_reason untouched."""
    waba_id = seeded_store_with_byo_credentials.settings["whatsapp"]["waba_id"]
    seeded_pending_template.meta_template_id = "meta_t_001"
    await db_session.commit()

    updated = await handle_template_status_update(
        db_session,
        waba_id=waba_id,
        value={
            "event": "APPROVED",
            "message_template_id": "meta_t_001",
            "message_template_name": seeded_pending_template.name,
            "message_template_language": seeded_pending_template.language,
        },
    )
    await db_session.commit()
    assert updated is True
    await db_session.refresh(seeded_pending_template)
    assert seeded_pending_template.status == "APPROVED"
    assert seeded_pending_template.approved_at is not None


@pytest.mark.asyncio
async def test_template_status_webhook_idempotent(
    db_session, seeded_store_with_byo_credentials, seeded_pending_template
):
    """TASK-SEC-008 — applying the same status twice writes once.
    Meta retries webhook deliveries on non-2xx, so duplicate payloads
    must NOT compound their effect.
    """
    waba_id = seeded_store_with_byo_credentials.settings["whatsapp"]["waba_id"]
    seeded_pending_template.meta_template_id = "meta_t_002"
    await db_session.commit()

    payload = {
        "event": "APPROVED",
        "message_template_id": "meta_t_002",
        "message_template_name": seeded_pending_template.name,
        "message_template_language": seeded_pending_template.language,
    }
    first = await handle_template_status_update(
        db_session, waba_id=waba_id, value=payload
    )
    await db_session.commit()
    await db_session.refresh(seeded_pending_template)
    first_approved_at = seeded_pending_template.approved_at

    # Second delivery of the same payload
    second = await handle_template_status_update(
        db_session, waba_id=waba_id, value=payload
    )
    await db_session.commit()
    await db_session.refresh(seeded_pending_template)

    assert first is True
    assert second is False  # no-op on identical state
    assert seeded_pending_template.approved_at == first_approved_at


@pytest.mark.asyncio
async def test_template_status_webhook_rejected_carries_reason(
    db_session, seeded_store_with_byo_credentials, seeded_pending_template
):
    waba_id = seeded_store_with_byo_credentials.settings["whatsapp"]["waba_id"]
    seeded_pending_template.meta_template_id = "meta_t_003"
    await db_session.commit()

    updated = await handle_template_status_update(
        db_session,
        waba_id=waba_id,
        value={
            "event": "REJECTED",
            "message_template_id": "meta_t_003",
            "message_template_name": seeded_pending_template.name,
            "message_template_language": seeded_pending_template.language,
            "reason": "ABUSIVE_CONTENT",
        },
    )
    await db_session.commit()
    assert updated is True
    await db_session.refresh(seeded_pending_template)
    assert seeded_pending_template.status == "REJECTED"
    assert seeded_pending_template.rejection_reason == "ABUSIVE_CONTENT"


# ── T084 — polling sync ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_polling_sync_updates_pending_templates(
    db_session, seeded_store_with_byo_credentials, seeded_pending_template
):
    """The polling task calls Meta's list_templates and applies status
    transitions to local PENDING rows that match by meta_template_id."""
    from src.infrastructure.messaging.tasks.whatsapp_template_poll_task import (
        _poll_for_tenant,
    )

    store = seeded_store_with_byo_credentials
    seeded_pending_template.meta_template_id = "meta_t_004"
    # Age the submitted_at so it's older than the 5-min threshold
    from datetime import UTC, datetime, timedelta

    seeded_pending_template.submitted_at = datetime.now(UTC) - timedelta(minutes=10)
    await db_session.commit()

    fake_client = MagicMock()
    fake_client.list_templates = AsyncMock(
        return_value={
            "data": [
                {
                    "id": "meta_t_004",
                    "name": seeded_pending_template.name,
                    "language": seeded_pending_template.language,
                    "status": "APPROVED",
                }
            ]
        }
    )
    fake_client.close = AsyncMock()

    with patch(
        "src.infrastructure.messaging.tasks.whatsapp_template_poll_task.WhatsAppClient",
        return_value=fake_client,
    ):
        stats = await _poll_for_tenant(store.tenant_id)

    assert stats["updated"] >= 1
    await db_session.refresh(seeded_pending_template)
    assert seeded_pending_template.status == "APPROVED"


# ── T085 — send guard refuses non-APPROVED template (cross-ref) ─────


def test_t085_documented_in_unit_send_guard_tests() -> None:
    """T085 ('sends refuse non-APPROVED template') is already covered
    by tests/unit/core/whatsapp/test_send_guard.py
    ``test_non_approved_template_rejected``, which parametrizes over
    {PENDING, REJECTED, FLAGGED, PAUSED, DISABLED, None} and asserts
    reason=template_not_approved. This stub exists so the test file
    line-count traces back to the spec's T085 task id."""


# ── Fixtures expected at conftest level ─────────────────────────────
#   - db_session
#   - seeded_store_platform_managed (no active ServiceCredential)
#   - seeded_store_with_byo_credentials (has active WHATSAPP_BUSINESS
#     ServiceCredential row + store.settings.whatsapp.waba_id set)
#   - seeded_pending_template (whatsapp_templates row, status='PENDING',
#     submitted_at populated)
#   - seeded_approved_template (status='APPROVED')
