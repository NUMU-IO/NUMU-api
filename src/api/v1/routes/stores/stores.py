"""Store CRUD routes."""

import logging
import re
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    get_current_user_id,
    get_store_repository,
    get_storefront_cache_service,
    require_store_owner,
    verify_store_ownership,
)
from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import get_onboarding_repository
from src.api.responses import SuccessResponse
from src.api.v1.schemas import (
    CreateStoreRequest,
    PaginatedListResponse,
    StoreResponse,
    UpdateStoreRequest,
)
from src.api.v1.schemas.tenant.store import (
    CheckSubdomainRequest,
    CheckSubdomainResponse,
    ConnectCustomDomainRequest,
    CustomDomainDnsRecord,
    CustomDomainStatusResponse,
)
from src.application.dto.store import CreateStoreDTO, UpdateStoreDTO
from src.application.use_cases.stores import (
    CreateStoreUseCase,
    DeleteStoreUseCase,
    ListStoresUseCase,
    UpdateStoreUseCase,
)
from src.application.use_cases.stores.create_store import (
    RESERVED_SUBDOMAINS,
    validate_subdomain,
)
from src.core.entities.store import Store

logger = logging.getLogger(__name__)
from src.core.value_objects.money import Currency
from src.infrastructure.cache import StorefrontCache
from src.infrastructure.external_services import google_search_console
from src.infrastructure.external_services.cloudflare import (
    CloudflareCustomHostnameError,
    cloudflare_custom_hostname_service,
    cloudflare_dns_service,
)
from src.infrastructure.repositories import OnboardingRepository, StoreRepository
from src.infrastructure.tenancy.service import TenantService

router = APIRouter()


def _build_store_response(store) -> StoreResponse:
    """Build StoreResponse from store DTO."""
    return StoreResponse(
        id=str(store.id),
        owner_id=str(store.owner_id),
        name=store.name,
        slug=store.slug,
        subdomain=store.subdomain,
        custom_domain=store.custom_domain,
        store_url=store.store_url,
        description=store.description,
        logo_url=store.logo_url,
        banner_url=store.banner_url,
        status=store.status,
        default_currency=store.default_currency,
        country=getattr(store, "country", "EG"),
        default_language=store.default_language,
        contact_email=store.contact_email,
        contact_phone=store.contact_phone,
        address=store.address,
        social_links=store.social_links,
        settings=getattr(store, "settings", None) or {},
        theme_settings=store.theme_settings,
        business_hours=getattr(store, "business_hours", None) or {},
        created_at=str(store.created_at),
        updated_at=str(store.updated_at),
    )


