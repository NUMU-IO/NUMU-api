"""Admin platform config.

URLs:
    GET   /api/v1/admin/platform-config              — read top-level config
                                                       (currently: default theme)
    PATCH /api/v1/admin/platform-config              — set/clear default theme
                                                       (Session A 2026-05-27)
    GET   /api/v1/admin/platform-config/meta         — Meta credentials
    POST  /api/v1/admin/platform-config/meta         — set Meta credentials

Requires SUPER_ADMIN role.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.application.services.platform_default_theme_service import (
    PlatformDefaultThemeService,
)
from src.application.services.platform_flags import (
    CHECKOUT_KEY,
    PAYMENTS_KEY,
    get_checkout_platform_config,
    get_payments_config,
)
from src.config.settings import settings as app_settings
from src.core.exceptions import ValidationError as DomainValidationError
from src.infrastructure.database.models.public.platform_config import (
    PlatformConfigModel,
)
from src.infrastructure.repositories.marketplace_repository import (
    MarketplaceRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter()

META_CONFIG_KEY = "meta_credentials"
# Phase 5.2 — theme-engine platform flags (e.g. App-embeds tab visibility).
THEME_ENGINE_KEY = "theme_engine"


async def _get_theme_engine_config(db: AsyncSession) -> dict:
    """Read the theme_engine platform config value (or {} if unset)."""
    result = await db.execute(
        select(PlatformConfigModel).where(PlatformConfigModel.key == THEME_ENGINE_KEY)
    )
    row = result.scalar_one_or_none()
    return row.value if (row and isinstance(row.value, dict)) else {}


async def _set_app_embeds_enabled(db: AsyncSession, enabled: bool) -> None:
    """Upsert theme_engine.app_embeds_tab_enabled (race-safe)."""
    existing = await _get_theme_engine_config(db)
    merged = {**existing, "app_embeds_tab_enabled": bool(enabled)}
    stmt = (
        pg_insert(PlatformConfigModel)
        .values(
            key=THEME_ENGINE_KEY,
            value=merged,
            description="Theme engine platform flags (App-embeds tab, …)",
        )
        .on_conflict_do_update(index_elements=["key"], set_={"value": merged})
    )
    await db.execute(stmt)
    await db.commit()


async def _set_apple_pay_platform_enabled(db: AsyncSession, enabled: bool) -> None:
    """Upsert payments.apple_pay_enabled — the Apple Pay master switch (race-safe)."""
    existing = await get_payments_config(db)
    merged = {**existing, "apple_pay_enabled": bool(enabled)}
    stmt = (
        pg_insert(PlatformConfigModel)
        .values(
            key=PAYMENTS_KEY,
            value=merged,
            description="Payment platform flags (Apple Pay master switch, …)",
        )
        .on_conflict_do_update(index_elements=["key"], set_={"value": merged})
    )
    await db.execute(stmt)
    await db.commit()


async def _set_checkout_identity_enabled(db: AsyncSession, enabled: bool) -> None:
    """Upsert checkout.identity_enabled — the phone-first identity rollout
    gate (race-safe). Once written, this value WINS over the
    CHECKOUT_IDENTITY_ENABLED env default everywhere
    (platform_flags.is_checkout_identity_platform_enabled)."""
    existing = await get_checkout_platform_config(db)
    merged = {**existing, "identity_enabled": bool(enabled)}
    stmt = (
        pg_insert(PlatformConfigModel)
        .values(
            key=CHECKOUT_KEY,
            value=merged,
            description="Checkout platform flags (phone-first identity gate, …)",
        )
        .on_conflict_do_update(index_elements=["key"], set_={"value": merged})
    )
    await db.execute(stmt)
    await db.commit()


def _resolve_checkout_identity_enabled(checkout_cfg: dict) -> bool:
    """Effective gate value for the snapshot: stored flag, else env default."""
    value = checkout_cfg.get("identity_enabled")
    if value is None:
        return app_settings.checkout_identity_enabled
    return bool(value)


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class MetaCredentialsRequest(BaseModel):
    meta_app_id: str
    meta_app_secret: str
    meta_webhook_verify_token: str
    meta_login_config_id: str


class MetaCredentialsResponse(BaseModel):
    meta_app_id: str
    meta_app_secret: str
    meta_webhook_verify_token: str
    meta_login_config_id: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_or_create_config(db: AsyncSession) -> PlatformConfigModel:
    """Get the Meta config row, creating it with defaults if absent.

    Race-safe via INSERT ... ON CONFLICT DO NOTHING + re-SELECT.
    """
    result = await db.execute(
        select(PlatformConfigModel).where(PlatformConfigModel.key == META_CONFIG_KEY)
    )
    config = result.scalar_one_or_none()

    if config is None:
        stmt = (
            pg_insert(PlatformConfigModel)
            .values(
                key=META_CONFIG_KEY,
                value={
                    "meta_app_id": "",
                    "meta_app_secret": "",
                    "meta_webhook_verify_token": "",
                    "meta_login_config_id": "",
                },
                description="Meta (Facebook/Instagram/WhatsApp) API credentials",
            )
            .on_conflict_do_nothing(index_elements=["key"])
        )
        await db.execute(stmt)
        await db.commit()
        result = await db.execute(
            select(PlatformConfigModel).where(
                PlatformConfigModel.key == META_CONFIG_KEY
            )
        )
        config = result.scalar_one()

    return config


# ---------------------------------------------------------------------------
# Platform-wide config (default theme) — Session A 2026-05-27, file 04 §5.1
# ---------------------------------------------------------------------------


class UpdatePlatformConfigPayload(BaseModel):
    """PATCH body for /admin/platform-config.

    Currently exposes a single field — keep PATCH semantics intact so we
    can add more fields later without bumping the URL. Sending the field
    explicitly as ``null`` clears the default; omitting it leaves the
    current value untouched.
    """

    # Allow set-to-null by clients sending `{"default_marketplace_theme_id": null}`.
    # Distinguishing "omitted" from "explicit null" is done via
    # ``model_fields_set`` below.
    default_marketplace_theme_id: UUID | None = None
    # Phase 5.2 — toggle the merchant editor's "App embeds" tab platform-wide.
    # Omitted = leave untouched; explicit bool = set.
    app_embeds_tab_enabled: bool | None = None
    # Apple Pay master switch. Omitted = leave untouched; explicit bool = set.
    apple_pay_enabled: bool | None = None
    # Phone-first checkout identity rollout gate (WhatsApp OTP at checkout +
    # save-cart nudge). Omitted = leave untouched; explicit bool = set.
    # NOTE: flipping this ON turns the gate on for every GOWA-transport
    # store whose merchant hasn't opted out (require_verification defaults
    # true) — the admin UI carries the same warning.
    checkout_identity_enabled: bool | None = None


class PlatformConfigSnapshot(BaseModel):
    """GET /admin/platform-config response.

    Returned both as the read body and as the echo on PATCH so clients
    can refresh their cached state in one round-trip.
    """

    default_marketplace_theme_id: UUID | None = None
    default_marketplace_theme: dict[str, str | None] | None = None
    # Phase 5.2 — App-embeds tab visibility (default False → hidden).
    app_embeds_tab_enabled: bool = False
    # Apple Pay master switch (default True → available; admin can disable).
    apple_pay_enabled: bool = True
    # Phone-first checkout identity gate (default off; env is the
    # unset-default, the stored flag wins once set).
    checkout_identity_enabled: bool = False


@router.get(
    "",
    response_model=SuccessResponse[PlatformConfigSnapshot],
    summary="Get top-level platform config",
    operation_id="admin_get_platform_config",
)
async def get_platform_config(
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[dict, Depends(require_admin)],
) -> SuccessResponse[PlatformConfigSnapshot]:
    """Return the platform-wide config snapshot (default theme today).

    Adds the resolved theme summary alongside the raw UUID so the admin
    UI doesn't need an extra round-trip to render the chosen-theme card.
    """
    marketplace_repo = MarketplaceRepository(db)
    svc = PlatformDefaultThemeService(db, marketplace_repo)

    default_id = await svc.get_default_theme_id()
    summary = await svc.get_default_theme_summary() if default_id else None
    theme_engine = await _get_theme_engine_config(db)
    payments = await get_payments_config(db)
    checkout_cfg = await get_checkout_platform_config(db)

    return SuccessResponse(
        data=PlatformConfigSnapshot(
            default_marketplace_theme_id=default_id,
            default_marketplace_theme=summary,
            app_embeds_tab_enabled=bool(
                theme_engine.get("app_embeds_tab_enabled", False)
            ),
            apple_pay_enabled=bool(payments.get("apple_pay_enabled", True)),
            checkout_identity_enabled=_resolve_checkout_identity_enabled(checkout_cfg),
        ),
        message="Platform config retrieved",
    )


@router.patch(
    "",
    response_model=SuccessResponse[PlatformConfigSnapshot],
    summary="Update top-level platform config",
    operation_id="admin_update_platform_config",
)
async def update_platform_config(
    payload: UpdatePlatformConfigPayload,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin: Annotated[dict, Depends(require_admin)],
) -> SuccessResponse[PlatformConfigSnapshot]:
    """Patch platform-wide config. Currently supports clearing/setting
    the platform default theme.

    Sending ``{"default_marketplace_theme_id": "<uuid>"}`` requires the
    theme to be published + installable; the service raises
    :class:`ValidationError` which is mapped to HTTP 400 here.

    Sending ``{"default_marketplace_theme_id": null}`` clears the default
    (new stores then fall through to legacy V2 fallback per file 08).

    Omitting the field entirely leaves the current value untouched.
    """
    marketplace_repo = MarketplaceRepository(db)
    svc = PlatformDefaultThemeService(db, marketplace_repo)

    fields_set = payload.model_fields_set
    if "default_marketplace_theme_id" in fields_set:
        try:
            await svc.update_default_theme(payload.default_marketplace_theme_id)
        except DomainValidationError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        logger.info(
            "platform_default_theme_updated",
            extra={
                "admin_id": str(admin),
                "new_value": (
                    str(payload.default_marketplace_theme_id)
                    if payload.default_marketplace_theme_id
                    else None
                ),
            },
        )

    if "app_embeds_tab_enabled" in fields_set:
        await _set_app_embeds_enabled(db, bool(payload.app_embeds_tab_enabled))
        logger.info(
            "platform_app_embeds_tab_toggled",
            extra={
                "admin_id": str(admin),
                "new_value": bool(payload.app_embeds_tab_enabled),
            },
        )

    if "apple_pay_enabled" in fields_set:
        await _set_apple_pay_platform_enabled(db, bool(payload.apple_pay_enabled))
        logger.info(
            "platform_apple_pay_toggled",
            extra={
                "admin_id": str(admin),
                "new_value": bool(payload.apple_pay_enabled),
            },
        )

    if "checkout_identity_enabled" in fields_set:
        await _set_checkout_identity_enabled(
            db, bool(payload.checkout_identity_enabled)
        )
        logger.info(
            "platform_checkout_identity_toggled",
            extra={
                "admin_id": str(admin),
                "new_value": bool(payload.checkout_identity_enabled),
            },
        )

    default_id = await svc.get_default_theme_id()
    summary = await svc.get_default_theme_summary() if default_id else None
    theme_engine = await _get_theme_engine_config(db)
    payments = await get_payments_config(db)
    checkout_cfg = await get_checkout_platform_config(db)

    return SuccessResponse(
        data=PlatformConfigSnapshot(
            default_marketplace_theme_id=default_id,
            default_marketplace_theme=summary,
            app_embeds_tab_enabled=bool(
                theme_engine.get("app_embeds_tab_enabled", False)
            ),
            apple_pay_enabled=bool(payments.get("apple_pay_enabled", True)),
            checkout_identity_enabled=_resolve_checkout_identity_enabled(checkout_cfg),
        ),
        message="Platform config updated",
    )


# ---------------------------------------------------------------------------
# Meta credentials routes (legacy — preserved as-is)
# ---------------------------------------------------------------------------


@router.get(
    "/meta",
    response_model=SuccessResponse[MetaCredentialsResponse],
    summary="Get Meta credentials",
    description="Get stored Meta app credentials (secrets redacted)",
)
async def get_meta_credentials(
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[dict, Depends(require_admin)],
) -> SuccessResponse[MetaCredentialsResponse]:
    """Get the current Meta credentials configuration."""
    config = await _get_or_create_config(db)

    # Redact secrets for response
    response = MetaCredentialsResponse(
        meta_app_id=config.value.get("meta_app_id", ""),
        meta_app_secret="****" if config.value.get("meta_app_secret") else "",
        meta_webhook_verify_token="****"
        if config.value.get("meta_webhook_verify_token")
        else "",
        meta_login_config_id=config.value.get("meta_login_config_id", ""),
    )

    return SuccessResponse(data=response)


@router.post(
    "/meta",
    response_model=SuccessResponse[MetaCredentialsResponse],
    summary="Update Meta credentials",
    description="Update Meta app credentials for platform-wide OAuth/webhook configuration",
)
async def update_meta_credentials(
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[dict, Depends(require_admin)],
    request: MetaCredentialsRequest,
) -> SuccessResponse[MetaCredentialsResponse]:
    """Update the Meta credentials configuration."""
    config = await _get_or_create_config(db)

    # Update values (allow clearing by sending empty string)
    config.value = {
        "meta_app_id": request.meta_app_id,
        "meta_app_secret": request.meta_app_secret,
        "meta_webhook_verify_token": request.meta_webhook_verify_token,
        "meta_login_config_id": request.meta_login_config_id,
    }

    await db.commit()
    await db.refresh(config)

    logger.info(
        "Meta credentials updated by admin %s",
        str(_admin),
    )

    # Redact secrets in response
    response = MetaCredentialsResponse(
        meta_app_id=config.value.get("meta_app_id", ""),
        meta_app_secret="****" if config.value.get("meta_app_secret") else "",
        meta_webhook_verify_token="****"
        if config.value.get("meta_webhook_verify_token")
        else "",
        meta_login_config_id=config.value.get("meta_login_config_id", ""),
    )

    return SuccessResponse(data=response)
