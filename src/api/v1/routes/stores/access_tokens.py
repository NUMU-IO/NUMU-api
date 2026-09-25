"""Personal Access Token routes nested under stores.

URL: /stores/{store_id}/access-tokens

Lets a store owner mint long-lived API tokens for machine clients (the NUMU
MCP server, n8n, scripts, …). A token inherits the owner's permissions and is
scoped to the store's tenant. The raw token is returned exactly once at
creation; thereafter only metadata is exposed.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.application.services import api_limits
from src.application.services.api_access import api_access_for_store
from src.application.services.audit_service import AuditService
from src.application.services.entitlement_service import aware
from src.application.services.personal_access_token_service import (
    VALID_SCOPES,
    PersonalAccessTokenService,
)
from src.core.entities.store import Store
from src.infrastructure.database.models.public.personal_access_token import (
    PersonalAccessTokenModel,
)

router = APIRouter(prefix="/{store_id}/access-tokens")


class CreateAccessTokenRequest(BaseModel):
    """Request body for minting a personal access token."""

    name: str = Field(
        min_length=1,
        max_length=100,
        description="Human-friendly label, e.g. 'Claude MCP'.",
    )
    expires_in_days: int = Field(
        default=90,
        ge=1,
        le=3650,
        description="Lifetime in days. Defaults to 90; 3650 is the maximum.",
    )
    scopes: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Scope strings such as 'catalog:read', 'orders:write'. Required: "
            "a token is only as wide as the scopes it names. '*' is the "
            "owner-equivalent escape hatch and has to be asked for."
        ),
    )


class AccessTokenResponse(BaseModel):
    """Metadata for a personal access token (never includes the secret)."""

    id: str
    name: str
    token_prefix: str
    scopes: list[str] | None
    last_used_at: str | None
    expires_at: str | None
    revoked_at: str | None
    created_at: str


class CreatedAccessTokenResponse(AccessTokenResponse):
    """Returned once at creation — carries the raw secret a single time."""

    token: str = Field(description="The secret token. Shown once; store it now.")


def _to_response(record: PersonalAccessTokenModel) -> AccessTokenResponse:
    return AccessTokenResponse(
        id=str(record.id),
        name=record.name,
        token_prefix=record.token_prefix,
        scopes=record.scopes,
        last_used_at=record.last_used_at.isoformat() if record.last_used_at else None,
        expires_at=record.expires_at.isoformat() if record.expires_at else None,
        revoked_at=record.revoked_at.isoformat() if record.revoked_at else None,
        created_at=record.created_at.isoformat(),
    )


class ApiAccessState(BaseModel):
    """Whether this store may use the API, and where that comes from."""

    allowed: bool
    #: "plan" | "grant" | None
    source: str | None = None
    plan: str
    in_plan: bool
    granted: bool


@router.get(
    "/access",
    response_model=SuccessResponse[ApiAccessState],
    summary="Whether this store may use the public API",
    operation_id="get_api_access_state",
)
async def get_api_access_state(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse[ApiAccessState]:
    """Drives the Developers page: offer tokens, or explain why not."""
    access = await api_access_for_store(db, store.id)
    return SuccessResponse(
        data=ApiAccessState(
            allowed=access.allowed,
            source=access.source,
            plan=access.plan,
            in_plan=access.in_plan,
            granted=access.granted,
        )
    )


@router.post(
    "/",
    response_model=SuccessResponse[CreatedAccessTokenResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a personal access token",
    operation_id="create_access_token",
)
async def create_access_token(
    request: CreateAccessTokenRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Mint a long-lived API token for this store. The secret is returned once."""
    if store.tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Store is not associated with a tenant",
        )

    access = await api_access_for_store(db, store.id)
    if not access.allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "api_access_not_enabled",
                "message": (
                    "API access is not enabled for this store. It is included "
                    "in the Pro plan, or NUMU can enable it for your account."
                ),
                "plan": access.plan,
            },
        )

    invalid = sorted(set(request.scopes) - VALID_SCOPES)
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown scopes: {', '.join(invalid)}",
        )

    policy = await api_limits.policy_for_tenant(db, store.tenant_id)
    if policy.key_limit is not None:
        now = datetime.now(UTC)
        live = await db.scalar(
            select(func.count())
            .select_from(PersonalAccessTokenModel)
            .where(
                PersonalAccessTokenModel.tenant_id == store.tenant_id,
                PersonalAccessTokenModel.revoked_at.is_(None),
                (PersonalAccessTokenModel.expires_at.is_(None))
                | (PersonalAccessTokenModel.expires_at > now),
            )
        )
        if (live or 0) >= policy.key_limit:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "api_key_limit_reached",
                    "message": (
                        f"Your plan allows {policy.key_limit} active API keys. "
                        "Revoke one you no longer use, or rotate it instead."
                    ),
                    "limit": policy.key_limit,
                },
            )

    expires_at = datetime.now(UTC) + timedelta(days=request.expires_in_days)

    service = PersonalAccessTokenService(db)
    raw, record = await service.create(
        user_id=store.owner_id,
        tenant_id=store.tenant_id,
        store_id=store.id,
        name=request.name,
        expires_at=expires_at,
        scopes=request.scopes,
    )

    await AuditService(db).log(
        event_type="api_key.created",
        action="create",
        resource_type="api_key",
        resource_id=str(record.id),
        user_id=store.owner_id,
        store_id=store.id,
        tenant_id=store.tenant_id,
        details={
            "name": record.name,
            "scopes": record.scopes,
            "prefix": record.token_prefix,
        },
    )

    base = _to_response(record)
    return SuccessResponse(
        data=CreatedAccessTokenResponse(**base.model_dump(), token=raw),
        message="Access token created. Copy it now — it won't be shown again.",
    )


