"""Admin platform config for Meta credentials.

URL: /api/v1/admin/platform-config/meta
Requires SUPER_ADMIN role.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
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

META_CONFIG_KEY = "meta_credentials"


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
# Routes
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
        _admin.get("email", "unknown"),
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
