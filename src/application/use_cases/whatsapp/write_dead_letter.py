"""Write a dead-letter record from an exhausted-retry or non-retriable
Celery task path (FR-033 / US6).

Single entry point used by every WhatsApp Celery task. Each task imports
``write_dead_letter`` and calls it once when:

- ``autoretry_for`` raises after ``max_retries`` is exhausted (the
  retriable-exhausted path), OR
- The task code catches ``NonRetriableWhatsAppError`` (the non-retriable
  short-circuit path) and short-circuits straight to DLQ.

The helper opens its own ``AsyncSessionLocal`` under
``RLSContext(tenant_id)`` so the DLQ row lands under the correct tenant
even though the Celery worker doesn't carry HTTP-request RLS context.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from src.config.logging_config import get_logger

logger = get_logger(__name__)


# Canonical originating-context values (matches the CHECK constraint on
# whatsapp_dead_letters.originating_context).
_VALID_CONTEXTS: frozenset[str] = frozenset({
    "order_created",
    "order_paid",
    "order_status_changed",
    "campaign",
    "scheduled_send",
    "abandoned_cart",
    "ad_hoc",
})


async def write_dead_letter(
    *,
    tenant_id: UUID,
    store_id: UUID,
    phone: str,
    originating_context: str,
    error_classification: str,
    error_history: list[dict[str, Any]],
    customer_id: UUID | None = None,
    template_id: UUID | None = None,
    template_params: dict[str, Any] | None = None,
    text_message: str | None = None,
    originating_context_id: UUID | None = None,
    final_error_code: str | None = None,
) -> UUID | None:
    """Persist a dead-letter row. Returns the new row's UUID, or None
    if persistence failed (logged; never raises — DLQ writeback failures
    must not crash the parent task's already-failing flow).
    """
    if originating_context not in _VALID_CONTEXTS:
        logger.warning(
            "dead_letter_invalid_context",
            originating_context=originating_context,
        )
        # Fall back to ad_hoc so the row still lands.
        originating_context = "ad_hoc"

    if error_classification not in ("retriable_exhausted", "non_retriable"):
        logger.warning(
            "dead_letter_invalid_classification",
            classification=error_classification,
        )
        error_classification = "retriable_exhausted"

    try:
        from src.infrastructure.database.connection import AsyncSessionLocal
        from src.infrastructure.repositories.whatsapp_dead_letter_repository import (
            WhatsAppDeadLetterRepository,
        )
        from src.infrastructure.tenancy.rls import RLSContext

        async with AsyncSessionLocal() as session:
            async with RLSContext(session, tenant_id):
                repo = WhatsAppDeadLetterRepository(session)
                row = await repo.create(
                    tenant_id=tenant_id,
                    store_id=store_id,
                    phone=phone,
                    originating_context=originating_context,
                    error_classification=error_classification,
                    error_history=error_history,
                    customer_id=customer_id,
                    template_id=template_id,
                    template_params=template_params,
                    text_message=text_message,
                    originating_context_id=originating_context_id,
                    final_error_code=final_error_code,
                )
                await session.commit()
                logger.info(
                    "whatsapp_dead_letter_written",
                    dl_id=str(row.id),
                    store_id=str(store_id),
                    originating_context=originating_context,
                    classification=error_classification,
                    code=final_error_code,
                )
                return row.id
    except Exception as exc:
        logger.error(
            "whatsapp_dead_letter_write_failed",
            error=str(exc),
            store_id=str(store_id),
            originating_context=originating_context,
            exc_info=True,
        )
        return None


def build_error_history_entry(
    *,
    attempt_n: int,
    http_status: int | None,
    meta_error_code: str | None,
    error_message: str,
) -> dict[str, Any]:
    """Build one row for the ``error_history`` JSONB array."""
    return {
        "attempt_n": attempt_n,
        "at": datetime.now(UTC).isoformat(),
        "http_status": http_status,
        "meta_error_code": meta_error_code,
        "error_message": error_message[:1000],  # cap to avoid runaway sizes
    }
