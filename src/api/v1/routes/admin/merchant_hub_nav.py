"""Admin merchant-hub nav configuration.

URL: /api/v1/admin/merchant-hub-nav
Lets platform admins hide, rename, mark "coming soon", or reorder any tab in
the merchant hub left sidebar. Stored as a single JSON blob in platform_config
under key "merchant_hub_nav".

`label` is an OVERRIDE, not a name. Empty means "use whatever the hub calls
this tab", which is the translated string from its own i18n bundle — so an
override that is set replaces both languages, and clearing it gives the
translations back. That is the honest trade: one field here cannot hold a
name in two languages, and a half-Arabic sidebar reads worse than an English
word in an Arabic one.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.infrastructure.database.models.public.platform_config import (
    PlatformConfigModel,
)

router = APIRouter()

CONFIG_KEY = "merchant_hub_nav"

# Authoritative tab registry. Keep in sync with NAV_REGISTRY in the merchant
# hub (AppSidebar) and the admin client (merchantHubNavApi.ts).
#
# Convention:
#   - Top-level tabs use a bare slug ("orders", "marketing", …).
#   - Sub-tabs use a dotted "parent.child" key ("orders.drafts",
#     "marketing.coupons", …) so the admin UI can group them under their
#     parent and the hub can gate each child independently.
#   - `order` defines the default ordering (children follow their parent);
#     admins can reorder at runtime.
_KEYS: list[str] = [
    # ── Pinned ──────────────────────────────────────────────────────────
    "dashboard",
    "orders",
    "orders.all",
    "orders.drafts",
    "orders.abandoned",
    "orders.shipping-labels",
    "products",
    "products.all",
    "products.categories",
    "customers",
    # ── Sell & grow ─────────────────────────────────────────────────────
    "online-store",
    "online-store.overview",
    "online-store.themes",
    "online-store.pages",
    "online-store.navigation",
    "online-store.preferences",
    "online-store.checkout-fields",
    "online-store.my-themes",
    "marketing",
    "marketing.overview",
    "marketing.coupons",
    "marketing.promotions",
    "marketing.gift-cards",
    "marketing.campaigns",
    "marketing.whatsapp",
    "marketing.email-templates",
    "marketing.attribution",
    "marketing.audiences",
    "marketing.referrals",
    "analytics",
    "analytics.overview",
    "analytics.sales",
    "analytics.orders",
    "analytics.customers",
    "analytics.products",
    "analytics.funnel",
    "analytics.reports",
    "analytics.live",
    "analytics.insights",
    "analytics.forecast",
    "analytics.journey",
    "analytics.health",
    # ── Money ───────────────────────────────────────────────────────────
    "payments",
    "payments.overview",
    "payments.payouts",
    "payments.store-balance",
    "payments.invoices",
    "payments.payment-setup",
    "payments.billing",
    "cod",
    # ── Operations ──────────────────────────────────────────────────────
    "logistics",
    "logistics.shipments",
    "logistics.zones",
    "logistics.locations",
    "channels",
    "channels.inbox",
    "channels.social",
    "whatsapp",
    "whatsapp.inbox",
    "whatsapp.campaigns",
    "whatsapp.templates",
    "whatsapp.opt-ins",
    "whatsapp.byo",
    "whatsapp.dead-letters",
    "staff",
    "staff.members",
    "staff.roles",
    "apps",
    # ── Footer ──────────────────────────────────────────────────────────
    "notifications",
    "settings",
    "store",
    # ── Global widgets (not sidebar tabs) ───────────────────────────────
    # The floating AI Assistant launcher. Gated by the hub via
    # useNavConfig().isVisible("assistant") exactly like every sidebar tab,
    # so super-admins flip it from the same Merchant Hub Nav screen.
    "assistant",
]

DEFAULT_TABS: list[dict[str, object]] = [
    # `label` empty = the hub's own translated name for this tab.
    {"key": key, "visible": True, "coming_soon": False, "order": i, "label": ""}
    for i, key in enumerate(_KEYS)
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
    #: Renames the tab in the merchant hub. Empty = the hub's own translated
    #: name. Capped short because the sidebar is a fixed width and a long
    #: label truncates into something nobody can read.
    label: str = Field(default="", max_length=32)


class NavConfig(BaseModel):
    tabs: list[NavTab] = Field(default_factory=list)


async def _get_config_row(db: AsyncSession) -> PlatformConfigModel:
    """Race-safe upsert. See platform_settings._get_or_create_settings
    for the rationale — concurrent first-time requests would otherwise
    both INSERT and trip the unique key."""
    result = await db.execute(
        select(PlatformConfigModel).where(PlatformConfigModel.key == CONFIG_KEY)
    )
    config = result.scalar_one_or_none()
    if config is None:
        stmt = (
            pg_insert(PlatformConfigModel)
            .values(
                key=CONFIG_KEY,
                value=DEFAULT_CONFIG,
                description="Per-tab visibility / coming-soon / order / name "
                "override for the merchant hub left sidebar.",
            )
            .on_conflict_do_nothing(index_elements=["key"])
        )
        await db.execute(stmt)
        await db.commit()
        result = await db.execute(
            select(PlatformConfigModel).where(PlatformConfigModel.key == CONFIG_KEY)
        )
        config = result.scalar_one()
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
                "label": str(s.get("label") or "")[:32],
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
            # Whitespace-only is not a rename; it would blank the tab.
            "label": (t.label.strip()[:32] if t else "") or "",
        })

    config = await _get_config_row(db)
    config.value = {"tabs": final}
    await db.commit()
    await db.refresh(config)
    return SuccessResponse(data=NavConfig(**config.value))
