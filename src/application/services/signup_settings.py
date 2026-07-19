"""Admin-editable signup & trial configuration (platform_config-backed).

Controls the acquisition funnel without a deploy (same merge pattern as
``wallet_settings``: code defaults ← ``platform_config`` row keyed
``signup_settings``, field-by-field, 60s in-process cache):

* ``trial_enabled``  — new signups get a free-trial window at all. When
  OFF, new users register with ``trial_ends_at = None``: they can still
  build (the go-live gate governs selling), there's just no countdown
  and no trial marketing.
* ``trial_days``     — length of the trial window (marketing says 37).
  Read by registration AND demo→trial conversion.
* ``trial_visible_on_landing`` — whether the landing page shows trial
  messaging / the trial card at all.
* ``payg_visible_on_landing``  — whether the Pay as you Grow card is
  injected into the public pricing payload.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.platform_config import (
    PlatformConfigModel,
)

SIGNUP_SETTINGS_KEY = "signup_settings"
_CACHE_TTL_SECONDS = 60

_OVERRIDABLE_FIELDS = frozenset({
    "trial_enabled",
    "trial_days",
    "trial_visible_on_landing",
    "payg_visible_on_landing",
})


@dataclass
class SignupSettings:
    trial_enabled: bool = True
    trial_days: int = 37
    trial_visible_on_landing: bool = True
    payg_visible_on_landing: bool = True


_cache: tuple[float, SignupSettings] | None = None


def invalidate_signup_settings_cache() -> None:
    global _cache
    _cache = None


async def get_signup_settings(
    session: AsyncSession, *, use_cache: bool = True
) -> SignupSettings:
    """Code defaults merged with the admin's platform_config overrides."""
    global _cache
    if (
        use_cache
        and _cache is not None
        and time.monotonic() - _cache[0] < _CACHE_TTL_SECONDS
    ):
        return _cache[1]

    merged = SignupSettings()
    row = (
        await session.execute(
            select(PlatformConfigModel.value).where(
                PlatformConfigModel.key == SIGNUP_SETTINGS_KEY
            )
        )
    ).scalar_one_or_none()
    if isinstance(row, dict):
        for field, value in row.items():
            if field in _OVERRIDABLE_FIELDS and value is not None:
                setattr(merged, field, value)

    _cache = (time.monotonic(), merged)
    return merged


async def update_signup_settings(session: AsyncSession, patch: dict) -> SignupSettings:
    """Persist admin overrides (partial; ``None`` clears back to default).

    Caller commits.
    """
    clean = {k: v for k, v in patch.items() if k in _OVERRIDABLE_FIELDS}

    row = (
        await session.execute(
            select(PlatformConfigModel).where(
                PlatformConfigModel.key == SIGNUP_SETTINGS_KEY
            )
        )
    ).scalar_one_or_none()
    stored = dict(row.value) if row and isinstance(row.value, dict) else {}
    for key, value in clean.items():
        if value is None:
            stored.pop(key, None)
        else:
            stored[key] = value

    if row is None:
        row = PlatformConfigModel(
            key=SIGNUP_SETTINGS_KEY,
            value=stored,
            description="Signup & free-trial configuration (landing + register)",
        )
        session.add(row)
    else:
        row.value = stored
    await session.flush()

    invalidate_signup_settings_cache()
    return await get_signup_settings(session, use_cache=False)


def signup_settings_to_dict(s: SignupSettings) -> dict:
    return asdict(s)
