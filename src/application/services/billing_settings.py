"""Admin-tunable billing lifecycle settings (platform_config-backed).

Controls the merchant-facing subscription lifecycle without a deploy:

* **Pre-expiry warnings** — how many days before the trial ends / the
  paid period renews the merchant gets a bilingual heads-up email, and
  a master switch for those emails. ``renewal_warning_days`` also
  drives the hub Billing page's "renewal due — pay now" window.
* **Dunning** — how many failed renewal attempts (24h apart by
  default) a paid tenant gets before read_only, mirroring the wallet
  settings pattern: code defaults, admin overrides in the
  ``billing_lifecycle_settings`` platform_config key, 60s in-process
  cache, ``None`` in a patch clears an override back to the default.

Same shape/discipline as ``wallet_settings.py`` — one dataclass, one
key, secrets never stored here.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.platform_config import (
    PlatformConfigModel,
)

BILLING_SETTINGS_KEY = "billing_lifecycle_settings"
_CACHE_TTL_SECONDS = 60

# Code defaults — match the constants the renewal task shipped with, so
# an empty platform_config row changes nothing.
DEFAULT_RENEWAL_WARNING_DAYS = 7
DEFAULT_TRIAL_WARNING_DAYS = 5
DEFAULT_DUNNING_MAX_RETRIES = 3
DEFAULT_DUNNING_RETRY_BACKOFF_HOURS = 24
DEFAULT_DUNNING_WINDOW_HOURS = 72

_OVERRIDABLE_FIELDS = frozenset({
    "warning_emails_enabled",
    "renewal_warning_days",
    "trial_warning_days",
    "dunning_max_retries",
    "dunning_retry_backoff_hours",
    "dunning_window_hours",
})

# Sanity clamps — a fat-fingered 0/None must never turn dunning into an
# instant read_only or a warning-email flood.
_CLAMPS: dict[str, tuple[int, int]] = {
    "renewal_warning_days": (1, 30),
    "trial_warning_days": (1, 30),
    "dunning_max_retries": (1, 10),
    "dunning_retry_backoff_hours": (1, 168),
    "dunning_window_hours": (0, 720),
}


@dataclass
class BillingLifecycleSettings:
    """Effective settings: code defaults merged with admin overrides."""

    warning_emails_enabled: bool
    renewal_warning_days: int
    trial_warning_days: int
    dunning_max_retries: int
    dunning_retry_backoff_hours: int
    dunning_window_hours: int


def _defaults() -> BillingLifecycleSettings:
    return BillingLifecycleSettings(
        warning_emails_enabled=True,
        renewal_warning_days=DEFAULT_RENEWAL_WARNING_DAYS,
        trial_warning_days=DEFAULT_TRIAL_WARNING_DAYS,
        dunning_max_retries=DEFAULT_DUNNING_MAX_RETRIES,
        dunning_retry_backoff_hours=DEFAULT_DUNNING_RETRY_BACKOFF_HOURS,
        dunning_window_hours=DEFAULT_DUNNING_WINDOW_HOURS,
    )


def _clamp(field_name: str, value):  # noqa: ANN001, ANN202 — tiny local guard
    bounds = _CLAMPS.get(field_name)
    if bounds is None or not isinstance(value, int):
        return value
    lo, hi = bounds
    return max(lo, min(hi, value))


_cache: tuple[float, BillingLifecycleSettings] | None = None


def invalidate_billing_settings_cache() -> None:
    global _cache
    _cache = None


async def get_billing_settings(
    session: AsyncSession, *, use_cache: bool = True
) -> BillingLifecycleSettings:
    """Code defaults merged with the admin's platform_config overrides."""
    global _cache
    if (
        use_cache
        and _cache is not None
        and time.monotonic() - _cache[0] < _CACHE_TTL_SECONDS
    ):
        return _cache[1]

    merged = _defaults()
    row = (
        await session.execute(
            select(PlatformConfigModel.value).where(
                PlatformConfigModel.key == BILLING_SETTINGS_KEY
            )
        )
    ).scalar_one_or_none()
    if isinstance(row, dict):
        for field_name, value in row.items():
            if field_name in _OVERRIDABLE_FIELDS and value is not None:
                setattr(merged, field_name, _clamp(field_name, value))

    _cache = (time.monotonic(), merged)
    return merged


async def update_billing_settings(
    session: AsyncSession, patch: dict
) -> BillingLifecycleSettings:
    """Persist admin overrides (partial patch, field-by-field).

    A ``None`` value REMOVES the override (falls back to the code
    default). Caller commits.
    """
    clean = {k: v for k, v in patch.items() if k in _OVERRIDABLE_FIELDS}

    row = (
        await session.execute(
            select(PlatformConfigModel).where(
                PlatformConfigModel.key == BILLING_SETTINGS_KEY
            )
        )
    ).scalar_one_or_none()
    stored = dict(row.value) if row and isinstance(row.value, dict) else {}
    for key, value in clean.items():
        if value is None:
            stored.pop(key, None)
        else:
            stored[key] = _clamp(key, value)

    if row is None:
        row = PlatformConfigModel(
            key=BILLING_SETTINGS_KEY,
            value=stored,
            description="Subscription lifecycle: pre-expiry warnings + dunning",
        )
        session.add(row)
    else:
        row.value = stored
    await session.flush()

    invalidate_billing_settings_cache()
    return await get_billing_settings(session, use_cache=False)


def billing_settings_to_dict(s: BillingLifecycleSettings) -> dict:
    return asdict(s)
