"""Admin landing page configuration endpoints.

URL: /api/v1/admin/landing-config
Requires SUPER_ADMIN role.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.infrastructure.database.models.public.platform_config import (
    DEFAULT_LANDING_CONFIG,
    PlatformConfigModel,
)

logger = logging.getLogger(__name__)

router = APIRouter()

LANDING_PAGE_KEY = "landing_page"


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class SectionConfig(BaseModel):
    visible: bool
    order: int


class UpdateLandingConfigRequest(BaseModel):
    sections: dict[str, SectionConfig]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_or_create_config(db: AsyncSession) -> PlatformConfigModel:
    """Get the landing page config row, creating it with defaults if absent."""
    result = await db.execute(
        select(PlatformConfigModel).where(PlatformConfigModel.key == LANDING_PAGE_KEY)
    )
    config = result.scalar_one_or_none()

    if config is None:
        config = PlatformConfigModel(
            key=LANDING_PAGE_KEY,
            value=DEFAULT_LANDING_CONFIG,
            description="Landing page section visibility and ordering",
        )
        db.add(config)
        await db.flush()

    return config


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/",
    response_model=SuccessResponse[dict],
    summary="Get landing page config (admin)",
    operation_id="admin_get_landing_config",
)
async def get_landing_config(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return the current landing page section configuration."""
    config = await _get_or_create_config(db)
    return SuccessResponse(
        data=config.value,
        message="Landing page configuration",
    )


@router.put(
    "/",
    response_model=SuccessResponse[dict],
    summary="Update landing page config (admin)",
    operation_id="admin_update_landing_config",
)
async def update_landing_config(
    request: UpdateLandingConfigRequest,
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update landing page section visibility and ordering."""
    config = await _get_or_create_config(db)

    # Merge incoming sections with existing config to preserve unknown keys
    current_sections = config.value.get("sections", {})
    for section_id, section_cfg in request.sections.items():
        current_sections[section_id] = {
            "visible": section_cfg.visible,
            "order": section_cfg.order,
        }

    # Reassign to trigger SQLAlchemy change detection on JSONB
    config.value = {**config.value, "sections": current_sections}
    flag_modified(config, "value")

    logger.info(f"Landing page config updated by admin {_admin_id}")

    return SuccessResponse(
        data=config.value,
        message="Landing page configuration updated",
    )
