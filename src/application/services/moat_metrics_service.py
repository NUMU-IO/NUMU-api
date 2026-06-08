"""Moat-metrics — does the cross-merchant COD trust network actually work?

Platform-wide aggregates that PROVE the moat for due diligence:

  * **Coverage** — how many buyers the network sees, and how many are visible
    at more than one store (the network effect's reach).
  * **Cross-store catch** — of buyers who've had an RTO, how many are visible
    at >1 store — i.e. how much of the negative signal protects OTHER
    merchants. This is the whole point of the moat.
  * **Auto-approve quality** — does trust-driven auto-approve keep RTO BELOW
    the COD baseline? A negative delta means the network picks good customers.
  * **Kill-switch incidents** + **trust-tier distribution** for context.

Internal/admin use only — cross-tenant, and PII-free (phone hashes + counts).
The rate math is pure + unit-tested; ``gather_moat_metrics`` runs the SQL.
"""

from __future__ import annotations

from typing import Any


def _pct(n: int, d: int) -> float:
    return round(n / d * 100, 1) if d else 0.0


def _rate(n: int, d: int) -> float:
    return n / d if d else 0.0


def compute_coverage(
    *, phones_tracked: int, multi_store_phones: int, total_network_orders: int
) -> dict[str, Any]:
    return {
        "phones_tracked": phones_tracked,
        "multi_store_phones": multi_store_phones,
        # Share of tracked buyers seen at more than one store — the network reach.
        "cross_store_reach_pct": _pct(multi_store_phones, phones_tracked),
        "total_network_orders": total_network_orders,
    }


def compute_cross_store_catch(
    *, phones_with_rtos: int, cross_store_phones_with_rtos: int
) -> dict[str, Any]:
    return {
        "phones_with_rtos": phones_with_rtos,
        "cross_store_risk_signals": cross_store_phones_with_rtos,
        # Of buyers who've had an RTO, how many are visible at >1 store — i.e.
        # how much of the negative signal protects OTHER merchants. The moat.
        "cross_store_catch_pct": _pct(cross_store_phones_with_rtos, phones_with_rtos),
    }


def compute_auto_approve_quality(
    *,
    auto_approved: int,
    auto_approved_rtos: int,
    baseline_cod: int,
    baseline_cod_rtos: int,
) -> dict[str, Any]:
    aa = _rate(auto_approved_rtos, auto_approved)
    base = _rate(baseline_cod_rtos, baseline_cod)
    return {
        "auto_approved_orders": auto_approved,
        "auto_approved_rto_rate_pct": round(aa * 100, 2),
        "baseline_cod_rto_rate_pct": round(base * 100, 2),
        # Negative = the auto-approved cohort beats the COD baseline (the
        # network picks good customers). The kill-switch caps this cohort at 5%.
        "rto_rate_delta_pct": round((aa - base) * 100, 2),
    }


async def gather_moat_metrics(session: Any) -> dict[str, Any]:
    """Run the platform-wide aggregate queries and assemble the metrics dict."""
    from sqlalchemy import String, cast, func, select, text

    from src.infrastructure.database.models.tenant.network_reputation import (
        NetworkReputationModel as NR,
    )
    from src.infrastructure.database.models.tenant.risk_assessment import (
        RiskAssessmentModel as RA,
    )
    from src.infrastructure.database.models.tenant.shipment import (
        ShipmentModel as SH,
    )
    from src.infrastructure.database.models.tenant.shopify_app_settings import (
        ShopifyAppSettingsModel as SS,
    )

    await session.execute(text("SET search_path TO public"))

    async def scalar(stmt) -> int:
        return int((await session.execute(stmt)).scalar() or 0)

    _RETURNED = ("returned", "rto")
    _COD = ("cod", "cash_on_delivery", "cash")

    # ── Coverage ───────────────────────────────────────────────────────────
    phones = await scalar(
        select(func.count()).select_from(NR).where(NR.anonymized_at.is_(None))
    )
    multi = await scalar(
        select(func.count())
        .select_from(NR)
        .where(NR.anonymized_at.is_(None), NR.contributing_store_count > 1)
    )
    net_orders = await scalar(
        select(func.coalesce(func.sum(NR.total_network_orders), 0))
    )

    # ── Cross-store catch ──────────────────────────────────────────────────
    with_rtos = await scalar(
        select(func.count()).select_from(NR).where(NR.total_network_rtos > 0)
    )
    cross_rtos = await scalar(
        select(func.count())
        .select_from(NR)
        .where(NR.total_network_rtos > 0, NR.contributing_store_count > 1)
    )

    # ── Auto-approve quality (cast the UUID order_id to text for the join) ──
    aa = await scalar(
        select(func.count())
        .select_from(RA)
        .where(RA.action_taken_by == "system_trust_auto")
    )
    aa_rtos = await scalar(
        select(func.count())
        .select_from(RA)
        .join(SH, SH.order_id == cast(RA.order_id, String))
        .where(RA.action_taken_by == "system_trust_auto", SH.status.in_(_RETURNED))
    )
    base_cod = await scalar(
        select(func.count())
        .select_from(RA)
        .where(func.lower(RA.payment_method).in_(_COD))
    )
    base_rtos = await scalar(
        select(func.count())
        .select_from(RA)
        .join(SH, SH.order_id == cast(RA.order_id, String))
        .where(func.lower(RA.payment_method).in_(_COD), SH.status.in_(_RETURNED))
    )

    # ── Kill-switch incidents ──────────────────────────────────────────────
    kill = await scalar(
        select(func.count()).select_from(SS).where(SS.auto_disabled_at.isnot(None))
    )

    # ── Trust-tier distribution (final scores) ─────────────────────────────
    tier_rows = (
        await session.execute(
            select(RA.trust_tier, func.count())
            .where(RA.score_type == "final", RA.trust_tier.isnot(None))
            .group_by(RA.trust_tier)
        )
    ).all()
    tiers = {str(t): int(c) for t, c in tier_rows}

    return {
        "coverage": compute_coverage(
            phones_tracked=phones,
            multi_store_phones=multi,
            total_network_orders=net_orders,
        ),
        "cross_store_catch": compute_cross_store_catch(
            phones_with_rtos=with_rtos,
            cross_store_phones_with_rtos=cross_rtos,
        ),
        "auto_approve_quality": compute_auto_approve_quality(
            auto_approved=aa,
            auto_approved_rtos=aa_rtos,
            baseline_cod=base_cod,
            baseline_cod_rtos=base_rtos,
        ),
        "kill_switch_incidents": kill,
        "trust_tier_distribution": tiers,
    }
