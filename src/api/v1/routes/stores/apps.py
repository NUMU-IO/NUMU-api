"""Merchant app marketplace routes — Phase 6.

Mounted at /stores/{store_id}/apps/

Endpoints:
  GET    /catalog              — list all published apps the merchant
                                 can install (global registry).
  GET    /                     — list this store's installs.
  POST   /{slug}/install       — install + activate an app on this store.
  PUT    /{slug}/settings      — update per-store settings.
  POST   /{slug}/disable       — soft-disable (keeps settings).
  POST   /{slug}/enable        — re-enable.
  DELETE /{slug}               — uninstall (deletes settings).

Apps themselves are managed by their developers in the marketplace
admin (out of scope for v1 — admins seed via SQL/console). Merchants
only ever see *published* apps in the catalog.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from urllib.parse import urlencode, urljoin, urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import false, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.api.dependencies import get_current_user_id, verify_store_ownership
from src.api.dependencies.feature_flags import _read_feature_flags, is_flag_enabled
from src.api.dependencies.repositories import get_store_repository
from src.api.responses import SuccessResponse
from src.application.services.app_manifest import validate_settings
from src.application.services.numu_apps import (
    FLAG,
    NUMU_APPS,
    cancel_purge,
    schedule_purge,
)
from src.application.services.partner_program import partner_apps_enabled
from src.core.entities.app import AppStatus
from src.core.entities.store import Store
from src.core.logging import get_logger
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppModel,
    AppOAuthClientModel,
    AppUninstallEventModel,
)
from src.infrastructure.repositories import StoreRepository

logger = get_logger(__name__)

router = APIRouter(
    prefix="/{store_id}/apps",
    tags=["Apps"],
    dependencies=[Depends(verify_store_ownership)],
)


# ─── Schemas ───────────────────────────────────────────────────────

UninstallReason = Literal[
    "not_needed",
    "missing_features",
    "too_expensive",
    "bugs",
    "hard_to_use",
    "poor_support",
    "switched_app",
    "temporary",
    "other",
]


class AppListing(BaseModel):
    """Everything the merchant-facing app page shows about an app.

    Read straight off the manifest, so a THIRD-PARTY app gets the same detail
    page as a first-party one by filling the same keys — the hub renders what
    is present and omits what is not, rather than special-casing any app.
    """

    tagline: str | None = None
    developer: dict[str, Any] | None = None
    lockup_url: str | None = None
    screenshots: list[dict[str, Any]] = []
    highlights: list[dict[str, Any]] = []
    # `locales[lang].tagline` and `app_locales[lang].{name,description}` — the
    # hub prefers these over the English columns so an Arabic-first merchant
    # reads the app in Arabic.
    locales: dict[str, Any] = {}
    app_locales: dict[str, Any] = {}
    # The fuller listing a merchant reads before installing: `highlights` is the
    # four-line pitch, `features` the tour with a body per entry.
    features: list[dict[str, Any]] = []
    pricing: dict[str, Any] | None = None
    languages: list[str] = []
    compatibility: dict[str, Any] | None = None
    #: Partner App opted in to render inside the hub (``/apps/<slug>/app``).
    embedded: bool = False


def _listing(manifest: dict | None) -> AppListing:
    m = manifest or {}
    return AppListing(
        tagline=m.get("tagline"),
        developer=m.get("developer") if isinstance(m.get("developer"), dict) else None,
        lockup_url=m.get("lockup_url"),
        screenshots=[x for x in (m.get("screenshots") or []) if isinstance(x, dict)],
        highlights=[x for x in (m.get("highlights") or []) if isinstance(x, dict)],
        locales=m.get("locales") if isinstance(m.get("locales"), dict) else {},
        app_locales=m.get("app_locales")
        if isinstance(m.get("app_locales"), dict)
        else {},
        features=[x for x in (m.get("features") or []) if isinstance(x, dict)],
        pricing=m.get("pricing") if isinstance(m.get("pricing"), dict) else None,
        languages=[x for x in (m.get("languages") or []) if isinstance(x, str)],
        compatibility=(
            m.get("compatibility") if isinstance(m.get("compatibility"), dict) else None
        ),
        embedded=bool((m.get("app") or {}).get("embedded")),
    )


class AppCatalogEntry(BaseModel):
    slug: str
    name: str
    description: str | None = None
    icon_url: str | None = None
    version: str
    blocks: list[dict[str, Any]] = []
    listing: AppListing = AppListing()
    #: Partner Apps install through consent: what the hub needs to open it
    #: (``client_id``, the first registered ``redirect_uri``, the scopes).
    connect: dict[str, Any] | None = None


class AppInstallation(BaseModel):
    slug: str
    name: str
    description: str | None = None
    icon_url: str | None = None
    version: str
    is_enabled: bool
    settings: dict[str, Any]
    blocks: list[dict[str, Any]] = []
    # The app's PLATFORM status, which is not the same thing as `is_enabled`.
    #
    # The storefront serves only `PUBLISHED` apps; this list used to ignore
    # status entirely, so a suspended app kept showing here as installed and
    # enabled while shoppers saw nothing — a silent desync with no signal to
    # the merchant. Surfaced rather than filtered out: an app that vanishes
    # from the list reads as "my settings are gone", which is a worse lie than
    # the one being fixed.
    # The app's own settings form, straight off the manifest. The merchant
    # endpoint is authenticated and store-scoped, so unlike the storefront
    # projection this may carry the whole schema — the hub needs it to render
    # the form at all, and a schema the hub cannot read is an app the merchant
    # cannot configure.
    settings_schema: list[dict[str, Any]] = []
    #: Partner Apps: what the merchant consented to, and whether the app has
    #: finished connecting (``pending_auth`` until it exchanges its code).
    granted_scopes: list[str] = []
    install_status: str = "active"
    #: Scopes the live version asks for that this install has not granted:
    #: the hub shows a re-consent banner when non-empty.
    missing_scopes: list[str] = []
    listing: AppListing = AppListing()
    app_status: str = AppStatus.PUBLISHED.value
    # False when the app is installed and enabled but the PLATFORM has
    # suspended it, so the hub can say so plainly.
    is_live: bool = True


class UpdateSettingsRequest(BaseModel):
    settings: dict[str, Any]
    # Merge is the default because a replace silently loses concurrent writes.
    # An explicit replace is still the only way to REMOVE a key, so it stays
    # available — just never by accident.
    replace: bool = False


async def _revalidate_app_settings(store: Store | None) -> None:
    """Push an app-settings change through both caches.

    App settings ride the store payload. That payload is cached by the API in
    Redis (`StorefrontCache.DEFAULT_TTL`, 60s) AND again by the storefront's
    fetch cache under `store-{subdomain}`, so busting only one leaves shoppers
    on the old settings. Best-effort on both counts: a cache miss must never
    fail the write the merchant just made.
    """
    if store is None:
        return
    try:
        # The process-wide singleton, NOT `StorefrontCache()` — the constructor
        # takes a required redis client, so building one here raised TypeError
        # that the except below swallowed, and the API-side bust silently never
        # ran. Local QA is what caught it; the broad except is exactly what hid
        # it, so the call is now the same accessor every read path uses.
        from src.infrastructure.cache.storefront_cache import get_storefront_cache

        await get_storefront_cache().invalidate_store(
            store_id=store.id,
            subdomain=store.subdomain,
            custom_domain=getattr(store, "custom_domain", None),
        )
    except Exception:  # noqa: BLE001 — best-effort
        pass
    if not store.subdomain:
        return
    try:
        from src.infrastructure.external_services.nextjs_revalidation import (
            revalidate_store,
            store_cache_tags,
        )

        await revalidate_store(
            subdomain=store.subdomain,
            tags=store_cache_tags(
                store.subdomain, getattr(store, "custom_domain", None)
            ),
        )
    except Exception:  # noqa: BLE001 — best-effort
        pass


def _installation(app: AppModel, install: AppInstallationModel) -> AppInstallation:
    """One install, with the platform status the hub needs to tell the truth."""
    status_value = getattr(app.status, "value", app.status)
    return AppInstallation(
        slug=app.slug,
        name=app.name,
        description=app.description,
        icon_url=app.icon_url,
        version=app.version,
        is_enabled=install.is_enabled,
        settings=install.settings or {},
        blocks=(app.manifest or {}).get("blocks", []) or [],
        settings_schema=(app.manifest or {}).get("settings_schema", []) or [],
        listing=_listing(app.manifest),
        app_status=status_value,
        is_live=bool(install.is_enabled)
        and status_value != AppStatus.SUSPENDED.value
        # Mid-consent installs are skipped by the storefront too.
        and (install.status or "active") == "active",
        granted_scopes=list(install.granted_scopes or []),
        install_status=install.status or "active",
        missing_scopes=sorted(
            set(
                (((app.manifest or {}).get("app") or {}).get("oauth") or {}).get(
                    "scopes"
                )
                or []
            )
            - set(install.granted_scopes or [])
        )
        # Only a live install can be missing scopes; one mid-consent has
        # granted nothing yet, and that is not a re-consent.
        if app.developer_id is not None and install.status == "active"
        else [],
    )


async def _hidden_slugs(session, store_id: UUID) -> frozenset[str]:
    """NUMU Apps stay out of every list until the tenant has ff_numu_apps.

    The migration installed them on stores that already used WhatsApp or the
    Inbox; without this, those stores would see them appear before the switch.
    """
    flags = await _read_feature_flags(session, store_id=store_id)
    return frozenset() if is_flag_enabled(flags, FLAG) else NUMU_APPS


# ─── Catalog ───────────────────────────────────────────────────────


@router.get(
    "/catalog",
    response_model=SuccessResponse[list[AppCatalogEntry]],
    summary="List installable apps",
    operation_id="list_app_catalog",
)
async def list_catalog(store_id: UUID):
    async with AsyncSessionLocal() as session:
        hidden = await _hidden_slugs(session, store_id)
        # NUMU Apps (no developer) are always listed once published. A
        # Partner App also needs an admin's catalog_visible, and the
        # Partner-apps kill switch on.
        partner_listed = (
            AppModel.listing_flags["catalog_visible"].as_boolean().is_(True)
        )
        if not await partner_apps_enabled(session):
            partner_listed = false()
        stmt = select(AppModel).where(
            AppModel.status == AppStatus.PUBLISHED,
            AppModel.slug.notin_(hidden),
            AppModel.private_store_id.is_(None),
            or_(AppModel.developer_id.is_(None), partner_listed),
        )
        rows = (await session.execute(stmt)).scalars().all()
        client_ids = dict(
            (
                await session.execute(
                    select(AppOAuthClientModel.app_id, AppOAuthClientModel.client_id)
                )
            ).all()
        )

    def connect(a: AppModel) -> dict[str, Any] | None:
        oauth = ((a.manifest or {}).get("app") or {}).get("oauth") or {}
        if a.developer_id is None or a.id not in client_ids or not oauth:
            return None
        return {
            "client_id": client_ids[a.id],
            "redirect_uri": (oauth.get("redirect_urls") or [None])[0],
            "scopes": oauth.get("scopes") or [],
        }

    return SuccessResponse(
        data=[
            AppCatalogEntry(
                slug=a.slug,
                name=a.name,
                description=a.description,
                icon_url=a.icon_url,
                version=a.version,
                blocks=(a.manifest or {}).get("blocks", []) or [],
                listing=_listing(a.manifest),
                connect=connect(a),
            )
            for a in rows
        ],
        message="Catalog listed",
    )


# ─── Installations ─────────────────────────────────────────────────


@router.get(
    "",
    response_model=SuccessResponse[list[AppInstallation]],
    summary="List installs for this store",
    operation_id="list_app_installations",
)
async def list_installations(store_id: UUID):
    async with AsyncSessionLocal() as session:
        hidden = await _hidden_slugs(session, store_id)
        stmt = (
            select(AppModel, AppInstallationModel)
            .join(AppInstallationModel, AppModel.id == AppInstallationModel.app_id)
            .where(
                AppInstallationModel.store_id == store_id,
                AppModel.slug.notin_(hidden),
            )
        )
        rows = (await session.execute(stmt)).all()
    return SuccessResponse(
        data=[_installation(app, install) for app, install in rows],
        message="Installations listed",
    )


@router.post(
    "/{slug}/install",
    response_model=SuccessResponse[AppInstallation],
    status_code=status.HTTP_201_CREATED,
    summary="Install an app",
    operation_id="install_app",
)
async def install_app(
    store_id: UUID,
    slug: str,
    store_repo: StoreRepository = Depends(get_store_repository),
):
    """Idempotent: re-installing an already-installed app simply
    re-enables it (keeps existing settings)."""

    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Store not found"
        )
    tenant_id = store.tenant_id

    async with AsyncSessionLocal() as session:
        app = (
            await session.execute(
                select(AppModel).where(
                    AppModel.slug == slug, AppModel.status == AppStatus.PUBLISHED
                )
            )
        ).scalar_one_or_none()
        if not app:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="App not found in catalog.",
            )
        if app.developer_id is not None:
            # A Partner App installs through consent (OAuth), which is where
            # the merchant sees and grants its scopes.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "consent_required",
                    "message": "Install this app through its consent screen.",
                },
            )

        # ON CONFLICT: re-enable the existing row, don't blow away
        # settings the merchant configured before disabling.
        stmt = (
            pg_insert(AppInstallationModel)
            .values(
                tenant_id=tenant_id,
                store_id=store_id,
                app_id=app.id,
                is_enabled=True,
                settings={},
            )
            .on_conflict_do_update(
                constraint="uq_app_installation_store_app",
                set_={"is_enabled": True},
            )
        )
        await session.execute(stmt)
        # Back inside the retention window: the data stays.
        await cancel_purge(session, store_id, app.id)
        await session.commit()

        install = (
            await session.execute(
                select(AppInstallationModel).where(
                    AppInstallationModel.store_id == store_id,
                    AppInstallationModel.app_id == app.id,
                )
            )
        ).scalar_one()
        result = _installation(app, install)

    # Installing changes what the store payload's installed_apps carries.
    await _revalidate_app_settings(store)
    return SuccessResponse(data=result, message="App installed")


@router.put(
    "/{slug}/settings",
    response_model=SuccessResponse[AppInstallation],
    summary="Update app settings",
    operation_id="update_app_settings",
)
async def update_settings(
    store_id: UUID,
    slug: str,
    body: UpdateSettingsRequest,
    store: Annotated[Store, Depends(verify_store_ownership)] = None,  # type: ignore[assignment]
):
    """Update per-store app settings.

    MERGES by default. A full replace loses writes whenever two tabs, or a
    settings form and an onboarding step, save overlapping keys — the second
    save silently drops whatever the first added, with no ETag and no version
    history to notice it. Send `replace: true` to deliberately overwrite the
    whole blob (the only honest way to REMOVE a key).

    Busts both caches on the way out. Doing neither is what this endpoint did
    before: app settings ride the store payload, which the API caches in Redis
    for 60s and the storefront caches again under `store-{subdomain}`, so a
    merchant changing a swatch shape saw nothing change and tried again.
    """
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(AppModel, AppInstallationModel)
                .join(AppInstallationModel, AppModel.id == AppInstallationModel.app_id)
                .where(AppInstallationModel.store_id == store_id, AppModel.slug == slug)
            )
        ).one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Install not found"
            )
        app, install = row
        incoming = body.settings or {}
        merged = incoming if body.replace else {**(install.settings or {}), **incoming}
        errors = validate_settings(
            merged,
            (app.manifest or {}).get("settings_schema") or [],
            # Partner Apps get an exact contract; NUMU Apps keep legacy keys.
            strict_keys=app.developer_id is not None,
        )
        if errors:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "invalid_settings", "fields": errors},
            )
        install.settings = merged
        await session.commit()
        await session.refresh(install)
        result = _installation(app, install)

    await _revalidate_app_settings(store)
    return SuccessResponse(data=result, message="Settings updated")


@router.post(
    "/{slug}/disable",
    response_model=SuccessResponse[AppInstallation],
    summary="Disable app",
    operation_id="disable_app",
)
async def disable_app(
    store_id: UUID,
    slug: str,
    store: Annotated[Store, Depends(verify_store_ownership)] = None,  # type: ignore[assignment]
):
    return await _set_enabled(store, slug, enabled=False)


@router.post(
    "/{slug}/enable",
    response_model=SuccessResponse[AppInstallation],
    summary="Enable app",
    operation_id="enable_app",
)
async def enable_app(
    store_id: UUID,
    slug: str,
    store: Annotated[Store, Depends(verify_store_ownership)] = None,  # type: ignore[assignment]
):
    return await _set_enabled(store, slug, enabled=True)


@router.delete(
    "/{slug}",
    response_model=SuccessResponse[dict[str, str]],
    summary="Uninstall app",
    operation_id="uninstall_app",
)
async def uninstall_app(
    store_id: UUID,
    slug: str,
    store: Annotated[Store, Depends(verify_store_ownership)] = None,  # type: ignore[assignment]
    reason: UninstallReason | None = None,
    reason_text: Annotated[str | None, Query(max_length=500)] = None,
):
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(AppInstallationModel)
                .join(AppModel, AppModel.id == AppInstallationModel.app_id)
                .where(AppInstallationModel.store_id == store_id, AppModel.slug == slug)
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Install not found"
            )
        app = await session.get(AppModel, row.app_id)
        if app is not None and app.developer_id is not None:
            # Partner App: its tokens and subscriptions go with the row
            # (FK cascade); tell the app now, and ask it to delete the
            # store's data in 48 hours unless the merchant reinstalls.
            from src.application.services.app_webhooks import (
                REDACT_DELAY_SECONDS,
                deliver_app_event,
            )

            await deliver_app_event(session, app, store_id, "app.uninstalled", {})
            session.add(
                AppUninstallEventModel(
                    app_id=app.id,
                    store_id=store_id,
                    installed_at=row.created_at,
                    reason=reason,
                    reason_text=(reason_text or "").strip() or None,
                )
            )
            try:
                from src.infrastructure.messaging.tasks.app_redact_task import (
                    app_store_redact_task,
                )

                app_store_redact_task.apply_async(
                    args=[str(app.id), str(store_id)], countdown=REDACT_DELAY_SECONDS
                )
            except Exception:  # noqa: BLE001 — a broker outage must not block uninstall
                logger.warning("app_store_redact_schedule_failed", app=app.slug)
        await session.delete(row)
        if slug in NUMU_APPS:
            # Their data lives outside the install row; keep it for the
            # retention window so a reinstall brings it back.
            await schedule_purge(session, store_id, row.app_id)
        await session.commit()
    await _revalidate_app_settings(store)
    return SuccessResponse(data={"slug": slug}, message="App uninstalled")


async def _set_enabled(
    store: Store, slug: str, *, enabled: bool
) -> SuccessResponse[AppInstallation]:
    store_id = store.id
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(AppModel, AppInstallationModel)
                .join(AppInstallationModel, AppModel.id == AppInstallationModel.app_id)
                .where(AppInstallationModel.store_id == store_id, AppModel.slug == slug)
            )
        ).one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Install not found"
            )
        app, install = row
        install.is_enabled = enabled
        await session.commit()
        await session.refresh(install)
        result = _installation(app, install)

    # A disabled app drops out of the store payload's installed_apps.
    await _revalidate_app_settings(store)
    return SuccessResponse(
        data=result, message="App enabled" if enabled else "App disabled"
    )


@router.get(
    "/{slug}/open-url",
    response_model=SuccessResponse[dict[str, str]],
    summary="Signed link to a Partner App's own admin",
    operation_id="app_open_url",
)
async def open_url(store_id: UUID, slug: str, locale: str = "ar"):
    """``app_url?store_id&locale&timestamp&hmac``, signed with the app's
    client secret (plan 03 § 6.3). It proves the merchant came from the hub;
    it is not a login, and the app must reject a timestamp older than 5 min."""
    from src.application.services.app_tokens import read_client_secret, signed_params

    async with AsyncSessionLocal() as session:
        app = (
            await session.execute(
                select(AppModel)
                .join(AppInstallationModel, AppModel.id == AppInstallationModel.app_id)
                .where(AppInstallationModel.store_id == store_id, AppModel.slug == slug)
            )
        ).scalar_one_or_none()
        app_url = (
            (((app.manifest or {}).get("app") or {}).get("app_url")) if app else None
        )
        if not app_url:
            raise HTTPException(status_code=404, detail="Install not found")
        secret = await read_client_secret(session, app.id)
    if not secret:
        raise HTTPException(
            status_code=409, detail="This app must rotate its client secret."
        )
    params = signed_params(
        {"store_id": str(store_id), "locale": "en" if locale == "en" else "ar"}, secret
    )
    from urllib.parse import urlencode

    sep = "&" if "?" in app_url else "?"
    return SuccessResponse(data={"url": f"{app_url}{sep}{urlencode(params)}"})


@router.post(
    "/{slug}/session-token",
    response_model=SuccessResponse[dict[str, str]],
    summary="Session token for an embedded Partner App",
    operation_id="app_session_token",
)
async def app_session_token(
    store_id: UUID,
    slug: str,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    locale: str = "ar",
):
    """A 60-second HS256 JWT signed with the app's client secret, and the URL
    the hub frames (``app_url`` + ``embedded_path`` + ``?session_token``). The
    hub asks again whenever the framed app posts ``numu:session-token``."""
    from src.application.services.app_tokens import read_client_secret, session_token

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(AppModel, AppOAuthClientModel.client_id)
                .join(AppInstallationModel, AppModel.id == AppInstallationModel.app_id)
                .join(AppOAuthClientModel, AppOAuthClientModel.app_id == AppModel.id)
                .where(
                    AppInstallationModel.store_id == store_id,
                    AppInstallationModel.is_enabled.is_(True),
                    AppInstallationModel.status == "active",
                    AppModel.slug == slug,
                    AppModel.status == AppStatus.PUBLISHED,
                )
            )
        ).one_or_none()
        contract = ((row[0].manifest or {}).get("app") or {}) if row else {}
        if not contract.get("embedded") or not await partner_apps_enabled(session):
            raise HTTPException(status_code=404, detail="Embedded app not found")
        app, client_id = row
        secret = await read_client_secret(session, app.id)
    if not secret:
        raise HTTPException(
            status_code=409, detail="This app must rotate its client secret."
        )
    lang = "en" if locale == "en" else "ar"
    token = session_token(
        secret, client_id=client_id, user_id=user_id, store_id=store_id, locale=lang
    )
    base = urljoin(contract["app_url"], contract.get("embedded_path") or "")
    query = urlencode({
        "store_id": str(store_id),
        "locale": lang,
        "session_token": token,
    })
    parts = urlsplit(contract["app_url"])
    return SuccessResponse(
        data={
            "token": token,
            "url": f"{base}{'&' if '?' in base else '?'}{query}",
            "origin": f"{parts.scheme}://{parts.netloc}",
        }
    )


# ─── Paid apps: the store's subscription (apps plan, Phase 7) ─────


async def _install_for(session, store_id: UUID, slug: str):
    row = (
        await session.execute(
            select(AppInstallationModel, AppModel)
            .join(AppModel, AppModel.id == AppInstallationModel.app_id)
            .where(AppInstallationModel.store_id == store_id, AppModel.slug == slug)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Install not found")
    return row


@router.get(
    "/{slug}/subscription",
    response_model=SuccessResponse[dict[str, Any]],
    summary="The store's subscription to a paid app",
    operation_id="get_app_subscription",
)
async def get_subscription(store_id: UUID, slug: str):
    from src.application.services.app_billing import (
        subscription_for,
        subscription_view,
    )

    async with AsyncSessionLocal() as session:
        install, app = await _install_for(session, store_id, slug)
        sub = await subscription_for(session, install.id)
        return SuccessResponse(
            data=await subscription_view(session, sub, app, store_id)
        )


@router.post(
    "/{slug}/subscription",
    response_model=SuccessResponse[dict[str, Any]],
    summary="Pay for a paid app from the store's wallet",
    operation_id="subscribe_app",
)
async def subscribe_app(store_id: UUID, slug: str):
    """Charges one period to the NUMU wallet, now, or starts the app's free
    trial on the store's first subscription. Already covered: nothing is
    charged (and a pending cancellation is withdrawn). 402 when the wallet
    can't cover it; top it up (InstaPay, Vodafone Cash or card) and retry."""
    from src.application.services.app_billing import (
        InsufficientFundsError,
        NotPaidError,
        WalletChargeSource,
        started_notice,
        subscribe,
        subscription_view,
    )
    from src.application.services.notification_feed import (
        emit_notification_standalone,
    )
    from src.application.services.wallet_service import WalletSuspendedError

    async with AsyncSessionLocal() as session:
        install, app = await _install_for(session, store_id, slug)
        if not install.is_enabled or install.status != "active":
            raise HTTPException(
                status_code=409, detail="Finish installing or enable the app first."
            )
        if app.status != AppStatus.PUBLISHED:
            raise HTTPException(status_code=409, detail="This app is not available.")
        source = WalletChargeSource(session)
        try:
            sub, started = await subscribe(
                session, installation=install, app=app, source=source
            )
        except NotPaidError:
            raise HTTPException(status_code=409, detail="This app is free.") from None
        except InsufficientFundsError as exc:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "code": "insufficient_wallet_balance",
                    "needed_cents": exc.needed_cents,
                    "balance_cents": exc.balance_cents,
                },
            ) from None
        except WalletSuspendedError:
            raise HTTPException(
                status_code=409, detail="The store's wallet is suspended."
            ) from None
        await session.commit()
        await source.invalidate()
        logger.info("app_subscribe", app=slug, store_id=str(store_id), started=started)
        if started:
            await emit_notification_standalone(**started_notice(sub, app))
        return SuccessResponse(
            data=await subscription_view(session, sub, app, store_id),
            message="Subscribed" if started else "Already active",
        )


@router.delete(
    "/{slug}/subscription",
    response_model=SuccessResponse[dict[str, Any]],
    summary="Cancel a paid app at the end of the paid period",
    operation_id="cancel_app_subscription",
)
async def cancel_subscription(store_id: UUID, slug: str):
    from src.application.services.app_billing import cancel, subscription_view

    async with AsyncSessionLocal() as session:
        install, app = await _install_for(session, store_id, slug)
        sub = await cancel(session, install.id)
        await session.commit()
        return SuccessResponse(
            data=await subscription_view(session, sub, app, store_id)
        )
