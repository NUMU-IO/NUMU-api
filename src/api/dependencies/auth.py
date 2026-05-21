"""Authentication dependencies — cookie-based (with Bearer fallback)."""

from datetime import UTC
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status

from src.application.services.token_revocation_service import TokenRevocationService
from src.core.entities.user import UserRole
from src.core.exceptions import InvalidTokenError, TokenExpiredError
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.external_services.token_service import token_service

_revocation_service = TokenRevocationService(RedisCacheService())


def _bearer_token(request: Request) -> str | None:
    """Extract the JWT from an ``Authorization: Bearer …`` header.

    Returns None when the header is missing or malformed — the caller
    falls back to the cookie. Enables tab-isolated impersonation sessions
    on the merchant hub (sessionStorage → Authorization header) without
    disturbing the cookie-based session of merchants who aren't being
    impersonated.
    """
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


async def get_current_user_id(request: Request) -> UUID:
    """Get current user ID from access_token httpOnly cookie or Bearer header."""
    token = _bearer_token(request) or request.cookies.get("access_token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    try:
        payload = token_service.verify_token(token)
    except TokenExpiredError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    if await _revocation_service.is_revoked(payload.user_id, payload.iat):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been revoked. Please log in again.",
        )

    # Populate the user RLS contextvar early in the dependency chain so
    # the eventual `get_db_session()` can stamp `app.current_user` on
    # the Postgres session. Marketplace user-scoped RLS policies
    # (purchases, reviews) read this. Anonymous routes (no auth dep)
    # leave it None and the policies fall through to admin_bypass for
    # legitimate cross-user reads handled via get_admin_db_session.
    from src.infrastructure.database.connection import set_user_id

    set_user_id(payload.user_id)
    return payload.user_id


async def get_current_user_role(request: Request) -> tuple[UUID, str]:
    """Get current user ID and role from access_token httpOnly cookie or Bearer header."""
    token = _bearer_token(request) or request.cookies.get("access_token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    try:
        payload = token_service.verify_token(token)
    except (TokenExpiredError, InvalidTokenError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )

    if await _revocation_service.is_revoked(payload.user_id, payload.iat):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been revoked. Please log in again.",
        )

    return payload.user_id, payload.role


def require_roles(*allowed_roles: UserRole):
    """Dependency factory that requires specific user roles."""

    async def role_checker(
        user_data: Annotated[tuple[UUID, str], Depends(get_current_user_role)],
    ) -> UUID:
        user_id, role = user_data

        try:
            user_role = UserRole(role)
        except ValueError:
            try:
                user_role = UserRole[role.upper()]
            except KeyError:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Invalid user role",
                )

        if user_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )

        return user_id

    return role_checker


# Common role dependencies
require_store_owner = require_roles(UserRole.STORE_OWNER, UserRole.SUPER_ADMIN)


async def _get_admin_user_role(request: Request) -> tuple[UUID, str]:
    """Resolve the acting user from the admin cookie namespace.

    Prefers the `admin_access_token` cookie so the admin panel's session is
    isolated from the merchant-hub `access_token` cookie. Falls back to
    `access_token` so existing admin sessions keep working until the admin
    UI has migrated to the new login endpoint.
    """
    token = request.cookies.get("admin_access_token") or request.cookies.get(
        "access_token"
    )
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    try:
        payload = token_service.verify_token(token)
    except (TokenExpiredError, InvalidTokenError) as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    if await _revocation_service.is_revoked(payload.user_id, payload.iat):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been revoked. Please log in again.",
        )

    return payload.user_id, payload.role


async def require_admin(
    user_data: Annotated[tuple[UUID, str], Depends(_get_admin_user_role)],
) -> UUID:
    """Require the caller to be a platform admin, reading the admin cookie
    first so impersonation can't evict the admin session from this tab."""
    user_id, role = user_data
    try:
        user_role = UserRole(role)
    except ValueError:
        try:
            user_role = UserRole[role.upper()]
        except KeyError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Invalid user role"
            )
    if user_role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )
    return user_id


