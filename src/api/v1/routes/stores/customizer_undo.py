"""Customizer undo entry routes — Phase 6.

Server-side persistence for the V3 customizer undo stack. The hub
posts each undo-able action here as it happens so a tab close +
re-open preserves history. The cap is 50 per (user, store, theme);
older rows are pruned in the same transaction.

Mounted at /stores/{store_id}/themes/v3/undo/
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from src.api.dependencies import get_current_user_id, verify_store_ownership
from src.api.dependencies.repositories import get_store_repository
from src.api.responses import SuccessResponse
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.public.customizer_undo_entry import (
    CustomizerUndoEntryModel,
)
from src.infrastructure.repositories import StoreRepository

router = APIRouter(
    prefix="/{store_id}/themes/v3/undo",
    tags=["Theme Editor V3 — Undo"],
    dependencies=[Depends(verify_store_ownership)],
)

# Caps the per-user-per-theme stack at the same size as the
# client-side FIFO so behavior is consistent before/after the network
# round-trip.
MAX_UNDO_ENTRIES = 50


class UndoEntryRequest(BaseModel):
    theme_id: str = Field(min_length=1, max_length=64)
    action_label: str = Field(min_length=1, max_length=128)
    forward: dict[str, Any] = Field(default_factory=dict)
    inverse: dict[str, Any] = Field(default_factory=dict)


class UndoEntryResponse(BaseModel):
    id: str
    theme_id: str
    action_label: str
    forward: dict[str, Any]
    inverse: dict[str, Any]
    created_at: str


@router.get(
    "",
    response_model=SuccessResponse[list[UndoEntryResponse]],
    summary="List undo entries",
    operation_id="list_undo_entries",
)
async def list_entries(
    store_id: UUID,
    theme_id: str,
    user_id: UUID = Depends(get_current_user_id),
):
    """Newest-first; capped at MAX_UNDO_ENTRIES.

    The customizer rehydrates its undo stack from this list on mount
    so closing the tab and re-opening preserves history.
    """

    async with AsyncSessionLocal() as session:
        stmt = (
            select(CustomizerUndoEntryModel)
            .where(
                CustomizerUndoEntryModel.user_id == user_id,
                CustomizerUndoEntryModel.store_id == store_id,
                CustomizerUndoEntryModel.theme_id == theme_id,
            )
            .order_by(CustomizerUndoEntryModel.created_at.desc())
            .limit(MAX_UNDO_ENTRIES)
        )
        rows = (await session.execute(stmt)).scalars().all()
    return SuccessResponse(
        data=[
            UndoEntryResponse(
                id=str(r.id),
                theme_id=r.theme_id,
                action_label=r.action_label,
                forward=r.forward,
                inverse=r.inverse,
                created_at=r.created_at.isoformat(),
            )
            for r in rows
        ],
        message="Undo entries listed",
    )


@router.post(
    "",
    response_model=SuccessResponse[UndoEntryResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Append an undo entry",
    operation_id="append_undo_entry",
)
async def append_entry(
    store_id: UUID,
    body: UndoEntryRequest,
    user_id: UUID = Depends(get_current_user_id),
    store_repo: StoreRepository = Depends(get_store_repository),
):
    """Append + prune in a single transaction so the stack never
    grows past MAX_UNDO_ENTRIES.

    Pruning is by created_at desc — drop everything past row 50.
    """

    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Store not found"
        )
    tenant_id = store.tenant_id

    async with AsyncSessionLocal() as session:
        entry = CustomizerUndoEntryModel(
            tenant_id=tenant_id,
            store_id=store_id,
            user_id=user_id,
            theme_id=body.theme_id,
            action_label=body.action_label,
            forward=body.forward,
            inverse=body.inverse,
        )
        session.add(entry)
        await session.flush()

        # Identify rows past the cap, oldest first, and delete them.
        # We do this in a sub-select rather than a window-function
        # delete for compatibility with older PG versions.
        keep_ids_stmt = (
            select(CustomizerUndoEntryModel.id)
            .where(
                CustomizerUndoEntryModel.user_id == user_id,
                CustomizerUndoEntryModel.store_id == store_id,
                CustomizerUndoEntryModel.theme_id == body.theme_id,
            )
            .order_by(CustomizerUndoEntryModel.created_at.desc())
            .limit(MAX_UNDO_ENTRIES)
        )
        keep_ids = [r[0] for r in (await session.execute(keep_ids_stmt)).all()]
        if keep_ids:
            await session.execute(
                delete(CustomizerUndoEntryModel).where(
                    CustomizerUndoEntryModel.user_id == user_id,
                    CustomizerUndoEntryModel.store_id == store_id,
                    CustomizerUndoEntryModel.theme_id == body.theme_id,
                    ~CustomizerUndoEntryModel.id.in_(keep_ids),
                )
            )

        await session.commit()
        await session.refresh(entry)

    return SuccessResponse(
        data=UndoEntryResponse(
            id=str(entry.id),
            theme_id=entry.theme_id,
            action_label=entry.action_label,
            forward=entry.forward,
            inverse=entry.inverse,
            created_at=entry.created_at.isoformat(),
        ),
        message="Undo entry appended",
    )


@router.delete(
    "/{entry_id}",
    response_model=SuccessResponse[dict[str, str]],
    summary="Remove an undo entry (after applying)",
    operation_id="delete_undo_entry",
)
async def delete_entry(
    store_id: UUID,
    entry_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
):
    """When the client undoes an action and applies the inverse, it
    deletes that entry so re-mounting doesn't replay the same undo
    over and over."""

    async with AsyncSessionLocal() as session:
        entry = (
            await session.execute(
                select(CustomizerUndoEntryModel).where(
                    CustomizerUndoEntryModel.id == entry_id,
                    CustomizerUndoEntryModel.user_id == user_id,
                    CustomizerUndoEntryModel.store_id == store_id,
                )
            )
        ).scalar_one_or_none()
        if entry is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found"
            )
        await session.delete(entry)
        await session.commit()
    return SuccessResponse(data={"id": str(entry_id)}, message="Entry removed")


@router.delete(
    "",
    response_model=SuccessResponse[dict[str, int]],
    summary="Clear undo stack",
    operation_id="clear_undo_stack",
)
async def clear_stack(
    store_id: UUID,
    theme_id: str,
    user_id: UUID = Depends(get_current_user_id),
):
    """On publish, the customizer can clear its undo stack — published
    state becomes the new baseline."""

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(CustomizerUndoEntryModel).where(
                CustomizerUndoEntryModel.user_id == user_id,
                CustomizerUndoEntryModel.store_id == store_id,
                CustomizerUndoEntryModel.theme_id == theme_id,
            )
        )
        await session.commit()
    return SuccessResponse(
        data={"deleted": result.rowcount or 0}, message="Undo stack cleared"
    )
