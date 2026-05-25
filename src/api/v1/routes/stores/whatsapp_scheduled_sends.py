"""Merchant-facing scheduled-sends API (FR-013/018, US3).

Four endpoints under ``/stores/{store_id}/whatsapp/scheduled-sends``:
- GET ``/``                 list (filterable by status + related_order_id)
- POST ``/``                create
- GET ``/{send_id}``        get one
- DELETE ``/{send_id}``     cancel a pending row (409 if not pending)
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_store
from src.api.dependencies.database import get_db
from src.api.v1.schemas.stores.whatsapp_scheduled_send import (
    ScheduledSend,
    ScheduledSendCreate,
)
from src.application.use_cases.whatsapp.cancel_scheduled_send import (
    CancelScheduledSendUseCase,
)
from src.application.use_cases.whatsapp.schedule_send import (
    ScheduleSendError,
    ScheduleSendUseCase,
)
from src.core.entities.store import Store
from src.infrastructure.repositories.whatsapp_scheduled_send_repository import (
    WhatsAppScheduledSendRepository,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/{store_id}/whatsapp/scheduled-sends")


@router.get("", response_model=list[ScheduledSend])
async def list_scheduled_sends(
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: str | None = Query(
        None,
        alias="status",
        description="Filter to one of pending / sent / cancelled / skipped / failed.",
    ),
    related_order_id: UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
) -> list[ScheduledSend]:
    repo = WhatsAppScheduledSendRepository(db)
    rows, _total = await repo.list_by_store(
        store.id,
        status=status_filter,
        related_order_id=related_order_id,
        skip=skip,
        limit=limit,
    )
    return [ScheduledSend.model_validate(r) for r in rows]


@router.post("", response_model=ScheduledSend, status_code=status.HTTP_201_CREATED)
async def create_scheduled_send(
    body: ScheduledSendCreate,
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ScheduledSend:
    use_case = ScheduleSendUseCase(db)
    try:
        row = await use_case.execute(
            store_id=store.id,
            phone=body.phone,
            scheduled_for=body.scheduled_for,
            template_id=body.template_id,
            template_params=body.template_params,
            text_message=body.text_message,
            customer_id=body.customer_id,
            related_order_id=body.related_order_id,
        )
    except ScheduleSendError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return ScheduledSend.model_validate(row)


@router.get("/{send_id}", response_model=ScheduledSend)
async def get_scheduled_send(
    send_id: UUID,
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ScheduledSend:
    repo = WhatsAppScheduledSendRepository(db)
    row = await repo.get_by_id(send_id)
    if row is None or row.store_id != store.id:
        # RLS handles cross-store leakage, but in the same-tenant case
        # an explicit 404 is friendlier than an empty result.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "scheduled_send_not_found"},
        )
    return ScheduledSend.model_validate(row)


@router.delete("/{send_id}", status_code=status.HTTP_200_OK)
async def cancel_scheduled_send(
    send_id: UUID,
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Cancel a pending row. 200 on success, 409 if the row is not
    pending (already sent / cancelled / failed / skipped)."""
    repo = WhatsAppScheduledSendRepository(db)
    row = await repo.get_by_id(send_id)
    if row is None or row.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "scheduled_send_not_found"},
        )
    if row.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "not_pending",
                "message": (
                    f"Scheduled send is in status '{row.status}'; only pending "
                    "rows can be cancelled."
                ),
            },
        )

    use_case = CancelScheduledSendUseCase(db)
    moved = await use_case.execute(send_id)
    if not moved:
        # Race with the dispatcher — the row just transitioned out of
        # pending between our get_by_id and the UPDATE. Treat as 409.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "race_with_dispatcher",
                "message": "Scheduled send moved out of pending before cancel could apply.",
            },
        )
    logger.info(
        "whatsapp_scheduled_send_cancelled",
        send_id=str(send_id),
        store_id=str(store.id),
    )
    return Response(status_code=status.HTTP_200_OK)
