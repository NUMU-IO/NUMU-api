"""COD Shield gate: NUMU's COD protection belongs to the COD Shield app.

The Trust Network check, the checkout phone OTP, the COD confirmation
deposit, WhatsApp tap-to-confirm and the per-zone COD fee all still run
inside NUMU (checkout has to enforce them), but they are sold as the COD
Shield app. ``platform_config`` key ``cod_shield`` =
``{"required": bool, "app_slug": str}`` decides:

* ``required`` off (the default, and the state at deploy): every store keeps
  the features exactly as before.
* ``required`` on: a store gets them only while COD Shield is installed and
  active on it; otherwise checkout behaves as if each one were switched off.

Every check fails open: a lookup error must never take COD away mid-checkout.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select

from src.core.logging import get_logger

logger = get_logger(__name__)

CONFIG_KEY = "cod_shield"
DEFAULT_APP_SLUG = "cod-shield"


async def gate_config(session: Any) -> dict[str, Any]:
    from src.infrastructure.database.models.public.platform_config import (
        PlatformConfigModel,
    )

    value = await session.scalar(
        select(PlatformConfigModel.value).where(PlatformConfigModel.key == CONFIG_KEY)
    )
    return value if isinstance(value, dict) else {}


async def cod_shield_allows(session: Any, store_id: UUID | str) -> bool:
    """True when this store may use NUMU's COD protection features."""
    if session is None:
        return True
    try:
        cfg = await gate_config(session)
        if not cfg.get("required"):
            return True
        from src.infrastructure.database.models.public.app import (
            AppInstallationModel,
            AppModel,
        )

        installed = await session.scalar(
            select(AppInstallationModel.id)
            .join(AppModel, AppModel.id == AppInstallationModel.app_id)
            .where(
                AppModel.slug == (cfg.get("app_slug") or DEFAULT_APP_SLUG),
                AppInstallationModel.store_id == UUID(str(store_id)),
                AppInstallationModel.is_enabled.is_(True),
                AppInstallationModel.status == "active",
            )
            .limit(1)
        )
        return installed is not None
    except Exception:
        logger.warning("cod_shield_gate_check_failed", store_id=str(store_id))
        return True
