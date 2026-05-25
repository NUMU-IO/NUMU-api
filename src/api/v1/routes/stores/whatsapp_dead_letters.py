"""Merchant-facing dead-letter inspection + replay API (FR-034 / US6).

Three endpoints under ``/stores/{store_id}/whatsapp/dead-letters``:
- GET ``/``                  list (filterable by context, replay_state, etc.)
- GET ``/{dl_id}``           detail
- POST ``/{dl_id}/replay``   replay (race-safe; double-send-guarded; admin-only)

Role gating (TASK-SEC-002): listing DLQ rows exposes message content +
PII (template params, customer phones). The endpoints require the
store-owner role; staff/viewer tokens get 403 via the existing
``require_store_owner`` dependency. Rate limiting (TASK-SEC-004) is
deferred to the polish phase pending rate-limit middleware.
"""

import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_store
from src.api.dependencies.auth import require_store_owner
from src.api.dependencies.database import get_db
from src.api.v1.schemas.stores.whatsapp_dead_letter import DeadLetter
from src.application.use_cases.whatsapp.replay_dead_letter import (
    DeadLetterAlreadyReplayed,
    DeadLetterNotFound,
    ReplayDeadLetterUseCase,
)
from src.core.entities.store import Store
from src.infrastructure.repositories.whatsapp_dead_letter_repository import (
    WhatsAppDeadLetterRepository,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/{store_id}/whatsapp/dead-letters")


@router.get(
    "",
    response_model=list[DeadLetter],
    dependencies=[Depends(require_store_owner)],
)
async def list_dead_letters(
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[AsyncSession, Depends(get_db)],
    originating_context: str | None = Query(None),
    replay_state: str | None = Query(None),
    error_classification: str | None = Query(None),
    created_after: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
) -> list[DeadLetter]:
    repo = WhatsAppDeadLetterRepository(db)
    rows, _total = await repo.list_by_store(
        store.id,
        originating_context=originating_context,
        replay_state=replay_state,
        error_classification=error_classification,
        created_after=created_after,
        skip=skip,
        limit=limit,
    )
    return [DeadLetter.model_validate(r) for r in rows]


@router.get(
    "/{dl_id}",
    response_model=DeadLetter,
    dependencies=[Depends(require_store_owner)],
)
async def get_dead_letter(
    dl_id: UUID,
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DeadLetter:
    repo = WhatsAppDeadLetterRepository(db)
    row = await repo.get_by_id(dl_id)
    if row is None or row.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "dead_letter_not_found"},
        )
    return DeadLetter.model_validate(row)


@router.post(
    "/{dl_id}/replay",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_store_owner)],
)
async def replay_dead_letter(
    dl_id: UUID,
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Replay a dead-letter row.

    202 on accept (row transitions to ``replaying`` and the underlying
    Celery task is re-enqueued). 409 if the row is already in
    ``replaying`` / ``replayed_success`` / ``replayed_failed`` — the
    double-send guard refuses to replay a row twice.

    If the original send actually succeeded after the DLQ row was
    written (e.g., a delivery webhook arrived late and updated
    ``message_logs``), the use-case auto-marks the row
    ``replayed_success`` without re-issuing the send.
    """
    use_case = ReplayDeadLetterUseCase(db)
    try:
        result = await use_case.execute(
            dl_id=dl_id,
            store_id=store.id,
            replayed_by=None,  # admin-user-id wiring lands with role guard polish
        )
    except DeadLetterNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "dead_letter_not_found", "message": str(exc)},
        ) from exc
    except DeadLetterAlreadyReplayed as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "already_replayed", "message": str(exc)},
        ) from exc

    logger.info(
        "whatsapp_dead_letter_replay_accepted",
        dl_id=str(dl_id),
        store_id=str(store.id),
        result_status=result.get("status"),
    )
    return result
