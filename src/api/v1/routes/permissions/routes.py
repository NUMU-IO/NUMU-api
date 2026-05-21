"""Permission catalog routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.infrastructure.database.models.public.permission import PermissionModel

router = APIRouter(prefix="/permissions", tags=["Permissions"])


@router.get("")
async def list_permissions(
    db: Annotated[AsyncSession, Depends(get_db)],
    domain: str | None = None,
):
    """Get permission catalog."""
    query = select(PermissionModel)

    if domain:
        query = query.where(PermissionModel.domain == domain)

    result = await db.execute(query)
    permissions = result.scalars().all()

    return {
        "permissions": [
            {
                "id": str(p.id),
                "code": p.code,
                "domain": p.domain,
                "action": p.action,
                "qualifier": p.qualifier,
                "scope_type": p.scope_type.value,
                "description": p.description,
                "dependencies": list(p.dependencies),
                "risk_level": p.risk_level.value,
                "is_app": p.is_app,
            }
            for p in permissions
        ]
    }


@router.get("/{permission_id}")
async def get_permission(
    permission_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get a specific permission."""
    permission = await db.get(PermissionModel, permission_id)

    if not permission:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Permission not found",
        )

    return {
        "id": str(permission.id),
        "code": permission.code,
        "domain": permission.domain,
        "action": permission.action,
        "qualifier": permission.qualifier,
        "scope_type": permission.scope_type.value,
        "description": permission.description,
        "dependencies": list(permission.dependencies),
        "risk_level": permission.risk_level.value,
        "is_app": permission.is_app,
    }