@router.get(
    "/",
    response_model=SuccessResponse[list[AccessTokenResponse]],
    summary="List personal access tokens",
    operation_id="list_access_tokens",
)
async def list_access_tokens(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List this owner's tokens for the store's tenant (secrets excluded)."""
    if store.tenant_id is None:
        return SuccessResponse(data=[])

    service = PersonalAccessTokenService(db)
    records = await service.list_for(user_id=store.owner_id, tenant_id=store.tenant_id)
    return SuccessResponse(data=[_to_response(r) for r in records])


@router.delete(
    "/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a personal access token",
    operation_id="revoke_access_token",
)
async def revoke_access_token(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
    token_id: Annotated[UUID, Path(description="The token id to revoke")],
):
    """Permanently revoke a token. Idempotent; safe to call on an already-revoked id."""
    service = PersonalAccessTokenService(db)
    record = await service.revoke(token_id=token_id, user_id=store.owner_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Access token not found",
        )
    await AuditService(db).log(
        event_type="api_key.revoked",
        action="delete",
        resource_type="api_key",
        resource_id=str(record.id),
        user_id=store.owner_id,
        store_id=store.id,
        tenant_id=store.tenant_id,
    )
    # Commit before dropping the cached key: dropped first, a request in
    # between could re-cache it from the not-yet-revoked row.
    await db.commit()
    await api_limits.forget_key(record.token_hash)


@router.post(
    "/{token_id}/rotate",
    response_model=SuccessResponse[CreatedAccessTokenResponse],
    summary="Rotate a personal access token",
    operation_id="rotate_access_token",
)
async def rotate_access_token(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
    token_id: Annotated[UUID, Path(description="The token id to rotate")],
):
    """Replace a live token with a new secret: same name, scopes and lifetime.

    The old secret stops working immediately; there is no overlap window, so
    deploy the new one before rotating a key a live integration depends on.
    Doesn't count against the key limit: one key goes as one comes.
    """
    old = await db.scalar(
        select(PersonalAccessTokenModel).where(
            PersonalAccessTokenModel.id == token_id,
            PersonalAccessTokenModel.user_id == store.owner_id,
            PersonalAccessTokenModel.tenant_id == store.tenant_id,
        )
    )
    now = datetime.now(UTC)
    expires = aware(old.expires_at) if old else None
    if (
        old is None
        or old.revoked_at is not None
        or (expires is not None and expires <= now)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Access token not found or no longer active",
        )

    lifetime = expires - aware(old.created_at) if expires else None
    service = PersonalAccessTokenService(db)
    raw, record = await service.create(
        user_id=old.user_id,
        tenant_id=old.tenant_id,
        store_id=old.store_id,
        name=old.name,
        expires_at=now + lifetime if lifetime else None,
        scopes=old.scopes,
    )
    old.revoked_at = now
    await AuditService(db).log(
        event_type="api_key.rotated",
        action="update",
        resource_type="api_key",
        resource_id=str(record.id),
        user_id=store.owner_id,
        store_id=store.id,
        tenant_id=store.tenant_id,
        details={"replaced": str(old.id), "prefix": record.token_prefix},
    )
    base = _to_response(record)
    await db.commit()
    await api_limits.forget_key(old.token_hash)
    return SuccessResponse(
        data=CreatedAccessTokenResponse(**base.model_dump(), token=raw),
        message="Key rotated. Copy the new key now — it won't be shown again.",
    )


#: A GET on one of these, averaging a call a minute or more today, is polling
#: that a webhook would replace. ponytail: route-name heuristic, no per-client
#: fingerprinting; revisit when the admin "suspicious integrations" view needs
#: more than this.
_POLLED = ("orders", "products", "customers", "inventory")
_POLL_MIN_REQUESTS = 300


def _usage_warnings(
    policy, month_used: int, today_rows: dict, now: datetime
) -> list[dict]:
    warnings: list[dict] = []
    quota = policy.monthly_quota
    if quota:
        share = month_used / quota
        if share >= 1:
            warnings.append({"code": "quota_reached", "percent": 100})
        elif share >= 0.8:
            warnings.append({"code": "quota_80", "percent": int(share * 100)})
    minutes = max(1, now.hour * 60 + now.minute)
    per_route: dict[str, int] = {}
    throttled = 0
    for (_, method, route), m in today_rows.items():
        throttled += m["throttled"]
        if method == "GET" and route.rstrip("/").rsplit("/", 1)[-1] in _POLLED:
            per_route[route] = per_route.get(route, 0) + m["requests"]
    for route, n in sorted(per_route.items(), key=lambda kv: -kv[1]):
        if n >= _POLL_MIN_REQUESTS and n / minutes >= 1:
            warnings.append({
                "code": "polling",
                "route": route,
                "per_minute": round(n / minutes, 1),
            })
    if throttled:
        warnings.append({"code": "throttled", "count": throttled})
    return warnings


@router.get(
    "/usage",
    response_model=SuccessResponse[dict],
    summary="API usage, limits and warnings for this store",
    operation_id="get_api_usage",
)
async def get_api_usage(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Today comes live from Redis; earlier days from ``api_usage_daily``."""
    from src.infrastructure.database.models.public.api_usage import (
        ApiUsageDailyModel as U,
    )

    if store.tenant_id is None:
        raise HTTPException(
            status_code=400, detail="Store is not associated with a tenant"
        )
    tid = str(store.tenant_id)
    now = datetime.now(UTC)
    today = now.date()
    policy = await api_limits.policy_for_tenant(db, store.tenant_id)
    today_rows = await api_limits.usage_today(tid, now)
    month_used = await api_limits.quota_used(tid, now)
    if month_used is None:
        month_used = await api_limits.month_usage_from_db(db, store.tenant_id, now)

    since = today - timedelta(days=29)
    daily = (
        await db.execute(
            select(
                U.day,
                func.sum(U.requests),
                func.sum(U.throttled),
                func.sum(U.errors_4xx + U.errors_5xx),
            )
            .where(U.tenant_id == store.tenant_id, U.day >= since, U.day < today)
            .group_by(U.day)
            .order_by(U.day)
        )
    ).all()
    month_routes = (
        await db.execute(
            select(U.method, U.route, func.sum(U.requests))
            .where(
                U.tenant_id == store.tenant_id,
                U.day >= today.replace(day=1),
                U.day < today,
            )
            .group_by(U.method, U.route)
        )
    ).all()

    totals = {"requests": 0, "throttled": 0, "errors_4xx": 0, "errors_5xx": 0}
    routes: dict[tuple[str, str], int] = {(m, r): int(n) for m, r, n in month_routes}
    per_key: dict[str, int] = {}
    for (token, method, route), m in today_rows.items():
        for k in totals:
            totals[k] += m[k]
        routes[(method, route)] = routes.get((method, route), 0) + m["requests"]
        per_key[token] = per_key.get(token, 0) + m["requests"]
    errors = totals["errors_4xx"] + totals["errors_5xx"]

    return SuccessResponse(
        data={
            "limits": {
                "per_minute": policy.per_minute,
                "per_second": policy.per_second,
                "monthly_quota": policy.monthly_quota,
                "key_limit": policy.key_limit,
            },
            "month": {"used": month_used, "quota": policy.monthly_quota},
            "today": totals
            | {
                "error_rate": round(errors / totals["requests"], 4)
                if totals["requests"]
                else 0
            },
            "daily": [
                {
                    "day": d.isoformat(),
                    "requests": int(n),
                    "throttled": int(t),
                    "errors": int(e),
                }
                for d, n, t, e in daily
            ]
            + [
                {
                    "day": today.isoformat(),
                    "requests": totals["requests"],
                    "throttled": totals["throttled"],
                    "errors": errors,
                }
            ],
            "top_endpoints": [
                {"method": m, "route": r, "requests": n}
                for (m, r), n in sorted(routes.items(), key=lambda kv: -kv[1])[:10]
            ],
            "keys_today": per_key,
            "warnings": _usage_warnings(policy, month_used, today_rows, now),
        }
    )