@router.post(
    "/check-subdomain",
    response_model=SuccessResponse[CheckSubdomainResponse],
    summary="Check subdomain availability",
    operation_id="check_subdomain",
)
async def check_subdomain(
    request: CheckSubdomainRequest,
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Check if a subdomain is available for use."""
    subdomain = request.subdomain.lower().strip()

    # Check reserved
    if subdomain in RESERVED_SUBDOMAINS:
        return SuccessResponse(
            data=CheckSubdomainResponse(
                subdomain=subdomain,
                available=False,
                message=f"'{subdomain}' is a reserved subdomain",
            ),
            message="Subdomain check completed",
        )

    # Validate format
    try:
        validate_subdomain(subdomain)
    except Exception as e:
        return SuccessResponse(
            data=CheckSubdomainResponse(
                subdomain=subdomain,
                available=False,
                message=str(e),
            ),
            message="Subdomain check completed",
        )

    # Check if exists
    exists = await store_repo.subdomain_exists(subdomain)

    return SuccessResponse(
        data=CheckSubdomainResponse(
            subdomain=subdomain,
            available=not exists,
            message="Subdomain is already taken"
            if exists
            else "Subdomain is available",
        ),
        message="Subdomain check completed",
    )


@router.post(
    "/",
    response_model=SuccessResponse[StoreResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create new store",
    operation_id="create_store",
)
async def create_store(
    request: CreateStoreRequest,
    user_id: Annotated[UUID, Depends(require_store_owner)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a new store with a subdomain."""
    from datetime import datetime

    from sqlalchemy import select

    from src.infrastructure.database.models.public.user import UserModel

    # Determine plan based on user's trial status
    user_result = await db.execute(select(UserModel).where(UserModel.id == user_id))
    user = user_result.scalar_one_or_none()
    plan = (
        "demo"
        if user and user.trial_ends_at and user.trial_ends_at > datetime.now(UTC)
        else "free"
    )

    store_repo = StoreRepository(db)
    onboarding_repo = OnboardingRepository(db)
    tenant_service = TenantService(db)
    use_case = CreateStoreUseCase(
        store_repository=store_repo,
        tenant_service=tenant_service,
        onboarding_repository=onboarding_repo,
    )

    dto = CreateStoreDTO(
        name=request.name,
        subdomain=request.subdomain,
        slug=request.slug,
        description=request.description,
        default_currency=request.default_currency,
        country=request.country,
        default_language=request.default_language,
        contact_email=request.contact_email,
        contact_phone=request.contact_phone,
    )

    result = await use_case.execute(dto, owner_id=user_id, plan=plan)

    # Landing plan intent: a visitor who clicked "Pay as you Grow" on the
    # pricing page goes straight onto payg — no billing page detour. The
    # activation snapshots today's commission rate into their wallet
    # (rate lock) and opens the go-live gate. Paid intents (starter/pro)
    # are left recorded — those merchants still subscribe normally.
    #
    # The target is the tenant of the store we JUST created (each store
    # creation mints its own tenant) — never looked up by owner_id, which
    # is not unique for multi-store owners and would raise
    # MultipleResultsFound on the second store.
    try:
        if user and user.plan_intent == "payg" and result.tenant_id is not None:
            from src.application.use_cases.billing.subscribe import (
                SubscribeUseCase,
            )

            await SubscribeUseCase(db).execute(tenant_id=result.tenant_id, plan="payg")
            user.plan_intent = None  # applied — don't re-run on store #2
            # Make the clear part of the pending statements now rather
            # than relying on request-teardown autoflush semantics.
            await db.flush()
    except Exception:
        # Never fail store creation over plan activation — the merchant
        # can still pick payg from Billing. The condition itself is
        # inside the try: an attribute regression here once 500'd every
        # payg-intent signup on prod (StoreDTO had no tenant_id).
        logger.warning("payg_intent_activation_failed", exc_info=True)

    if result.subdomain:
        await cloudflare_dns_service.ensure_store_subdomain(result.subdomain)

        # Hand Google the new host's sitemap. Without this a storefront can be
        # flawless on-page — 200, SSR'd, self-canonical, index/follow, listed in
        # its own sitemap — and still sit at "URL is unknown to Google" forever,
        # because nothing ever told Google the host exists. Inert unless
        # GOOGLE_SEARCH_CONSOLE_CREDENTIALS_JSON is set, and never fatal: a
        # marketing ping must not roll back a store the merchant just created.
        try:
            await google_search_console.submit_store_sitemap(result.subdomain)
        except Exception:  # noqa: BLE001
            logger.warning("search_console_submit_failed", exc_info=True)

    # Seed the platform default theme for the brand-new store (file 04 §5.2).
    # Best-effort — a failure here doesn't roll back store creation; the
    # merchant just lands without a theme assigned and can pick one from
    # the marketplace later. sawsaw + rabbit are not in scope: they were
    # created before this code existed.
    #
    # ``result.id`` is already a UUID (StoreDTO.id is typed UUID); when
    # asyncpg yields an `asyncpg.pgproto.pgproto.UUID` it has no
    # ``replace`` attr so passing it through ``UUID(...)`` would crash.
    # Coerce via ``str()`` to get a plain stdlib UUID.
    await _seed_default_theme_if_configured(
        db=db,
        store_id=UUID(str(result.id)),
        owner_id=user_id,
    )

    return SuccessResponse(
        data=_build_store_response(result),
        message="Store created successfully",
    )


async def _seed_default_theme_if_configured(
    *,
    db: AsyncSession,
    store_id: UUID,
    owner_id: UUID,
) -> None:
    """Install + activate the platform default theme on a freshly-created
    store, if one is configured.

    Both the install and the activate route through the existing
    ``MarketplaceService`` so all the snapshot/sync invariants from
    Phase 3a's ``ThemeActivationService`` are preserved — in particular,
    the snapshot row will be written with ``reason="initial-store-creation"``
    via the ``ThemeActivationService.activate`` path (no prior active
    row means the snapshot is skipped, which is correct).

    Errors here are LOGGED, not raised. The store has already been
    created and we can't roll that back without a more invasive
    transaction restructure — and silently shipping a store without a
    default theme is no worse than what users see when the admin
    hasn't configured a default at all.
    """
    import logging

    from src.application.services.marketplace_service import MarketplaceService
    from src.application.services.platform_default_theme_service import (
        PlatformDefaultThemeService,
    )
    from src.infrastructure.repositories import (
        MarketplaceRepository,
        StoreThemeRepository,
    )
    from src.infrastructure.repositories import (
        StoreRepository as StoreRepoCls,
    )
    from src.infrastructure.repositories.theme_repository import ThemeRepository
    from src.infrastructure.repositories.theme_version_repository import (
        ThemeVersionRepository,
    )

    seed_logger = logging.getLogger(__name__ + "._seed_default_theme")

    marketplace_repo = MarketplaceRepository(db)
    default_svc = PlatformDefaultThemeService(db, marketplace_repo)

    default_theme_id = await default_svc.get_default_theme_id()
    if default_theme_id is None:
        seed_logger.debug(
            "default_theme_seed_skipped_no_config",
            extra={"store_id": str(store_id)},
        )
        return

    try:
        svc = MarketplaceService(
            marketplace_repo=marketplace_repo,
            store_theme_repo=StoreThemeRepository(db),
            store_repo=StoreRepoCls(db),
            theme_repo=ThemeRepository(db),
            version_repo=ThemeVersionRepository(db),
        )
        # Install creates the marketplace_theme_installations row that
        # activate_theme's get_installation lookup needs.
        await svc.install_theme(
            store_id=store_id,
            marketplace_theme_id=default_theme_id,
            user_id=owner_id,
        )
        # Activate flips is_active on both store_themes + the install row
        # and seeds customization_v3 from the version's presets (per
        # ThemeActivationService.activate). The "initial-store-creation"
        # reason string distinguishes this from a merchant-driven activate
        # in the admin restore browser.
        #
        # MarketplaceService.activate_theme uses
        # reason="marketplace-activate" internally, so this still gets
        # tagged that way — file 04 §5.2 suggested a custom reason but
        # we'd have to either add a kwarg or fork the method. Documented
        # follow-up: thread reason through MarketplaceService.activate_theme.
        await svc.activate_theme(
            store_id=store_id,
            marketplace_theme_id=default_theme_id,
            user_id=owner_id,
        )
        seed_logger.info(
            "default_theme_seeded",
            extra={
                "store_id": str(store_id),
                "marketplace_theme_id": str(default_theme_id),
            },
        )
    except Exception as exc:  # noqa: BLE001 — best-effort seeding
        seed_logger.warning(
            "default_theme_seed_failed store=%s theme=%s err=%s type=%s",
            str(store_id),
            str(default_theme_id),
            exc,
            type(exc).__name__,
            exc_info=True,
        )


@router.get(
    "/",
    response_model=SuccessResponse[PaginatedListResponse[StoreResponse]],
    summary="List my stores",
    operation_id="list_stores",
)
async def list_stores(
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """List stores the current user can access.

    Returns stores the user owns outright and stores the user is an active
    tenant member of (e.g. accepted a staff invitation). This is what the
    merchant dashboard uses to decide whether to show the store picker or
    redirect to /create-store.
    """
    use_case = ListStoresUseCase(store_repository=store_repo)

    result = await use_case.accessible_for_user(
        user_id=user_id,
        page=page,
        page_size=limit,
    )

    stores = [_build_store_response(store) for store in result.items]

    return SuccessResponse(
        data=PaginatedListResponse(
            items=stores,
            total=result.total,
            page=page,
            page_size=limit,
            total_pages=(result.total + limit - 1) // limit if limit > 0 else 0,
        ),
        message="Stores retrieved successfully",
    )


@router.get(
    "/{store_id}",
    response_model=SuccessResponse[StoreResponse],
    summary="Get store by ID",
    operation_id="get_store",
)
async def get_store(
    store: Annotated[Store, Depends(verify_store_ownership)],
):
    """Get store details by ID. Only accessible by the store owner."""
    return SuccessResponse(
        data=_build_store_response(store),
        message="Store retrieved successfully",
    )


@router.patch(
    "/{store_id}",
    response_model=SuccessResponse[StoreResponse],
    summary="Update store",
    operation_id="update_store",
)
async def update_store(
    request: UpdateStoreRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
    cache: Annotated[StorefrontCache, Depends(get_storefront_cache_service)],
):
    """Update store details."""
    use_case = UpdateStoreUseCase(
        store_repository=store_repo,
        onboarding_repository=onboarding_repo,
    )

    dto = UpdateStoreDTO(
        name=request.name,
        description=request.description,
        logo_url=request.logo_url,
        banner_url=request.banner_url,
        contact_email=request.contact_email,
        contact_phone=request.contact_phone,
        address=request.address,
        social_links=request.social_links,
        default_language=request.default_language,
        status=request.status,
        settings=request.settings,
        theme_settings=request.theme_settings,
        business_hours=request.business_hours,
        country=request.country,
        default_currency=request.default_currency,
    )

    result = await use_case.execute(
        store_id=store.id,
        dto=dto,
        user_id=store.owner_id,
    )

    await cache.invalidate_store(
        store_id=result.id,
        subdomain=result.subdomain,
        custom_domain=result.custom_domain,
    )
    if request.theme_settings is not None:
        await cache.invalidate_theme(result.id)

    return SuccessResponse(
        data=_build_store_response(result),
        message="Store updated successfully",
    )


@router.delete(
    "/{store_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete store",
    operation_id="delete_store",
)
async def delete_store(
    store: Annotated[Store, Depends(verify_store_ownership)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    cache: Annotated[StorefrontCache, Depends(get_storefront_cache_service)],
):
    """Delete a store."""
    use_case = DeleteStoreUseCase(store_repository=store_repo)

    await use_case.execute(store_id=store.id, user_id=store.owner_id)

    await cache.invalidate_store(
        store_id=store.id,
        subdomain=store.subdomain,
        custom_domain=store.custom_domain,
    )
    await cache.invalidate_theme(store.id)

    return None


# ── Custom domain (Cloudflare for SaaS) ──────────────────────────────────────

_DOMAIN_RE = re.compile(
    r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


def _normalize_custom_domain(raw: str) -> str:
    """Validate + canonicalize a merchant-entered domain. Raises 400 on bad
    input. Strips an accidentally-pasted scheme/path/port so 'https://shop.x/'
    still works."""
    d = raw.strip().lower().rstrip(".")
    d = d.split("//")[-1].split("/")[0].split(":")[0]
    if not _DOMAIN_RE.match(d):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Enter a valid domain like shop.yourbrand.com",
        )
    if d == "numueg.app" or d.endswith(".numueg.app"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That's a NUMU subdomain — use the Subdomain field instead.",
        )
    return d


def _derive_domain_status(cf_state: dict) -> tuple[str, bool, list[str]]:
    """Map Cloudflare's nested status into our lifecycle + error list."""
    ssl = (cf_state.get("ssl_status") or "").lower()
    hostname_status = (cf_state.get("status") or "").lower()
    errors: list[str] = []
    for e in cf_state.get("ssl_validation_errors") or []:
        msg = e.get("message") if isinstance(e, dict) else str(e)
        if msg:
            errors.append(msg)
    for e in cf_state.get("verification_errors") or []:
        errors.append(e if isinstance(e, str) else str(e))

    if ssl == "active":
        return "active", True, []
    if errors:
        return "failed", False, errors
    if hostname_status == "active":
        return "verifying", False, []
    return "pending_dns", False, []


def _build_custom_domain_response(
    store: Store, cf_state: dict | None
) -> CustomDomainStatusResponse:
    cd = (store.settings or {}).get("custom_domain") or {}
    domain = store.custom_domain or cd.get("hostname")
    target = cloudflare_custom_hostname_service.fallback_target

    if not domain:
        return CustomDomainStatusResponse(connected=False, status="none")

    if cf_state is not None:
        lifecycle, is_active, errors = _derive_domain_status(cf_state)
        ssl_status = cf_state.get("ssl_status")
    else:
        lifecycle = cd.get("status", "pending_dns")
        is_active = lifecycle == "active"
        ssl_status = cd.get("ssl_status")
        errors = []

    verification: list[CustomDomainDnsRecord] = []
    ov = (cf_state or {}).get("ownership_verification") or {}
    if ov.get("name") and ov.get("value"):
        verification.append(
            CustomDomainDnsRecord(
                type=(ov.get("type") or "TXT").upper(),
                name=ov["name"],
                value=ov["value"],
            )
        )

    return CustomDomainStatusResponse(
        connected=True,
        domain=domain,
        status=lifecycle,
        ssl_status=ssl_status,
        is_active=is_active,
        cname=CustomDomainDnsRecord(type="CNAME", name=domain, value=target),
        verification=verification,
        errors=errors,
        checked_at=datetime.now(UTC).isoformat(),
    )


def _persist_domain_state(
    store: Store, domain: str | None, cf_state: dict | None
) -> None:
    """Write the custom-domain block into store.settings (reassigning a new
    dict so SQLAlchemy detects the JSONB change)."""
    settings_copy = dict(store.settings or {})
    if domain is None:
        settings_copy.pop("custom_domain", None)
    else:
        lifecycle = "pending_dns"
        if cf_state is not None:
            lifecycle, _, _ = _derive_domain_status(cf_state)
        settings_copy["custom_domain"] = {
            "hostname": domain,
            "cf_id": (cf_state or {}).get("cf_id"),
            "status": lifecycle,
            "ssl_status": (cf_state or {}).get("ssl_status"),
            "cname_target": cloudflare_custom_hostname_service.fallback_target,
            "updated_at": datetime.now(UTC).isoformat(),
        }
    store.settings = settings_copy


@router.get(
    "/{store_id}/custom-domain",
    response_model=SuccessResponse[CustomDomainStatusResponse],
    summary="Get custom domain status",
    operation_id="get_custom_domain",
)
async def get_custom_domain(
    store: Annotated[Store, Depends(verify_store_ownership)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Return the store's custom-domain state, polling Cloudflare for the
    live cert status when one is connected."""
    cd = (store.settings or {}).get("custom_domain") or {}
    cf_state: dict | None = None
    cf_id = cd.get("cf_id")
    if cf_id and cloudflare_custom_hostname_service.is_enabled:
        try:
            cf_state = await cloudflare_custom_hostname_service.get(cf_id)
            # Persist the refreshed lifecycle so the hub has a value even if a
            # later poll can't reach CF.
            prev = cd.get("status")
            _persist_domain_state(store, store.custom_domain, cf_state)
            new = (store.settings or {}).get("custom_domain", {}).get("status")
            if new != prev:
                await store_repo.update(store)
        except CloudflareCustomHostnameError:
            cf_state = None  # fall back to last-known persisted status

    return SuccessResponse(
        data=_build_custom_domain_response(store, cf_state),
        message="Custom domain status retrieved",
    )


@router.post(
    "/{store_id}/custom-domain",
    response_model=SuccessResponse[CustomDomainStatusResponse],
    summary="Connect a custom domain",
    operation_id="connect_custom_domain",
)
async def connect_custom_domain(
    request: ConnectCustomDomainRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    cache: Annotated[StorefrontCache, Depends(get_storefront_cache_service)],
):
    """Register a merchant-owned domain as a Cloudflare custom hostname and
    return the CNAME the merchant must add to go live."""
    if not cloudflare_custom_hostname_service.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Custom domains aren't enabled yet. Contact support.",
        )

    domain = _normalize_custom_domain(request.domain)

    # Dedupe: a domain can only ever route to one store.
    existing = await store_repo.get_by_custom_domain(domain)
    if existing and existing.id != store.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That domain is already connected to another store.",
        )

    try:
        cf_state = await cloudflare_custom_hostname_service.create(domain)
    except CloudflareCustomHostnameError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=e.message
        ) from e

    store.custom_domain = domain
    _persist_domain_state(store, domain, cf_state)
    await store_repo.update(store)
    await cache.invalidate_store(
        store_id=store.id,
        subdomain=store.subdomain,
        custom_domain=domain,
    )

    return SuccessResponse(
        data=_build_custom_domain_response(store, cf_state),
        message="Custom domain connected. Add the CNAME to finish.",
    )


@router.delete(
    "/{store_id}/custom-domain",
    response_model=SuccessResponse[CustomDomainStatusResponse],
    summary="Disconnect the custom domain",
    operation_id="disconnect_custom_domain",
)
async def disconnect_custom_domain(
    store: Annotated[Store, Depends(verify_store_ownership)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    cache: Annotated[StorefrontCache, Depends(get_storefront_cache_service)],
):
    """Remove the custom domain from the store and delete its Cloudflare
    custom hostname (best-effort — local state is always cleared)."""
    prev_domain = store.custom_domain
    cd = (store.settings or {}).get("custom_domain") or {}
    cf_id = cd.get("cf_id")
    if cf_id and cloudflare_custom_hostname_service.is_enabled:
        try:
            await cloudflare_custom_hostname_service.delete(cf_id)
        except CloudflareCustomHostnameError:
            # Don't block disconnect on a CF hiccup; the hostname can be
            # reaped later. Local state is the source of truth for routing.
            pass

    store.custom_domain = None
    _persist_domain_state(store, None, None)
    await store_repo.update(store)
    await cache.invalidate_store(
        store_id=store.id,
        subdomain=store.subdomain,
        custom_domain=prev_domain,
    )

    return SuccessResponse(
        data=CustomDomainStatusResponse(connected=False, status="none"),
        message="Custom domain disconnected",
    )


# ─── Phase 5.11 — demo seed catalog ───────────────────────────────


@router.post(
    "/{store_id}/seed-demo",
    summary="Seed demo catalog (5 products + 1 collection)",
    operation_id="seed_demo_catalog",
)
async def seed_demo_catalog_route(
    store: Annotated[Store, Depends(verify_store_ownership)],
):
    """Phase 5.11 — opt-in demo seed.

    Inserts 5 placeholder products + 1 starter collection so the
    merchant can preview their storefront before uploading their own
    catalog. Idempotent — re-running the seed against a store that
    already has demo rows is a no-op (slug uniqueness covers it).

    The merchant calls this from the hub onboarding flow when they
    pick "Try with demo products" on store creation, OR later via
    Settings → Demo Catalog → "Refresh demo data".
    """
    from src.application.services.demo_seed_service import seed_demo_catalog

    counts = await seed_demo_catalog(
        store_id=store.id,
        tenant_id=store.tenant_id or store.id,
        currency=store.default_currency or Currency.EGP,
    )
    return {"seeded": True, **counts}


@router.delete(
    "/{store_id}/seed-demo",
    summary="Remove demo catalog (bulk delete tagged products)",
    operation_id="remove_demo_catalog",
)
async def remove_demo_catalog_route(
    store: Annotated[Store, Depends(verify_store_ownership)],
):
    """Phase 5.11 — bulk delete demo-tagged products.

    Used by the hub's "Reset demo" / "I'm ready to go live" button
    — merchants who started seeded but want a clean slate before
    launch run this. Doesn't touch products without the `demo` tag,
    so a merchant who edited a seeded product's tags off keeps that
    product (intentional — once they edit it, it's "real").
    """
    from src.application.services.demo_seed_service import remove_demo_catalog

    deleted = await remove_demo_catalog(store_id=store.id)
    return {"deleted": deleted}
