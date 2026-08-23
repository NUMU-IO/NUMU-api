"""Authentication dependencies — cookie-based (with Bearer fallback)."""

from datetime import UTC
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.application.services.personal_access_token_service import looks_like_pat
from src.application.services.token_revocation_service import TokenRevocationService
from src.core.entities.user import UserRole
from src.core.exceptions import InvalidTokenError, TokenExpiredError
from src.core.logging import get_logger
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.database.connection import set_tenant_id
from src.infrastructure.external_services.token_service import token_service

_revocation_service = TokenRevocationService(RedisCacheService())
logger = get_logger(__name__)


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
    """Get current user ID from access_token cookie, JWT Bearer, or PAT."""
    payload = await _resolve_principal(request)

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
    """Get current user ID and role from cookie, JWT Bearer, or PAT."""
    payload = await _resolve_principal(request)
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
    """Get full token payload from cookie, JWT Bearer, or Personal Access Token."""
    return await _resolve_principal(request)


async def _resolve_principal(request: Request) -> TokenPayload:
    """Resolve the acting user from a cookie/JWT Bearer or a Personal Access Token.

    A single entry point used by ``get_current_user_id`` /
    ``get_current_user_role`` / ``get_current_token_payload`` so JWT and PAT
    callers travel the exact same downstream path (membership → RBAC → plan
    limits). JWT behaviour is unchanged; tokens prefixed with the PAT marker
    are validated against the ``personal_access_tokens`` table instead.
    """
    token = _bearer_token(request) or request.cookies.get("access_token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    if looks_like_pat(token):
        return await _resolve_pat_principal(token, request)

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


async def _resolve_pat_principal(token: str, request: Request) -> TokenPayload:
    """Validate a Personal Access Token and synthesize a ``TokenPayload``.

    Opens its own short-lived session (the request-scoped one isn't available
    this early in the dependency chain). The PAT, user, and store tables all
    live in the ``public`` schema, so the default search_path resolves them
    without tenant-schema switching.
    """
    from src.application.services.personal_access_token_service import (
        PersonalAccessTokenService,
        required_scope_for,
        scope_allows,
    )
    from src.infrastructure.database.connection import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        service = PersonalAccessTokenService(session)
        resolved = await service.authenticate(token)
        if resolved is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired access token",
            )
        record, user = resolved

        # Defense in depth: a PAT may only act on the tenant it was minted for.
        # The subdomain middleware has already resolved the target tenant, so a
        # token replayed against another store's subdomain is rejected here.
        tenant = getattr(request.state, "tenant", None)
        if tenant is not None and str(record.tenant_id) != str(tenant.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access token is not valid for this store",
            )

        # Hard store binding: a store-bound PAT may only touch its own store's
        # routes, even when the same owner has sibling stores in the tenant.
        path = request.url.path
        parts = [p for p in path.split("/") if p]
        if (
            record.store_id is not None
            and len(parts) >= 4
            and parts[0] == "api"
            and parts[1] == "v1"
            and parts[2] == "stores"
            and parts[3] != str(record.store_id)
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access token is bound to a different store",
            )

        # Central scope enforcement (routes stay scope-unaware). NULL scopes =
        # unrestricted legacy token; scoped tokens are default-deny outside the
        # mapped store surface and may never manage tokens themselves.
        if record.scopes is not None:
            required = required_scope_for(path, request.method)
            if required is None or not scope_allows(record.scopes, required):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "Access token does not permit this operation"
                        if required is None
                        else f"Access token lacks the '{required}' scope"
                    ),
                )

        await service.mark_used(record)
        await session.commit()

        # Expose PAT identity to downstream handlers (e.g. /auth/api-key/me)
        # without re-authenticating the token.
        request.state.pat = {
            "token_id": str(record.id),
            "name": record.name,
            "scopes": record.scopes,
            "store_id": str(record.store_id) if record.store_id else None,
            "tenant_id": str(record.tenant_id),
        }

        role_value = user.role.value if hasattr(user.role, "value") else str(user.role)
        return TokenPayload(
            user_id=user.id,
            email=user.email,
            role=role_value,
            exp=0,
            token_type="access",
            iat=0,
            tenant_id=record.tenant_id,
        )


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
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Store:
    """Get the current store, verifying ownership AND setting the RLS tenant.

    Loading the store here is the one point on every store-scoped merchant
    route where we know the authorised store — and therefore its tenant. We
    set that tenant as the RLS context so Postgres row-level security can
    enforce tenant isolation on this request. Merchant traffic arrives on the
    apex host, so `TenantMiddleware` (which derives tenant from the Host
    subdomain) never sets it; without this, RLS would filter every merchant
    query to zero rows the moment the app connects as a non-superuser role.

    This is inert while the API connects as the `postgres` superuser (which
    bypasses RLS), so setting it now changes nothing observable — it is the
    prerequisite that makes flipping to the enforcing app role safe. See
    `docs/REports/RLS-enforcement.md`.
    """
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

    # Set the tenant for RLS. `set_tenant_id` updates the contextvar (read by
    # any session opened later in the request); we ALSO apply it to the
    # already-open request session directly, because that session's GUC was
    # set at creation time (before this dependency ran) when no tenant was
    # known. Best-effort — a failure here must not break a legitimate request.
    if store.tenant_id:
        try:
            set_tenant_id(store.tenant_id)
            await db.execute(
                text("SELECT set_config('app.current_tenant', :v, true)"),
                {"v": str(store.tenant_id)},
            )
        except Exception:  # noqa: BLE001 — RLS wiring must never 500 a request
            logger.warning("rls_tenant_context_set_failed", store_id=str(store_id))

    return store


async def verify_store_ownership_streaming(
    store_id: UUID,
    user_id: Annotated[UUID, Depends(require_store_owner)],
) -> Store:
    """Ownership check for SSE routes that does NOT hold a pooled connection.

    FastAPI keeps ``yield`` dependencies alive until the response finishes,
    and an SSE response never finishes — so a stream depending on
    ``get_current_store`` pins its ``get_db`` session for the life of the
    connection. Every open hub tab then permanently consumes one slot of a
    pool sized 5 (+3 overflow), and the whole API starts timing out with
    "QueuePool limit ... reached".

    This opens its own session, verifies, and closes it before streaming
    begins. Safe because stream handlers do no further database work; they
    relay Redis. Do not use it on routes that query afterwards — they need
    the request-scoped session and its RLS tenant context.
    """
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.store_repository import StoreRepository

    async with AsyncSessionLocal() as session:
        store = await StoreRepository(session).get_by_id(store_id)

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
    if store.tenant_id:
        try:
            set_tenant_id(store.tenant_id)
        except Exception:  # noqa: BLE001 — RLS wiring must never 500 a request
            logger.warning("rls_tenant_context_set_failed", store_id=str(store_id))
    return store


# Alias — use in store-scoped routes for explicit ownership verification
verify_store_ownership = get_current_store
