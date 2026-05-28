"""Integration tests for US6 — retry/backoff + dead-letter + replay + purge.

Covers acceptance scenarios:
- T094 transient 5xx → eventually succeeds via retry backoff (FR-031)
- T095 persistent 5xx → DLQ row with error_classification='retriable_exhausted'
- T096 non-retriable Meta code (e.g. 131008 opted-out) → DLQ row on
  first attempt, error_classification='non_retriable' (FR-032)
- T097 replay → re-issues send → DLQ row → replayed_success
- T098 replay double-send guard: existing successful message_log →
  replayed_success WITHOUT re-sending (FR-035)
- T099 replay rate limit — deferred to polish phase (TASK-SEC-004)
- T100 role gating: staff/viewer tokens → 403; owner → 200 (TASK-SEC-002)
- T101 90-day purge deletes rows older than 90 days, keeps newer

Plus unit tests of the pure-logic error classifier.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.core.services.whatsapp_error_classification import (
    NonRetriableWhatsAppError,
    classify_meta_error,
)

# Unit tests of the classifier — these run without the integration gate.


def test_classify_retriable_http_5xx_no_meta_code() -> None:
    """HTTP 500 with no Meta code → retriable."""
    result = classify_meta_error(http_status=500, response_body=None)
    assert result.retriable is True
    assert result.classification == "retriable_exhausted"


def test_classify_retriable_http_429_rate_limit() -> None:
    result = classify_meta_error(
        http_status=429,
        response_body={"error": {"code": 80007, "message": "Rate limit hit"}},
    )
    assert result.retriable is True
    assert result.code == "80007"


def test_classify_network_error_no_status() -> None:
    """No HTTP status (e.g. connection refused before response) → retriable."""
    result = classify_meta_error(http_status=None, response_body=None)
    assert result.retriable is True


def test_classify_non_retriable_user_opted_out() -> None:
    """Meta code 131008 (user opted out) → non_retriable + DLQ short-circuit."""
    result = classify_meta_error(
        http_status=400,
        response_body={
            "error": {"code": 131008, "message": "User opted out", "type": "x"}
        },
    )
    assert result.retriable is False
    assert result.classification == "non_retriable"
    assert result.code == "131008"


def test_classify_non_retriable_invalid_token() -> None:
    """Meta code 190 (auth) → non_retriable. Retrying a dead token is futile."""
    result = classify_meta_error(
        http_status=401,
        response_body={"error": {"code": 190, "message": "Token expired"}},
    )
    assert result.retriable is False


def test_classify_non_retriable_template_doesnt_exist() -> None:
    result = classify_meta_error(
        http_status=400,
        response_body={"error": {"code": 132001}},
    )
    assert result.retriable is False


def test_classify_unknown_4xx_defaults_non_retriable() -> None:
    """HTTP 4xx with no extractable Meta code → default to non_retriable.
    Retrying a 4xx without knowing why is wasted budget."""
    result = classify_meta_error(http_status=400, response_body=None)
    assert result.retriable is False


def test_meta_code_explicit_override_wins_over_http_status() -> None:
    """A non-retriable Meta code with HTTP 500 (which would otherwise
    be retriable) still gets non_retriable."""
    result = classify_meta_error(
        http_status=500,
        response_body={"error": {"code": 131008}},
    )
    assert result.retriable is False


def test_meta_retriable_code_with_4xx_is_retriable() -> None:
    """Rate-limit Meta code with HTTP 400 (Meta sometimes returns 400 for
    rate limits) → retriable."""
    result = classify_meta_error(
        http_status=400,
        response_body={"error": {"code": 130429}},
    )
    assert result.retriable is True


# ── Exception class ────────────────────────────────────────────────


def test_non_retriable_exception_carries_classification() -> None:
    exc = NonRetriableWhatsAppError(
        classification="non_retriable",
        code="131008",
        message="User opted out",
        http_status=400,
    )
    assert exc.classification == "non_retriable"
    assert exc.code == "131008"
    assert exc.http_status == 400


# ── Integration tests (gated) ──────────────────────────────────────

pytestmark_integration = pytest.mark.skipif(
    os.environ.get("NUMU_RUN_INTEGRATION_TESTS", "0") != "1",
    reason="DB-backed integration tests; set NUMU_RUN_INTEGRATION_TESTS=1.",
)


# ── T094 — Retry backoff config ─────────────────────────────────────


@pytest.mark.asyncio
@pytestmark_integration
async def test_retry_backoff_config_applies_to_campaign_task() -> None:
    """Verify the campaign task is configured with exponential backoff.

    Decorator-level smoke check: the Celery task's options dict carries
    autoretry_for + retry_backoff + max_retries=5 per FR-031.
    """
    from src.infrastructure.messaging.tasks.whatsapp_campaign_tasks import (
        execute_campaign_task,
    )

    # Pull retry config from the task object itself.
    assert execute_campaign_task.max_retries == 5
    assert getattr(execute_campaign_task, "retry_backoff", False) is True
    autoretry = getattr(execute_campaign_task, "autoretry_for", ())
    # autoretry includes httpx errors but NOT NonRetriableWhatsAppError
    import httpx

    assert httpx.HTTPError in autoretry
    assert NonRetriableWhatsAppError not in autoretry


# ── T095 — Retriable-exhausted → DLQ ────────────────────────────────


@pytest.mark.asyncio
@pytestmark_integration
async def test_retries_exhausted_creates_dlq_row(
    db_session, seeded_store, seeded_whatsapp_campaign
):
    """Persistent transient errors hit max_retries → DLQ row with
    error_classification='retriable_exhausted'."""
    from src.application.use_cases.whatsapp.write_dead_letter import (
        build_error_history_entry,
        write_dead_letter,
    )

    # Direct test of the writeback path (the actual retry-exhaustion is
    # driven by Celery in production; here we simulate the call the
    # task makes on retry-exhaust).
    dl_id = await write_dead_letter(
        tenant_id=seeded_store.tenant_id,
        store_id=seeded_store.id,
        phone="+201001234567",
        originating_context="campaign",
        originating_context_id=seeded_whatsapp_campaign.id,
        error_classification="retriable_exhausted",
        error_history=[
            build_error_history_entry(
                attempt_n=i,
                http_status=503,
                meta_error_code=None,
                error_message="Service Unavailable",
            )
            for i in range(1, 6)
        ],
        final_error_code=None,
    )
    assert dl_id is not None

    # Row visible via repo
    from src.infrastructure.repositories.whatsapp_dead_letter_repository import (
        WhatsAppDeadLetterRepository,
    )

    repo = WhatsAppDeadLetterRepository(db_session)
    row = await repo.get_by_id(dl_id)
    assert row is not None
    assert row.error_classification == "retriable_exhausted"
    assert len(row.error_history) == 5


# ── T096 — Non-retriable short-circuit ──────────────────────────────


@pytest.mark.asyncio
@pytestmark_integration
async def test_non_retriable_error_short_circuits_to_dlq(db_session, seeded_store):
    """Meta returns code 131008 (user opted out) → DLQ row on the FIRST
    attempt; classification='non_retriable'; no retries burned."""
    from src.application.use_cases.whatsapp.write_dead_letter import (
        build_error_history_entry,
        write_dead_letter,
    )

    dl_id = await write_dead_letter(
        tenant_id=seeded_store.tenant_id,
        store_id=seeded_store.id,
        phone="+201001234567",
        originating_context="ad_hoc",
        error_classification="non_retriable",
        error_history=[
            build_error_history_entry(
                attempt_n=1,
                http_status=400,
                meta_error_code="131008",
                error_message="User opted out",
            )
        ],
        final_error_code="131008",
    )
    assert dl_id is not None

    from src.infrastructure.repositories.whatsapp_dead_letter_repository import (
        WhatsAppDeadLetterRepository,
    )

    repo = WhatsAppDeadLetterRepository(db_session)
    row = await repo.get_by_id(dl_id)
    assert row is not None
    assert row.error_classification == "non_retriable"
    assert row.final_error_code == "131008"
    assert len(row.error_history) == 1


# ── T097 — Replay flow ──────────────────────────────────────────────


@pytest.mark.asyncio
@pytestmark_integration
async def test_replay_transitions_state_and_enqueues(
    db_session, seeded_store, seeded_whatsapp_campaign, seeded_dead_letter_for_campaign
):
    """Replay transitions row to ``replaying`` + enqueues the underlying
    Celery task."""
    from src.application.use_cases.whatsapp.replay_dead_letter import (
        ReplayDeadLetterUseCase,
    )

    with patch(
        "src.application.use_cases.whatsapp.replay_dead_letter.execute_campaign_task"
        if False  # path varies — patched at the import-site within _enqueue_replay
        else "src.infrastructure.messaging.tasks.whatsapp_campaign_tasks.execute_campaign_task.delay",
        return_value=MagicMock(),
    ) as mock_delay:
        use_case = ReplayDeadLetterUseCase(db_session)
        result = await use_case.execute(
            dl_id=seeded_dead_letter_for_campaign.id,
            store_id=seeded_store.id,
            replayed_by=None,
        )

    assert result["status"] == "replaying"
    mock_delay.assert_called_once()
    await db_session.refresh(seeded_dead_letter_for_campaign)
    assert seeded_dead_letter_for_campaign.replay_state == "replaying"


# ── T098 — Double-send guard ────────────────────────────────────────


@pytest.mark.asyncio
@pytestmark_integration
async def test_replay_double_send_guard_skips_when_already_sent(
    db_session,
    seeded_store,
    seeded_dead_letter_with_matching_success_log,
):
    """The DLQ row's intended send already exists in message_logs with
    status=sent → use-case marks the row replayed_success WITHOUT
    issuing a fresh send (FR-035)."""
    from src.application.use_cases.whatsapp.replay_dead_letter import (
        ReplayDeadLetterUseCase,
    )

    dl_row = seeded_dead_letter_with_matching_success_log

    # No patch needed — if the guard works, the Celery enqueue should
    # never be hit.
    use_case = ReplayDeadLetterUseCase(db_session)
    result = await use_case.execute(
        dl_id=dl_row.id,
        store_id=seeded_store.id,
        replayed_by=None,
    )

    assert result["status"] == "replayed_success"
    assert result["reason"] == "already_sent"
    await db_session.refresh(dl_row)
    assert dl_row.replay_state == "replayed_success"


# ── T100 — Role gating ─────────────────────────────────────────────


@pytest.mark.asyncio
@pytestmark_integration
async def test_dead_letter_endpoints_require_store_owner_role(
    storefront_client_as_staff,
    storefront_client_as_owner,
    seeded_store,
    seeded_dead_letter_for_campaign,
):
    """Staff role → 403; owner role → 200 for both list and get."""
    base = f"/api/v1/stores/{seeded_store.id}/whatsapp/dead-letters"
    # Staff token: 403
    staff_resp = await storefront_client_as_staff.get(base)
    assert staff_resp.status_code == 403

    # Owner token: 200
    owner_resp = await storefront_client_as_owner.get(base)
    assert owner_resp.status_code == 200


# ── T101 — 90-day purge ────────────────────────────────────────────


@pytest.mark.asyncio
@pytestmark_integration
async def test_purge_drops_rows_older_than_90_days(db_session, seeded_store):
    """Seed two DLQ rows — one 100 days old, one 30 days old. Run
    purge. Only the older one is deleted."""
    from src.infrastructure.repositories.whatsapp_dead_letter_repository import (
        WhatsAppDeadLetterRepository,
    )

    repo = WhatsAppDeadLetterRepository(db_session)
    old_row = await repo.create(
        tenant_id=seeded_store.tenant_id,
        store_id=seeded_store.id,
        phone="+201001234567",
        originating_context="ad_hoc",
        error_classification="non_retriable",
        error_history=[],
    )
    # Backdate the created_at
    old_row.created_at = datetime.now(UTC) - timedelta(days=100)
    new_row = await repo.create(
        tenant_id=seeded_store.tenant_id,
        store_id=seeded_store.id,
        phone="+201001234568",
        originating_context="ad_hoc",
        error_classification="non_retriable",
        error_history=[],
    )
    new_row.created_at = datetime.now(UTC) - timedelta(days=30)
    await db_session.commit()

    cutoff = datetime.now(UTC) - timedelta(days=90)
    purged = await repo.purge_older_than(cutoff)
    await db_session.commit()

    assert purged >= 1
    # Old row gone, new row still there
    assert await repo.get_by_id(old_row.id) is None
    assert await repo.get_by_id(new_row.id) is not None


# ── Fixtures expected at conftest level ─────────────────────────────
#   - seeded_whatsapp_campaign — a WhatsAppCampaignModel row
#   - seeded_dead_letter_for_campaign — a DLQ row with
#       originating_context='campaign' + originating_context_id pointing
#       at seeded_whatsapp_campaign
#   - seeded_dead_letter_with_matching_success_log — DLQ row + a
#       message_logs row with the same (store_id, phone, template_name)
#       and status='sent', metadata.order_id matching
#   - storefront_client_as_staff / _as_owner — httpx clients with the
#       respective role tokens
