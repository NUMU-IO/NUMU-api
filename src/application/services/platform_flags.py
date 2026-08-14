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


# platform_config.key holding checkout-related platform flags.
CHECKOUT_KEY = "checkout"


async def get_checkout_platform_config(db: AsyncSession) -> dict:
    """Return the ``checkout`` platform-config value (or ``{}`` when unset)."""
    result = await db.execute(
        select(PlatformConfigModel).where(PlatformConfigModel.key == CHECKOUT_KEY)
    )
    row = result.scalar_one_or_none()
    return row.value if (row and isinstance(row.value, dict)) else {}


async def is_checkout_identity_platform_enabled(db: AsyncSession | None) -> bool:
    """Rollout gate for the phone-first checkout-identity feature.

    Precedence: the admin-panel flag (``checkout.identity_enabled`` in
    platform_config) wins WHEN SET, so the rollout — and the kill switch —
    is one toggle in the backoffice, no deploy. When the row/field is
    absent (fresh environment, or before the admin ever touched it) the
    ``CHECKOUT_IDENTITY_ENABLED`` env var is the default, which also keeps
    dev/test environments configurable without a DB write. No session
    (unit tests, degraded paths) falls back to the env var too.

    Unlike Apple Pay this defaults to **off**: it gates a flow customers
    must be able to PASS (an OTP at checkout), not a payment option that
    merely disappears, so it must never be on before an operator says so.
    """
    from src.config.settings import settings

    if db is None:
        return settings.checkout_identity_enabled
    try:
        cfg = await get_checkout_platform_config(db)
    except Exception:
        # Config unreadable → behave like unset rather than erroring the
        # checkout path this guards.
        return settings.checkout_identity_enabled
    value = cfg.get("identity_enabled")
    if value is None:
        return settings.checkout_identity_enabled
    return bool(value)
