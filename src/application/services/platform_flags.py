"""Platform-wide feature flags stored in the ``public.platform_config`` KV table.

Small, dependency-light readers so storefront hot paths can honour a
super-admin master switch (e.g. the Apple Pay rollout) without each caller
needing to know the storage layout. Mirrors the ``theme_engine`` flags pattern
in ``api/v1/routes/admin/platform_config.py``.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.platform_config import (
    PlatformConfigModel,
)

# platform_config.key holding payment-related platform flags.
PAYMENTS_KEY = "payments"


async def get_payments_config(db: AsyncSession) -> dict:
    """Return the ``payments`` platform-config value (or ``{}`` when unset)."""
    result = await db.execute(
        select(PlatformConfigModel).where(PlatformConfigModel.key == PAYMENTS_KEY)
    )
    row = result.scalar_one_or_none()
    return row.value if (row and isinstance(row.value, dict)) else {}


async def is_apple_pay_platform_enabled(db: AsyncSession | None) -> bool:
    """Master switch for Apple Pay across the whole platform.

    Defaults to ``True`` (available) when the flag is unset — or when no session
    is supplied (e.g. unit tests calling a route handler directly) — so
    per-store Apple Pay works out of the box. A super-admin can flip it off in
    the admin panel to stop Apple Pay platform-wide (kill switch).
    """
    if db is None:
        return True
    cfg = await get_payments_config(db)
    return bool(cfg.get("apple_pay_enabled", True))
