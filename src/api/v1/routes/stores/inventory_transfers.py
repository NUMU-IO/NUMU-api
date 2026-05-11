"""Merchant inventory transfers — Phase 8.2.

Mounted at /stores/{store_id}/inventory/transfers/

Endpoints:
  GET    /            — list transfers (optional ?status= filter)
  POST   /            — create a DRAFT transfer
  GET    /{id}        — single
  PUT    /{id}/lines  — replace lines (DRAFT only)
  POST   /{id}/transitions  — advance state machine

State machine transitions:
  DRAFT → REQUESTED / CANCELED
  REQUESTED → IN_TRANSIT / CANCELED
  IN_TRANSIT → RECEIVED / CANCELED
  RECEIVED + CANCELED are terminal

Stock only moves on the RECEIVED transition (handled by
InventoryService).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.repositories import get_store_repository
from src.api.responses import SuccessResponse
from src.application.services.inventory_service import InventoryService
from src.core.entities.inventory_transfer import (
    InventoryTransfer,
    InventoryTransferLine,
    TransferStatus,
)
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.repositories import StoreRepository

router = APIRouter(
    prefix="/{store_id}/inventory/transfers",
    tags=["Inventory Transfers"],
    dependencies=[Depends(verify_store_ownership)],
)


class TransferLineInput(BaseModel):
    variant_id: UUID
    quantity: int = Field(ge=1)


class CreateTransferRequest(BaseModel):
    from_location_id: UUID
    to_location_id: UUID
    note: str | None = None
    carrier_reference: str | None = None
    lines: list[TransferLineInput] = Field(default_factory=list)


class UpdateLinesRequest(BaseModel):
    lines: list[TransferLineInput] = Field(default_factory=list)


class TransitionRequest(BaseModel):
    target: TransferStatus


class TransferLineResponse(BaseModel):
    variant_id: str
    quantity: int


class TransferResponse(BaseModel):
    id: str
    from_location_id: str
    to_location_id: str
    status: str
    note: str | None = None
    carrier_reference: str | None = None
    lines: list[TransferLineResponse]
    requested_at: str | None = None
    shipped_at: str | None = None
    received_at: str | None = None
    canceled_at: str | None = None
    created_at: str
    updated_at: str


def _to_response(t: InventoryTransfer) -> TransferResponse:
    return TransferResponse(
        id=str(t.id),
        from_location_id=str(t.from_location_id),
        to_location_id=str(t.to_location_id),
        status=t.status.value,
        note=t.note,
        carrier_reference=t.carrier_reference,
        lines=[
            TransferLineResponse(
                variant_id=str(line.variant_id), quantity=line.quantity
            )
            for line in (t.lines or [])
        ],
        requested_at=t.requested_at.isoformat() if t.requested_at else None,
        shipped_at=t.shipped_at.isoformat() if t.shipped_at else None,
        received_at=t.received_at.isoformat() if t.received_at else None,
        canceled_at=t.canceled_at.isoformat() if t.canceled_at else None,
        created_at=t.created_at.isoformat() if t.created_at else "",
        updated_at=t.updated_at.isoformat() if t.updated_at else "",
    )


@router.get(
    "",
    response_model=SuccessResponse[list[TransferResponse]],
    summary="List inventory transfers",
    operation_id="list_inventory_transfers",
)
async def list_transfers(
    store_id: UUID,
    status_filter: TransferStatus | None = Query(None, alias="status"),
):
    async with AsyncSessionLocal() as session:
        svc = InventoryService(session)
        rows = await svc._transfers.list_for_store(store_id, status=status_filter)
    return SuccessResponse(
        data=[_to_response(t) for t in rows],
        message="Transfers listed",
    )


@router.post(
    "",
    response_model=SuccessResponse[TransferResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a draft transfer",
    operation_id="create_inventory_transfer",
)
async def create_transfer(
    store_id: UUID,
    body: CreateTransferRequest,
    store_repo: StoreRepository = Depends(get_store_repository),
):
    if body.from_location_id == body.to_location_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="from_location and to_location must differ.",
        )
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Store not found"
        )
    async with AsyncSessionLocal() as session:
        svc = InventoryService(session)
        t = InventoryTransfer(
            tenant_id=store.tenant_id,
            store_id=store_id,
            from_location_id=body.from_location_id,
            to_location_id=body.to_location_id,
            status=TransferStatus.DRAFT,
            note=body.note,
            carrier_reference=body.carrier_reference,
            lines=[
                InventoryTransferLine(
                    variant_id=line.variant_id, quantity=line.quantity
                )
                for line in body.lines
            ],
        )
        created = await svc._transfers.create(t)
        await session.commit()
    return SuccessResponse(data=_to_response(created), message="Transfer created")


@router.get(
    "/{transfer_id}",
    response_model=SuccessResponse[TransferResponse],
    summary="Get transfer",
    operation_id="get_inventory_transfer",
)
async def get_transfer(store_id: UUID, transfer_id: UUID):
    async with AsyncSessionLocal() as session:
        svc = InventoryService(session)
        t = await svc._transfers.get_by_id(transfer_id)
    if t is None or t.store_id != store_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Transfer not found"
        )
    return SuccessResponse(data=_to_response(t), message="Transfer retrieved")


@router.put(
    "/{transfer_id}/lines",
    response_model=SuccessResponse[TransferResponse],
    summary="Replace lines (DRAFT only)",
    operation_id="update_transfer_lines",
)
async def update_lines(store_id: UUID, transfer_id: UUID, body: UpdateLinesRequest):
    async with AsyncSessionLocal() as session:
        svc = InventoryService(session)
        t = await svc._transfers.get_by_id(transfer_id)
        if t is None or t.store_id != store_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Transfer not found"
            )
        if t.status != TransferStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot edit lines on a {t.status.value} transfer.",
            )
        updated = await svc._transfers.update_lines(
            transfer_id,
            [
                InventoryTransferLine(
                    variant_id=line.variant_id, quantity=line.quantity
                )
                for line in body.lines
            ],
        )
        await session.commit()
    return SuccessResponse(data=_to_response(updated), message="Lines updated")


@router.post(
    "/{transfer_id}/transitions",
    response_model=SuccessResponse[TransferResponse],
    summary="Move transfer through its state machine",
    operation_id="transition_inventory_transfer",
)
async def transition_transfer(
    store_id: UUID, transfer_id: UUID, body: TransitionRequest
):
    async with AsyncSessionLocal() as session:
        svc = InventoryService(session)
        existing = await svc._transfers.get_by_id(transfer_id)
        if existing is None or existing.store_id != store_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Transfer not found"
            )
        try:
            updated = await svc.transition_transfer(transfer_id, body.target)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
            ) from e
        await session.commit()
    return SuccessResponse(
        data=_to_response(updated),
        message=f"Transferred to {body.target.value}",
    )
