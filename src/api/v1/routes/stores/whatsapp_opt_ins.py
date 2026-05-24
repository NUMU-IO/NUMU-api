"""Merchant-facing WhatsApp opt-in API (FR-006, FR-007, FR-009 reading).

Three endpoints:
- ``GET  /stores/{store_id}/whatsapp/opt-ins``  list (history-preserving)
- ``POST /stores/{store_id}/whatsapp/opt-ins``  manual create (import / API)
- ``POST /stores/{store_id}/whatsapp/opt-ins/revoke``  merchant-side opt-out

The storefront-facing anonymous endpoint lives elsewhere
(:mod:`src.api.v1.routes.storefront.whatsapp_optin`) and uses a
checkout-session token rather than bearer auth (FR-007a).
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_store
from src.api.dependencies.database import get_db
from src.api.v1.schemas.stores.whatsapp_opt_in import (
    OptInCreate,
    OptInRevoke,
    OptInRow,
)
from src.application.use_cases.whatsapp.opt_in_customer import OptInCustomerUseCase
from src.application.use_cases.whatsapp.opt_out_customer import OptOutCustomerUseCase
from src.core.entities.store import Store
from src.core.value_objects.phone import InvalidPhoneError
from src.infrastructure.repositories.whatsapp_opt_in_repository import (
    WhatsAppOptInRepository,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/{store_id}/whatsapp/opt-ins")


@router.get("", response_model=list[OptInRow])
async def list_opt_ins(
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[AsyncSession, Depends(get_db)],
    phone: str | None = Query(None, description="Filter by E.164 phone."),
    active_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
) -> list[OptInRow]:
    repo = WhatsAppOptInRepository(db)
    rows, _total = await repo.list_by_store(
        store.id,
        phone=phone,
        active_only=active_only,
        skip=skip,
        limit=limit,
    )
    return [OptInRow.model_validate(r) for r in rows]


@router.post("", response_model=OptInRow, status_code=status.HTTP_201_CREATED)
async def create_opt_in(
    body: OptInCreate,
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OptInRow:
    """Merchant-side opt-in creation. Used by import flows / API integrations.

    Phone is canonicalized inside the use-case; 422 on parse failure.
    Idempotent: if an active opt-in already exists for (store, phone) the
    existing row is returned with 201 (no new row written).
    """
    use_case = OptInCustomerUseCase(db)
    try:
        row = await use_case.execute(
            store_id=store.id,
            phone=body.phone,
            source=body.source,
            customer_id=body.customer_id,
        )
    except InvalidPhoneError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_phone", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_request", "message": str(exc)},
        ) from exc
    return OptInRow.model_validate(row)


@router.post("/revoke", response_model=OptInRow)
async def revoke_opt_in(
    body: OptInRevoke,
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OptInRow:
    """Merchant-initiated opt-out for a customer's WhatsApp messaging.

    Returns 404 when no active opt-in row exists for (store, phone).
    """
    use_case = OptOutCustomerUseCase(db)
    try:
        row = await use_case.execute(
            store_id=store.id,
            phone=body.phone,
            reason=body.reason,
        )
    except InvalidPhoneError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_phone", "message": str(exc)},
        ) from exc

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "no_active_opt_in",
                "message": "No active opt-in row exists for this phone.",
            },
        )

    logger.info(
        "whatsapp_opt_in_revoked",
        store_id=str(store.id),
        phone_tail=body.phone[-4:],
        reason=body.reason,
    )
    return OptInRow.model_validate(row)
