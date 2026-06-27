"""Admin platform settings.

URL: /api/v1/admin/platform-settings
Requires SUPER_ADMIN role.

Stores the platform-wide configuration that the admin dashboard's
Settings > General / Security tabs expose: branding, signup gating,
maintenance mode, session/login policy. Persisted into the existing
`platform_config` key-value table under the `platform_settings` key, so
nothing new schema-wise is needed.

The `get_platform_settings(db)` helper at the bottom of this file is
reused by other parts of the app (maintenance middleware, auth register
gate) so a single source of truth drives both the UI and enforcement.
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.infrastructure.database.models.public.platform_config import (
    PlatformConfigModel,
)

logger = logging.getLogger(__name__)

router = APIRouter()

PLATFORM_SETTINGS_KEY = "platform_settings"

# Defaults used both for new installs and as a fallback when the DB row is
# missing a field (e.g. after adding a new setting). Keep this in lockstep
# with PlatformSettingsResponse below.
DEFAULTS: dict[str, Any] = {
    "platform_name": "NUMU",
    "support_email": "support@numueg.app",
    "default_currency": "USD",
    "enable_new_merchant_signups": True,
    "require_email_verification": True,
    "enable_two_factor_auth": False,
    "maintenance_mode": False,
    "session_timeout_minutes": 60,
    "max_login_attempts": 5,
}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PlatformSettingsResponse(BaseModel):
    platform_name: str
    support_email: str
    default_currency: str
    enable_new_merchant_signups: bool
    require_email_verification: bool
    enable_two_factor_auth: bool
    maintenance_mode: bool
    session_timeout_minutes: int
    max_login_attempts: int


class PlatformSettingsUpdate(BaseModel):
    """Partial update — any omitted field keeps its current value."""

    platform_name: str | None = Field(None, min_length=1, max_length=80)
    support_email: EmailStr | None = None
    default_currency: str | None = Field(None, min_length=3, max_length=3)
    enable_new_merchant_signups: bool | None = None
    require_email_verification: bool | None = None
    enable_two_factor_auth: bool | None = None
    maintenance_mode: bool | None = None
    session_timeout_minutes: int | None = Field(None, ge=5, le=24 * 60)
    max_login_attempts: int | None = Field(None, ge=1, le=100)


# ---------------------------------------------------------------------------
# Helpers (also imported by middleware + register gate)
# ---------------------------------------------------------------------------


async def _get_or_create_settings(db: AsyncSession) -> PlatformConfigModel:
    """Return the platform_settings row, creating it with DEFAULTS if absent.

    Race-safe: two concurrent requests on a fresh DB would each see no row
    and try to INSERT, tripping `platform_config_key_key`. Use
    INSERT ... ON CONFLICT DO NOTHING then re-SELECT, so whichever request
    wins, both end up with the same row.
    """
    result = await db.execute(
        select(PlatformConfigModel).where(
            PlatformConfigModel.key == PLATFORM_SETTINGS_KEY
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        stmt = (
            pg_insert(PlatformConfigModel)
            .values(
                key=PLATFORM_SETTINGS_KEY,
                value=dict(DEFAULTS),
                description="Platform-wide settings (branding, signups, maintenance, auth policy)",
            )
            .on_conflict_do_nothing(index_elements=["key"])
        )
        await db.execute(stmt)
        await db.commit()
        result = await db.execute(
            select(PlatformConfigModel).where(
                PlatformConfigModel.key == PLATFORM_SETTINGS_KEY
            )
        )
        row = result.scalar_one()
    return row


async def get_platform_settings(db: AsyncSession) -> dict[str, Any]:
    """Read the current settings as a plain dict, merged with defaults.

    Missing keys (e.g. after adding a new field to DEFAULTS) fall back to
    the default so callers never see `None`. Non-blocking: on any DB error
    this returns the defaults so the site stays up.
    """
    try:
        row = await _get_or_create_settings(db)
        merged = dict(DEFAULTS)
        if isinstance(row.value, dict):
            merged.update(row.value)
        return merged
    except Exception:
        logger.exception("Failed to load platform_settings — falling back to defaults")
        return dict(DEFAULTS)


def _to_response(value: dict[str, Any]) -> PlatformSettingsResponse:
    merged = dict(DEFAULTS)
    merged.update(value or {})
    return PlatformSettingsResponse(**merged)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=SuccessResponse[PlatformSettingsResponse],
    summary="Get platform settings",
    operation_id="admin_get_platform_settings",
)
async def get_platform_settings_route(
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[Any, Depends(require_admin)],
):
    """Read the current platform-wide settings (super-admin only)."""
    row = await _get_or_create_settings(db)
    return SuccessResponse(
        data=_to_response(row.value or {}),
        message="Platform settings retrieved",
    )


@router.patch(
    "",
    response_model=SuccessResponse[PlatformSettingsResponse],
    summary="Update platform settings",
    operation_id="admin_update_platform_settings",
)
async def update_platform_settings_route(
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[Any, Depends(require_admin)],
    request: PlatformSettingsUpdate,
):
    """Partially update platform settings. Omitted fields are preserved."""
    row = await _get_or_create_settings(db)

    current = dict(DEFAULTS)
    if isinstance(row.value, dict):
        current.update(row.value)

    patch = request.model_dump(exclude_unset=True)
    # EmailStr is a pydantic type — store as string
    if "support_email" in patch and patch["support_email"] is not None:
        patch["support_email"] = str(patch["support_email"])
    if "default_currency" in patch and patch["default_currency"]:
        patch["default_currency"] = str(patch["default_currency"]).upper()

    current.update({k: v for k, v in patch.items() if v is not None})
    row.value = current

    await db.commit()
    await db.refresh(row)

    logger.info(
        "Platform settings updated — fields=%s",
        list(patch.keys()),
    )
    return SuccessResponse(
        data=_to_response(row.value or {}),
        message="Platform settings saved",
    )
