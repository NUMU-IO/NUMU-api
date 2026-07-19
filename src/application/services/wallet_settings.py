"""Admin-editable wallet configuration (platform_config-backed).

The wallet's business knobs — which top-up methods are on, the default
commission rate, thresholds, the platform's Vodafone Cash number and
InstaPay IPA, and the feature switches — are controlled from the admin
panel and stored in ``platform_config`` under the ``wallet_settings``
key (same pattern as plan_limits). Env settings remain the DEFAULTS;
a DB override wins field-by-field. Secrets (gateway API keys) are NEVER
stored here — they stay env-only.

Reads are cached in-process for ``_CACHE_TTL_SECONDS`` so hot paths
(checkout gate, commission handler) don't add a query per order.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import get_settings
from src.infrastructure.database.models.public.platform_config import (
    PlatformConfigModel,
)

WALLET_SETTINGS_KEY = "wallet_settings"
_CACHE_TTL_SECONDS = 60

# Fields an admin may override. Anything else in the stored dict is ignored,
# so a stale/renamed key can never crash the merge.
_OVERRIDABLE_FIELDS = frozenset({
    "topups_enabled",
    "checkout_gate_enabled",
    "card_enabled",
    "vodafone_cash_enabled",
    "instapay_enabled",
    "commission_bps_default",
    "negative_allowance_cents",
    "low_balance_threshold_cents",
    "vodafone_cash_number",
    "instapay_ipa",
    "instapay_display_name",
})


@dataclass
class WalletAdminSettings:
    """Effective wallet configuration after merging DB overrides over env."""

    topups_enabled: bool
    checkout_gate_enabled: bool
    card_enabled: bool
    vodafone_cash_enabled: bool
    instapay_enabled: bool
    # None -> use the plan's commission_bps; set -> overrides the plan rate
    # for commission-bearing plans (per-tenant override still wins).
    commission_bps_default: int | None
    negative_allowance_cents: int
    low_balance_threshold_cents: int
    vodafone_cash_number: str | None
    instapay_ipa: str | None
    instapay_display_name: str | None

    def method_enabled(self, method: str) -> bool:
        return {
            "card": self.card_enabled,
            "vodafone_cash": self.vodafone_cash_enabled,
            "instapay": self.instapay_enabled,
        }.get(method, False)

    def methods_map(self) -> dict[str, bool]:
        return {
            "card": self.card_enabled,
            "vodafone_cash": self.vodafone_cash_enabled,
            "instapay": self.instapay_enabled,
        }


def _env_defaults() -> WalletAdminSettings:
    s = get_settings()
    return WalletAdminSettings(
        topups_enabled=s.ff_wallet_topups,
        checkout_gate_enabled=s.ff_wallet_checkout_gate,
        # Methods default ON — the real gate is topups_enabled plus the
        # presence of platform credentials/numbers for each method.
        card_enabled=True,
        vodafone_cash_enabled=True,
        instapay_enabled=True,
        commission_bps_default=None,
        negative_allowance_cents=s.wallet_negative_allowance_cents,
        low_balance_threshold_cents=s.wallet_low_balance_threshold_cents,
        vodafone_cash_number=s.platform_vodafone_cash_number,
        instapay_ipa=s.platform_instapay_ipa,
        instapay_display_name=s.platform_instapay_display_name,
    )


_cache: tuple[float, WalletAdminSettings] | None = None


def invalidate_wallet_settings_cache() -> None:
    global _cache
    _cache = None


async def get_wallet_settings(
    session: AsyncSession, *, use_cache: bool = True
) -> WalletAdminSettings:
    """Env defaults merged with the admin's platform_config overrides."""
    global _cache
    if (
        use_cache
        and _cache is not None
        and time.monotonic() - _cache[0] < _CACHE_TTL_SECONDS
    ):
        return _cache[1]

    merged = _env_defaults()
    row = (
        await session.execute(
            select(PlatformConfigModel.value).where(
                PlatformConfigModel.key == WALLET_SETTINGS_KEY
            )
        )
    ).scalar_one_or_none()
    if isinstance(row, dict):
        for field, value in row.items():
            if field in _OVERRIDABLE_FIELDS and value is not None:
                setattr(merged, field, value)

    _cache = (time.monotonic(), merged)
    return merged


async def update_wallet_settings(
    session: AsyncSession, patch: dict
) -> WalletAdminSettings:
    """Persist admin overrides (partial patch, field-by-field).

    A ``None`` value REMOVES the override (falls back to env default).
    Caller commits.
    """
    clean = {k: v for k, v in patch.items() if k in _OVERRIDABLE_FIELDS}

    row = (
        await session.execute(
            select(PlatformConfigModel).where(
                PlatformConfigModel.key == WALLET_SETTINGS_KEY
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
            key=WALLET_SETTINGS_KEY,
            value=stored,
            description="Merchant wallet (payg) admin configuration",
        )
        session.add(row)
    else:
        row.value = stored
    await session.flush()

    invalidate_wallet_settings_cache()
    return await get_wallet_settings(session, use_cache=False)


def resolve_commission_bps(
    tenant_plan_bps: int,
    wallet_override_bps: int | None,
    admin: WalletAdminSettings,
) -> int:
    """Effective rate: tenant override > admin default > plan rate.

    The admin default only applies to plans that are commission-bearing
    to begin with (``tenant_plan_bps > 0``) — it can tune the payg rate
    but can never start charging subscription tenants.
    """
    if wallet_override_bps is not None:
        return wallet_override_bps
    if tenant_plan_bps > 0 and admin.commission_bps_default is not None:
        return admin.commission_bps_default
    return tenant_plan_bps


def wallet_settings_to_dict(s: WalletAdminSettings) -> dict:
    return asdict(s)