# ── Admin 2FA step-up ────────────────────────────────────────────────────────
#
# `require_admin_2fa(max_age_seconds=N)` is the dependency we hang on
# any admin action that has irreversible blast-radius (approving a
# marketplace theme, refunding a purchase). It enforces:
#
#   1. SUPER_ADMIN role (delegates to `require_admin`).
#   2. The admin has 2FA *enabled* — not just enrolled-but-pending.
#   3. The admin completed a 2FA challenge within the last
#      `max_age_seconds` (default 5 minutes). Stale logins prompt the
#      frontend to pop a re-verify modal — much better than letting a
#      forgotten-laptop session approve themes.
#
# We consult `two_factor.last_used_at` for the freshness window
# because that's what the verify-2FA flow bumps on every challenge
# completion. `verified_at` (set at enrollment) would only ever
# advance when the admin re-enrolls — useless for step-up.
#
# Failure modes return 403 with explicit `detail`s so the frontend
# can branch:
#   - "2FA required ..." → admin needs to enroll first.
#   - "2FA verification expired ..." → admin needs to re-verify.
# We avoid 401 here since the access token is still valid; 403 is the
# correct "you're authenticated but lacking the necessary credential
# strength" code.
def require_admin_2fa(max_age_seconds: int = 300):
    """Build a dependency that requires recent 2FA verification.

    Pass into a route's `dependencies=[...]` list. The factory returns
    the dependency function so each call site can specify a different
    freshness window without having to hand-roll the closure.
    """
    from datetime import datetime

    from src.api.dependencies.repositories import get_two_factor_repository
    from src.infrastructure.repositories.two_factor_repository import (
        TwoFactorRepository,
    )

    async def _check(
        admin_id: Annotated[UUID, Depends(require_admin)],
        two_factor_repo: Annotated[
            TwoFactorRepository, Depends(get_two_factor_repository)
        ],
    ) -> UUID:
        # Dev/staging skip: enforcing 2FA in non-production blocks
        # local engineers from approving/rejecting their own test
        # submissions. Production keeps strict enforcement so a stale
        # session can't approve themes platform-wide. Override with
        # NUMU_FORCE_ADMIN_2FA=true to test the prod path locally.
        from src.config import settings as _settings

        force = __import__("os").environ.get("NUMU_FORCE_ADMIN_2FA", "").lower() in (
            "1",
            "true",
            "yes",
        )
        if _settings.environment != "production" and not force:
            return admin_id

        record = await two_factor_repo.get_by_user_id(admin_id)
        if record is None or not record.is_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="2FA required for this action — enroll in 2FA first.",
            )
        # Prefer the most recent challenge completion. Falls back to
        # `verified_at` (enrollment) for users whose last_used_at hasn't
        # been populated yet (legacy rows from before the column was
        # being bumped on every login).
        ref = record.last_used_at or record.verified_at
        if ref is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="2FA verification required.",
            )
        # Compare in UTC. Ref is timezone-aware (DateTime(timezone=True))
        # — naïve `datetime.utcnow()` would silently drift ~hours under
        # DST so we use `datetime.now(timezone.utc)` deliberately.
        elapsed = (datetime.now(UTC) - ref).total_seconds()
        if elapsed > max_age_seconds:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"2FA verification expired after {max_age_seconds}s. "
                    "Please re-verify."
                ),
            )
        return admin_id

    return _check


from src.api.dependencies.repositories import (
    get_customer_repository,
    get_store_repository,
)
from src.core.entities.customer import Customer
from src.core.entities.store import Store
from src.core.interfaces.services.token_service import (
    CustomerTokenPayload,
    TokenPayload,
)
from src.infrastructure.repositories.customer_repository import CustomerRepository
from src.infrastructure.repositories.store_repository import StoreRepository


async def get_current_token_payload(request: Request) -> TokenPayload:
    """Get full token payload from access_token httpOnly cookie or Bearer header."""
    token = _bearer_token(request) or request.cookies.get("access_token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    try:
        payload = token_service.verify_token(token)
    except TokenExpiredError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    if await _revocation_service.is_revoked(payload.user_id, payload.iat):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been revoked. Please log in again.",
        )

    return payload


async def get_current_customer_payload(request: Request) -> CustomerTokenPayload:
    """Get current customer payload from customer_access_token httpOnly cookie."""
    token = request.cookies.get("customer_access_token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    try:
        return token_service.verify_customer_token(token)
    except (TokenExpiredError, InvalidTokenError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )


async def get_current_customer(
    payload: Annotated[CustomerTokenPayload, Depends(get_current_customer_payload)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
) -> Customer:
    """Get current customer from JWT token."""
    customer = await customer_repo.get_by_id(payload.customer_id)
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Customer not found",
        )

    return customer


async def get_optional_customer(
    request: Request,
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
) -> Customer | None:
    """Get current customer if authenticated, else None (for guest checkout)."""
    token = request.cookies.get("customer_access_token")
    if not token:
        return None
    try:
        payload = token_service.verify_customer_token(token)
    except (TokenExpiredError, InvalidTokenError):
        return None
    return await customer_repo.get_by_id(payload.customer_id)


async def get_current_store(
    store_id: UUID,
    user_id: Annotated[UUID, Depends(require_store_owner)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
) -> Store:
    """Get the current store, verifying ownership."""
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Store not found",
        )
    if store.owner_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this store",
        )
    return store


# Alias — use in store-scoped routes for explicit ownership verification
verify_store_ownership = get_current_store
