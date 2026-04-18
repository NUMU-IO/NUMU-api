"""Admin merchant-hub nav configuration.

URL: /api/v1/admin/merchant-hub-nav
Lets platform admins hide, mark "coming soon", or reorder any tab in the
merchant hub left sidebar. Stored as a single JSON blob in platform_config
under key "merchant_hub_nav".
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.infrastructure.database.models.public.platform_config import (
    PlatformConfigModel,
)

router = APIRouter()

CONFIG_KEY = "merchant_hub_nav"

# Authoritative tab registry. Keep in sync with NAV_TABS in the merchant hub.
# order here defines the default ordering; admin can reorder at runtime.
DEFAULT_TABS: list[dict[str, object]] = [
    {"key": "dashboard", "visible": True, "coming_soon": False, "order": 0},
    {"key": "orders", "visible": True, "coming_soon": False, "order": 1},
    {"key": "products", "visible": True, "coming_soon": False, "order": 2},
    {"key": "categories", "visible": True, "coming_soon": False, "order": 3},
    {"key": "customers", "visible": True, "coming_soon": False, "order": 4},
    {"key": "marketing", "visible": True, "coming_soon": False, "order": 5},
    {"key": "referrals", "visible": True, "coming_soon": False, "order": 6},
    {"key": "payments", "visible": True, "coming_soon": False, "order": 7},
    {"key": "whatsapp", "visible": True, "coming_soon": False, "order": 8},
    {"key": "analytics", "visible": True, "coming_soon": False, "order": 9},
    {"key": "online-store", "visible": True, "coming_soon": False, "order": 10},
    {"key": "staff", "visible": True, "coming_soon": False, "order": 11},
    {"key": "channels", "visible": True, "coming_soon": False, "order": 12},
    {"key": "inbox", "visible": True, "coming_soon": False, "order": 13},
    {"key": "payment-setup", "visible": True, "coming_soon": False, "order": 14},
    {"key": "logistics", "visible": True, "coming_soon": False, "order": 15},
    {"key": "cod", "visible": True, "coming_soon": False, "order": 16},
    {"key": "social", "visible": True, "coming_soon": False, "order": 17},
    {"key": "invoices", "visible": True, "coming_soon": False, "order": 18},
    {"key": "billing", "visible": True, "coming_soon": False, "order": 19},
    {"key": "notifications", "visible": True, "coming_soon": False, "order": 20},
    {"key": "settings", "visible": True, "coming_soon": False, "order": 21},
    {"key": "store", "visible": True, "coming_soon": False, "order": 22},
]

DEFAULT_CONFIG = {"tabs": DEFAULT_TABS}

# Allow-list — we drop unknown keys on write so the admin can't poison the
# blob with typos that would never render on the hub side.
ALLOWED_KEYS = {t["key"] for t in DEFAULT_TABS}


class NavTab(BaseModel):
    key: str
    visible: bool = True
    coming_soon: bool = False
    order: int = 0


class NavConfig(BaseModel):
    tabs: list[NavTab] = Field(default_factory=list)


async def _get_config_row(db: AsyncSession) -> PlatformConfigModel:
    result = await db.execute(
        select(PlatformConfigModel).where(PlatformConfigModel.key == CONFIG_KEY)
    )
    config = result.scalar_one_or_none()
    if config is None:
        config = PlatformConfigModel(
            key=CONFIG_KEY,
            value=DEFAULT_CONFIG,
            description="Per-tab visibility / coming-soon / order for the "
            "merchant hub left sidebar.",
        )
        db.add(config)
        await db.commit()
        await db.refresh(config)
    return config


def _merge_with_defaults(stored: dict) -> dict:
    """Union the stored tabs with DEFAULT_TABS.

    - New tabs added to DEFAULT_TABS since the config was last written
      appear with their default state instead of being silently hidden.
    - Tabs removed from DEFAULT_TABS are dropped.
    - Stored values (visible/coming_soon/order) win when the key still exists.
    """
    stored_tabs = {
        t["key"]: t
        for t in (stored.get("tabs") or [])
        if isinstance(t, dict) and "key" in t
    }
    merged: list[dict[str, object]] = []
    for default in DEFAULT_TABS:
        if default["key"] in stored_tabs:
            s = stored_tabs[default["key"]]
            merged.append({
                "key": default["key"],
                "visible": bool(s.get("visible", default["visible"])),
                "coming_soon": bool(s.get("coming_soon", default["coming_soon"])),
                "order": int(s.get("order", default["order"])),
            })
        else:
            merged.append(default.copy())
    return {"tabs": merged}


@router.get(
    "",
    response_model=SuccessResponse[NavConfig],
    summary="Get merchant hub nav config",
)
async def get_merchant_hub_nav(
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[dict, Depends(require_admin)],
) -> SuccessResponse[NavConfig]:
    config = await _get_config_row(db)
    return SuccessResponse(data=NavConfig(**_merge_with_defaults(config.value)))


@router.put(
    "",
    response_model=SuccessResponse[NavConfig],
    summary="Update merchant hub nav config",
)
async def update_merchant_hub_nav(
    payload: NavConfig,
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[dict, Depends(require_admin)],
) -> SuccessResponse[NavConfig]:
    # Drop unknown tab keys entirely instead of silently accepting them.
    filtered = [t for t in payload.tabs if t.key in ALLOWED_KEYS]
    # Ensure coverage of every known key (missing ones stay at default).
    by_key = {t.key: t for t in filtered}
    final: list[dict[str, object]] = []
    for default in DEFAULT_TABS:
        t = by_key.get(default["key"])
        final.append({
            "key": default["key"],
            "visible": bool(t.visible) if t else bool(default["visible"]),
            "coming_soon": bool(t.coming_soon) if t else bool(default["coming_soon"]),
            "order": int(t.order) if t else int(default["order"]),
        })

    config = await _get_config_row(db)
    config.value = {"tabs": final}
    await db.commit()
    await db.refresh(config)
    return SuccessResponse(data=NavConfig(**config.value))
