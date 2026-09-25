"""Admin view of merchant API tokens and what each one is doing (SUPER_ADMIN).

Lists every personal access token with who minted it and for which store,
plus a summary of its recent request trail, and returns the trail itself for
one token. Read-only: revoking stays with the merchant, or with the per-tenant
API-access switch on the merchant page.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.middleware.token_activity import KEEP, recent_requests
from src.api.responses import SuccessResponse
from src.infrastructure.database.models.public.personal_access_token import (
    PersonalAccessTokenModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.tenant.store import StoreModel

router = APIRouter(
    prefix="/api-tokens",
    tags=["Admin - API tokens"],
    dependencies=[Depends(require_admin)],
)

REFUSED = {401, 402, 403}


@router.get("", response_model=SuccessResponse[list[dict]])
async def list_api_tokens(db: Annotated[AsyncSession, Depends(get_db)]):
    rows = (
        await db.execute(
            select(
                PersonalAccessTokenModel,
                TenantModel.subdomain,
                StoreModel.name,
                UserModel.email,
            )
            .outerjoin(
                TenantModel, TenantModel.id == PersonalAccessTokenModel.tenant_id
            )
            .outerjoin(StoreModel, StoreModel.id == PersonalAccessTokenModel.store_id)
            .outerjoin(UserModel, UserModel.id == PersonalAccessTokenModel.user_id)
            .order_by(PersonalAccessTokenModel.last_used_at.desc().nullslast())
        )
    ).all()

    items = []
    for token, subdomain, store_name, email in rows:
        trail = await recent_requests(str(token.id))
        items.append({
            "id": str(token.id),
            "name": token.name,
            "token_prefix": token.token_prefix,
            "scopes": token.scopes,
            "tenant": subdomain,
            "store_id": str(token.store_id) if token.store_id else None,
            "store_name": store_name,
            "minted_by": email,
            "created_at": token.created_at,
            "last_used_at": token.last_used_at,
            "expires_at": token.expires_at,
            "revoked_at": token.revoked_at,
            "trail": {
                "requests": len(trail),
                "refused": sum(1 for e in trail if e["status"] in REFUSED),
                "distinct_ips": len({e["ip"] for e in trail}),
                "last": trail[0] if trail else None,
            },
        })
    return SuccessResponse(data=items)


@router.get("/{token_id}/requests", response_model=SuccessResponse[list[dict]])
async def list_api_token_requests(
    token_id: UUID,
    limit: Annotated[int, Query(ge=1, le=KEEP)] = 200,
):
    return SuccessResponse(data=await recent_requests(str(token_id), limit))


@router.get("/usage", response_model=SuccessResponse[dict])
async def api_usage_overview(db: Annotated[AsyncSession, Depends(get_db)]):
    """Platform-wide API usage from ``api_usage_daily`` (today is at most one
    flush, 5 minutes, behind): top consumers, merchants near their quota,
    throttling, error rates and the slowest and busiest endpoints."""
    from datetime import UTC, datetime

    from sqlalchemy import case, func

    from src.application.services import api_limits
    from src.infrastructure.database.models.public.api_usage import (
        ApiUsageDailyModel as U,
    )

    today = datetime.now(UTC).date()
    month_start = today.replace(day=1)
    today_n = func.sum(case((U.day == today, U.requests), else_=0))

    tenants = (
        await db.execute(
            select(
                U.tenant_id,
                TenantModel.subdomain,
                func.sum(U.requests).label("month"),
                today_n.label("today"),
                func.sum(U.throttled).label("throttled"),
                func.sum(U.errors_5xx).label("errors_5xx"),
                func.max(U.updated_at).label("last_activity"),
            )
            .join(TenantModel, TenantModel.id == U.tenant_id)
            .where(U.day >= month_start)
            .group_by(U.tenant_id, TenantModel.subdomain)
            .order_by(func.sum(U.requests).desc())
            .limit(50)
        )
    ).all()

    consumers = []
    for row in tenants:
        policy = await api_limits.policy_for_tenant(db, row.tenant_id)
        used = int(row.month) - int(row.throttled)
        quota = policy.monthly_quota
        consumers.append({
            "tenant_id": str(row.tenant_id),
            "subdomain": row.subdomain,
            "requests_today": int(row.today),
            "requests_month": int(row.month),
            "throttled_month": int(row.throttled),
            "errors_5xx_month": int(row.errors_5xx),
            "last_activity": row.last_activity.isoformat()
            if row.last_activity
            else None,
            "monthly_quota": quota,
            "quota_percent": round(100 * used / quota, 1) if quota else None,
            "per_minute": policy.per_minute,
            # Throttled on more than a tenth of its calls: the integration
            # is ignoring 429s or polling.
            "suspicious": int(row.month) > 0
            and int(row.throttled) / int(row.month) > 0.1,
        })

    routes = (
        await db.execute(
            select(
                U.method,
                U.route,
                func.sum(U.requests).label("n"),
                func.sum(U.errors_5xx).label("e5"),
                func.sum(U.latency_ms_sum).label("ms"),
            )
            .where(U.day >= month_start)
            .group_by(U.method, U.route)
        )
    ).all()
    endpoints = [
        {
            "method": r.method,
            "route": r.route,
            "requests": int(r.n),
            "errors_5xx": int(r.e5),
            "avg_ms": round(int(r.ms) / int(r.n), 1) if r.n else 0,
        }
        for r in routes
    ]
    totals = (
        await db.execute(
            select(
                func.coalesce(func.sum(U.requests), 0),
                func.coalesce(func.sum(U.throttled), 0),
                func.coalesce(func.sum(U.errors_5xx), 0),
            ).where(U.day >= month_start)
        )
    ).one()
    today_total = await db.scalar(
        select(func.coalesce(func.sum(U.requests), 0)).where(U.day == today)
    )
    return SuccessResponse(
        data={
            "requests_today": int(today_total or 0),
            "requests_month": int(totals[0]),
            "throttled_month": int(totals[1]),
            "error_5xx_rate": round(int(totals[2]) / int(totals[0]), 4)
            if totals[0]
            else 0,
            "top_consumers": consumers,
            "near_quota": [c for c in consumers if (c["quota_percent"] or 0) >= 80],
            "suspicious": [c for c in consumers if c["suspicious"]],
            "top_endpoints": sorted(endpoints, key=lambda e: -e["requests"])[:15],
            "slowest_endpoints": sorted(
                (e for e in endpoints if e["requests"] >= 50),
                key=lambda e: -e["avg_ms"],
            )[:15],
        }
    )
