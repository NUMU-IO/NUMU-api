"""Staff audit view: who did what, to which merchant, and when.

URL: /api/v1/admin/audit
Reads ``audit_logs`` newest first with keyset paging. ``next_cursor`` is
``<created_at>_<id>``: rows written in one transaction share ``created_at``
(``now()`` is the transaction start), so the id breaks the tie.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import exists, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.core.entities.user import UserRole
from src.infrastructure.database.models.audit import AuditLogModel
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.tenant.store import StoreModel

router = APIRouter(dependencies=[Depends(require_admin)])

Db = Annotated[AsyncSession, Depends(get_db)]


def _cursor(value: str) -> tuple[datetime, UUID]:
    try:
        at, _, row_id = value.rpartition("_")
        return datetime.fromisoformat(at), UUID(row_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid cursor") from None


@router.get("", response_model=SuccessResponse[dict])
async def list_audit(
    db: Db,
    user_id: UUID | None = None,
    tenant_id: UUID | None = None,
    store_id: UUID | None = None,
    event_type: Annotated[str | None, Query(max_length=50)] = None,
    merchant: Annotated[str | None, Query(max_length=100)] = None,
    actor: Literal["staff", "system"] | None = None,
    since: datetime | None = None,
    before: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    """Newest first. ``event_type`` is a prefix; ``merchant`` matches the
    tenant's name or subdomain; ``actor`` is staff (a platform admin) or
    system (no user)."""
    q = (
        select(
            AuditLogModel,
            UserModel.email,
            TenantModel.name,
            TenantModel.subdomain,
            StoreModel.name,
        )
        .outerjoin(UserModel, UserModel.id == AuditLogModel.user_id)
        .outerjoin(TenantModel, TenantModel.id == AuditLogModel.tenant_id)
        .outerjoin(StoreModel, StoreModel.id == AuditLogModel.store_id)
        .order_by(AuditLogModel.created_at.desc(), AuditLogModel.id.desc())
        .limit(limit)
    )
    if user_id:
        q = q.where(AuditLogModel.user_id == user_id)
    if tenant_id:
        q = q.where(AuditLogModel.tenant_id == tenant_id)
    if store_id:
        q = q.where(AuditLogModel.store_id == store_id)
    if event_type:
        q = q.where(AuditLogModel.event_type.startswith(event_type, autoescape=True))
    if merchant:
        term = merchant.strip()
        q = q.where(
            or_(
                TenantModel.name.icontains(term, autoescape=True),
                TenantModel.subdomain.icontains(term, autoescape=True),
            )
        )
    if actor == "staff":
        q = q.where(UserModel.role == UserRole.SUPER_ADMIN)
    elif actor == "system":
        q = q.where(AuditLogModel.user_id.is_(None))
    if since:
        q = q.where(AuditLogModel.created_at >= since)
    if before:
        q = q.where(
            tuple_(AuditLogModel.created_at, AuditLogModel.id)
            < tuple_(*_cursor(before))
        )

    rows = (await db.execute(q)).all()
    items = [
        {
            "id": str(log.id),
            "created_at": log.created_at.isoformat(),
            "event_type": log.event_type,
            "action": log.action,
            "severity": log.severity,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "user_id": str(log.user_id) if log.user_id else None,
            "actor_email": email,
            "tenant_id": str(log.tenant_id) if log.tenant_id else None,
            "tenant_name": tenant_name,
            "tenant_subdomain": subdomain,
            "store_id": str(log.store_id) if log.store_id else None,
            "store_name": store_name,
            "details": log.details,
        }
        for log, email, tenant_name, subdomain, store_name in rows
    ]
    last = rows[-1][0] if len(rows) == limit else None
    return SuccessResponse(
        data={
            "items": items,
            "next_cursor": f"{last.created_at.isoformat()}_{last.id}" if last else None,
        }
    )


@router.get("/actors", response_model=SuccessResponse[list[dict]])
async def list_audit_actors(db: Db):
    """Platform admins with at least one audit row, for the staff filter."""
    rows = (
        await db.execute(
            select(UserModel.id, UserModel.email)
            .where(
                UserModel.role == UserRole.SUPER_ADMIN,
                exists().where(AuditLogModel.user_id == UserModel.id),
            )
            .order_by(UserModel.email)
        )
    ).all()
    return SuccessResponse(data=[{"id": str(i), "email": e} for i, e in rows])
