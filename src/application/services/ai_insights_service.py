"""AI-powered insights service.

Two-layer architecture:
1. Rule engine: detects anomalies, trends, risks, opportunities from rollup data
2. LLM layer (Gemini via Google AI Studio): generates natural-language narratives from signals

The rule engine always works. The LLM layer is optional — if it fails, we fall back
to template-based text.
"""

import json
import logging
import math
from collections.abc import Sequence
from datetime import UTC, datetime

from openai import AsyncOpenAI

from src.config import settings
from src.infrastructure.database.models.tenant.analytics_rollup import (
    AnalyticsDailyRollupModel,
)

logger = logging.getLogger(__name__)


# ── Signal types ──

SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"
SEVERITY_SUCCESS = "success"


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    variance = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2


def _slope_7d(values: list[float]) -> float:
    """Linear regression slope over the last 7 data points."""
    n = min(len(values), 7)
    if n < 3:
        return 0.0
    recent = values[-n:]
    x_mean = (n - 1) / 2
    y_mean = _mean(recent)
    num = sum((i - x_mean) * (recent[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den != 0 else 0.0


# ── Rule Engine ──


def detect_signals(
    rollups: Sequence[AnalyticsDailyRollupModel],
    store_currency: str = "EGP",
) -> list[dict]:
    """Analyze rollup data and return a list of signal dicts.

    Each signal has: type, severity, metric, current_value, baseline_value,
    deviation_pct, title_en, title_ar, body_en, body_ar, action_en, action_ar
    """
    if len(rollups) < 7:
        return [
            {
                "type": "insufficient_data",
                "severity": SEVERITY_INFO,
                "title_en": "Not enough data for insights",
                "title_ar": "مفيش بيانات كافية للتحليل",
                "body_en": f"We need at least 7 days of data. You have {len(rollups)} days so far.",
                "body_ar": f"محتاجين ٧ أيام بيانات على الأقل. عندك {len(rollups)} أيام حالياً.",
                "action_en": "Keep selling! Insights will appear soon.",
                "action_ar": "استمر في البيع! التحليلات هتظهر قريب.",
            }
        ]

    signals: list[dict] = []

    # Split data: current week vs previous 4 weeks (baseline)
    sorted_rollups = sorted(rollups, key=lambda r: r.rollup_date)
    current_week = sorted_rollups[-7:]
    baseline = sorted_rollups[:-7] if len(sorted_rollups) > 7 else []

    # --- Revenue anomaly ---
    curr_revenue = [float(r.total_revenue_cents) for r in current_week]
    base_revenue = [float(r.total_revenue_cents) for r in baseline] if baseline else []

    if base_revenue:
        curr_avg = _mean(curr_revenue)
        base_avg = _mean(base_revenue)
        base_std = _std(base_revenue)

        if base_std > 0:
            z_score = (curr_avg - base_avg) / base_std
            deviation_pct = (
                round((curr_avg - base_avg) / base_avg * 100, 1) if base_avg > 0 else 0
            )

            if z_score > 2:
                signals.append({
                    "type": "revenue_spike",
                    "severity": SEVERITY_SUCCESS,
                    "metric": "revenue",
                    "current_value": round(curr_avg),
                    "baseline_value": round(base_avg),
                    "deviation_pct": deviation_pct,
                    "z_score": round(z_score, 1),
                    "title_en": f"Revenue up {deviation_pct}% vs baseline",
                    "title_ar": f"الإيرادات ارتفعت {deviation_pct}% عن المعدل",
                    "body_en": f"Daily avg revenue this week is {deviation_pct}% above your 4-week average.",
                    "body_ar": f"متوسط الإيرادات اليومي هذا الأسبوع أعلى بنسبة {deviation_pct}% من متوسط ٤ أسابيع.",
                    "action_en": "Identify what's driving this growth and double down.",
                    "action_ar": "حدد سبب النمو ده وركز عليه أكتر.",
                })
            elif z_score < -2:
                signals.append({
                    "type": "revenue_drop",
                    "severity": SEVERITY_CRITICAL,
                    "metric": "revenue",
                    "current_value": round(curr_avg),
                    "baseline_value": round(base_avg),
                    "deviation_pct": deviation_pct,
                    "z_score": round(z_score, 1),
                    "title_en": f"Revenue down {abs(deviation_pct)}% vs baseline",
                    "title_ar": f"الإيرادات انخفضت {abs(deviation_pct)}% عن المعدل",
                    "body_en": "Daily avg revenue dropped significantly below your normal range.",
                    "body_ar": "متوسط الإيرادات اليومي انخفض بشكل ملحوظ عن المعدل الطبيعي.",
                    "action_en": "Check for issues: site downtime, stock-outs, or campaign changes.",
                    "action_ar": "تحقق من المشاكل: توقف الموقع، نفاد المخزون، أو تغييرات الحملات.",
                })

    # --- Order volume anomaly ---
    curr_orders = [float(r.total_orders) for r in current_week]
    base_orders = [float(r.total_orders) for r in baseline] if baseline else []

    if base_orders:
        curr_ord_avg = _mean(curr_orders)
        base_ord_avg = _mean(base_orders)
        base_ord_std = _std(base_orders)

        if base_ord_std > 0:
            z = (curr_ord_avg - base_ord_avg) / base_ord_std
            dev = (
                round((curr_ord_avg - base_ord_avg) / base_ord_avg * 100, 1)
                if base_ord_avg > 0
                else 0
            )

            if z < -2:
                signals.append({
                    "type": "orders_drop",
                    "severity": SEVERITY_WARNING,
                    "metric": "orders",
                    "deviation_pct": dev,
                    "title_en": f"Order volume down {abs(dev)}%",
                    "title_ar": f"حجم الطلبات انخفض {abs(dev)}%",
                    "body_en": "Fewer orders than usual this week.",
                    "body_ar": "عدد الطلبات أقل من المعتاد هذا الأسبوع.",
                    "action_en": "Consider running a promotion or checking your ad spend.",
                    "action_ar": "فكر في عمل عرض أو مراجعة ميزانية الإعلانات.",
                })

    # --- Revenue trend (7-day slope) ---
    all_revenue = [float(r.total_revenue_cents) for r in sorted_rollups]
    slope = _slope_7d(all_revenue)
    daily_avg = _mean(all_revenue[-7:])

    if daily_avg > 0:
        slope_pct = round(slope / daily_avg * 100, 1)
        if slope_pct > 5:
            signals.append({
                "type": "revenue_uptrend",
                "severity": SEVERITY_SUCCESS,
                "metric": "revenue_trend",
                "deviation_pct": slope_pct,
                "title_en": "Revenue trending upward",
                "title_ar": "الإيرادات في اتجاه صاعد",
                "body_en": f"Revenue is growing at ~{slope_pct}% per day over the last 7 days.",
                "body_ar": f"الإيرادات بتنمو بمعدل ~{slope_pct}% يومياً خلال آخر ٧ أيام.",
                "action_en": "Great momentum. Ensure inventory can handle increased demand.",
                "action_ar": "زخم ممتاز. تأكد إن المخزون يقدر يتحمل الطلب المتزايد.",
            })
        elif slope_pct < -5:
            signals.append({
                "type": "revenue_downtrend",
                "severity": SEVERITY_WARNING,
                "metric": "revenue_trend",
                "deviation_pct": slope_pct,
                "title_en": "Revenue trending downward",
                "title_ar": "الإيرادات في اتجاه هابط",
                "body_en": f"Revenue is declining at ~{abs(slope_pct)}% per day over the last 7 days.",
                "body_ar": f"الإيرادات بتنخفض بمعدل ~{abs(slope_pct)}% يومياً خلال آخر ٧ أيام.",
                "action_en": "Investigate the cause. Check traffic, conversion, and average order value.",
                "action_ar": "ابحث عن السبب. راجع الزيارات والتحويلات ومتوسط قيمة الطلب.",
            })

    # --- COD rejection spike ---
    curr_cod_rejected = sum(r.cod_rejected for r in current_week)
    curr_cod_total = sum(r.cod_orders for r in current_week)
    base_cod_rejected = sum(r.cod_rejected for r in baseline) if baseline else 0
    base_cod_total = sum(r.cod_orders for r in baseline) if baseline else 0

    if curr_cod_total >= 5:
        curr_rej_rate = curr_cod_rejected / curr_cod_total * 100
        base_rej_rate = (
            (base_cod_rejected / base_cod_total * 100) if base_cod_total > 0 else 0
        )

        if curr_rej_rate > 20:
            signals.append({
                "type": "cod_rejection_high",
                "severity": SEVERITY_CRITICAL,
                "metric": "cod_rejection",
                "current_value": round(curr_rej_rate, 1),
                "baseline_value": round(base_rej_rate, 1),
                "title_en": f"COD rejection rate at {round(curr_rej_rate, 1)}%",
                "title_ar": f"معدل رفض الدفع عند الاستلام وصل {round(curr_rej_rate, 1)}%",
                "body_en": f"{curr_cod_rejected} of {curr_cod_total} COD orders were rejected this week.",
                "body_ar": f"{curr_cod_rejected} من {curr_cod_total} طلب دفع عند الاستلام تم رفضهم هذا الأسبوع.",
                "action_en": "Consider requiring phone verification or reducing COD availability in high-rejection areas.",
                "action_ar": "فكر في طلب تأكيد الهاتف أو تقليل الدفع عند الاستلام في المناطق ذات الرفض العالي.",
            })
        elif curr_rej_rate > base_rej_rate * 1.5 and base_rej_rate > 0:
            signals.append({
                "type": "cod_rejection_spike",
                "severity": SEVERITY_WARNING,
                "metric": "cod_rejection",
                "current_value": round(curr_rej_rate, 1),
                "baseline_value": round(base_rej_rate, 1),
                "title_en": f"COD rejections up ({round(curr_rej_rate, 1)}% vs {round(base_rej_rate, 1)}%)",
                "title_ar": f"رفض الدفع عند الاستلام زاد ({round(curr_rej_rate, 1)}% مقابل {round(base_rej_rate, 1)}%)",
                "body_en": "COD rejection rate is significantly higher than your baseline.",
                "body_ar": "معدل رفض الدفع عند الاستلام أعلى بشكل ملحوظ من المعتاد.",
                "action_en": "Review which locations and products have the highest rejection rates.",
                "action_ar": "راجع أي المواقع والمنتجات عندها أعلى معدلات رفض.",
            })

    # --- Fulfillment slowdown ---
    # Cancelled orders spike
    curr_cancelled = sum(r.cancelled_orders for r in current_week)
    curr_total = sum(r.total_orders for r in current_week)
    if curr_total >= 5:
        cancel_rate = curr_cancelled / curr_total * 100
        if cancel_rate > 15:
            signals.append({
                "type": "high_cancellation",
                "severity": SEVERITY_WARNING,
                "metric": "cancellation_rate",
                "current_value": round(cancel_rate, 1),
                "title_en": f"Cancellation rate at {round(cancel_rate, 1)}%",
                "title_ar": f"معدل الإلغاء وصل {round(cancel_rate, 1)}%",
                "body_en": f"{curr_cancelled} orders cancelled out of {curr_total} this week.",
                "body_ar": f"{curr_cancelled} طلب ملغي من أصل {curr_total} هذا الأسبوع.",
                "action_en": "Check product availability and fulfillment speed.",
                "action_ar": "تحقق من توفر المنتجات وسرعة التجهيز.",
            })

    # --- Refund spike ---
    curr_refunds = sum(r.refund_count for r in current_week)
    curr_refund_amount = sum(r.refund_amount_cents for r in current_week)
    base_refunds = sum(r.refund_count for r in baseline) if baseline else 0
    base_weeks = max(len(baseline) / 7, 1)

    if curr_refunds > 0:
        base_weekly_refunds = base_refunds / base_weeks if base_weeks > 0 else 0
        if curr_refunds > base_weekly_refunds * 2 and base_weekly_refunds > 0:
            signals.append({
                "type": "refund_spike",
                "severity": SEVERITY_WARNING,
                "metric": "refunds",
                "current_value": curr_refunds,
                "baseline_value": round(base_weekly_refunds, 1),
                "title_en": f"{curr_refunds} refunds this week (2x+ normal)",
                "title_ar": f"{curr_refunds} استرجاع هذا الأسبوع (ضعف المعتاد أو أكتر)",
                "body_en": f"Refund amount: {curr_refund_amount / 100:.0f} {store_currency}.",
                "body_ar": f"مبلغ الاسترجاع: {curr_refund_amount / 100:.0f} {store_currency}.",
                "action_en": "Review refund reasons. Check product quality or description accuracy.",
                "action_ar": "راجع أسباب الاسترجاع. تحقق من جودة المنتجات أو دقة الوصف.",
            })

    # --- New customer acquisition ---
    curr_new = sum(r.new_customers for r in current_week)
    curr_returning = sum(r.returning_customers for r in current_week)
    base_new = [float(r.new_customers) for r in baseline] if baseline else []

    if base_new and curr_new > 0:
        base_new_avg = _mean(base_new) * 7  # Weekly average
        if base_new_avg > 0:
            new_change = round((curr_new - base_new_avg) / base_new_avg * 100, 1)
            if new_change > 50:
                signals.append({
                    "type": "new_customers_surge",
                    "severity": SEVERITY_SUCCESS,
                    "metric": "new_customers",
                    "current_value": curr_new,
                    "deviation_pct": new_change,
                    "title_en": f"New customer acquisition up {new_change}%",
                    "title_ar": f"اكتساب عملاء جدد زاد {new_change}%",
                    "body_en": f"{curr_new} new customers this week vs ~{round(base_new_avg)} weekly avg.",
                    "body_ar": f"{curr_new} عميل جديد هذا الأسبوع مقابل ~{round(base_new_avg)} متوسط أسبوعي.",
                    "action_en": "Convert them to repeat buyers with a follow-up email or loyalty offer.",
                    "action_ar": "حولهم لعملاء متكررين بإيميل متابعة أو عرض ولاء.",
                })

    # --- Returning customer ratio ---
    if curr_new + curr_returning > 10:
        return_pct = round(curr_returning / (curr_new + curr_returning) * 100, 1)
        if return_pct < 15:
            signals.append({
                "type": "low_retention",
                "severity": SEVERITY_INFO,
                "metric": "retention",
                "current_value": return_pct,
                "title_en": f"Only {return_pct}% returning customers",
                "title_ar": f"فقط {return_pct}% عملاء عائدين",
                "body_en": "Most of your customers are one-time buyers.",
                "body_ar": "معظم عملائك مشترين لمرة واحدة.",
                "action_en": "Implement post-purchase engagement: email sequences, loyalty rewards.",
                "action_ar": "نفذ استراتيجية تفاعل بعد الشراء: رسائل متتابعة، مكافآت ولاء.",
            })

    # --- Traffic drop (page views) ---
    curr_views = [float(r.total_page_views) for r in current_week]
    base_views = [float(r.total_page_views) for r in baseline] if baseline else []

    if base_views:
        curr_view_avg = _mean(curr_views)
        base_view_avg = _mean(base_views)
        if base_view_avg > 0:
            view_change = round(
                (curr_view_avg - base_view_avg) / base_view_avg * 100, 1
            )
            if view_change < -30:
                signals.append({
                    "type": "traffic_drop",
                    "severity": SEVERITY_WARNING,
                    "metric": "page_views",
                    "deviation_pct": view_change,
                    "title_en": f"Traffic down {abs(view_change)}%",
                    "title_ar": f"الزيارات انخفضت {abs(view_change)}%",
                    "body_en": "Significantly fewer page views than your baseline.",
                    "body_ar": "عدد مشاهدات الصفحات أقل بشكل ملحوظ من المعتاد.",
                    "action_en": "Check if marketing campaigns are running. Review SEO and social presence.",
                    "action_ar": "تحقق إن الحملات التسويقية شغالة. راجع السيو والتواجد على السوشيال.",
                })

    # --- Positive: everything looks good ---
    if not signals:
        signals.append({
            "type": "all_good",
            "severity": SEVERITY_SUCCESS,
            "title_en": "Everything looks healthy!",
            "title_ar": "كل حاجة تمام!",
            "body_en": "No anomalies detected. Your store metrics are within normal ranges.",
            "body_ar": "مفيش مشاكل مكتشفة. مقاييس متجرك في المعدل الطبيعي.",
            "action_en": "Keep up the good work. Focus on growth opportunities.",
            "action_ar": "استمر في الشغل الجيد. ركز على فرص النمو.",
        })

    return signals


# ── LLM Narrative Layer ──


async def generate_llm_narrative(
    signals: list[dict],
    metrics_summary: dict,
    lang: str = "ar",
) -> str | None:
    """Use Gemini via Google AI Studio to generate a natural-language narrative from signals.

    Returns None if LLM is unavailable or fails.
    """
    if not settings.google_ai_api_key:
        return None

    try:
        client = AsyncOpenAI(
            api_key=settings.google_ai_api_key,
            base_url=settings.google_ai_base_url,
        )

        signals_text = json.dumps(signals, ensure_ascii=False, indent=2)
        metrics_text = json.dumps(metrics_summary, ensure_ascii=False, indent=2)

        lang_instruction = (
            "Write your analysis in Egyptian Arabic (عامية مصرية). Be concise and actionable."
            if lang == "ar"
            else "Write your analysis in English. Be concise and actionable."
        )

        prompt = f"""You are an expert eCommerce analytics consultant for the Egyptian market.

Analyze the following store performance signals and metrics, then provide a brief executive summary with actionable recommendations.

{lang_instruction}

**Detected Signals:**
{signals_text}

**Store Metrics Summary (last 7 days):**
{metrics_text}

Provide your analysis as a JSON object with these keys:
- "summary": A 2-3 sentence executive summary
- "top_actions": Array of 3-5 specific, actionable recommendations (short strings)
- "outlook": One sentence outlook (positive/neutral/negative)

Return ONLY the JSON object, no markdown."""

        response = await client.chat.completions.create(
            model=settings.google_ai_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
            temperature=0.3,
        )

        content = response.choices[0].message.content
        # Strip markdown fences if present
        if content and "```" in content:
            content = content.split("```json")[-1].split("```")[0].strip()
            if not content:
                content = response.choices[0].message.content.split("```")[-2].strip()

        return content
    except Exception as e:
        logger.warning("llm_narrative_failed", error=str(e))
        return None


# ── Main entry point ──


async def generate_insights(
    rollups: Sequence[AnalyticsDailyRollupModel],
    store_currency: str = "EGP",
    lang: str = "ar",
) -> dict:
    """Generate complete insights payload.

    Returns dict with: signals, narrative (optional), generated_at
    """
    signals = detect_signals(rollups, store_currency)

    # Build metrics summary for LLM context
    sorted_rollups = sorted(rollups, key=lambda r: r.rollup_date)
    recent_7 = sorted_rollups[-7:] if len(sorted_rollups) >= 7 else sorted_rollups

    metrics_summary = {
        "days_analyzed": len(rollups),
        "last_7d_revenue": sum(r.total_revenue_cents for r in recent_7) / 100,
        "last_7d_orders": sum(r.total_orders for r in recent_7),
        "last_7d_new_customers": sum(r.new_customers for r in recent_7),
        "last_7d_returning_customers": sum(r.returning_customers for r in recent_7),
        "last_7d_page_views": sum(r.total_page_views for r in recent_7),
        "last_7d_cod_orders": sum(r.cod_orders for r in recent_7),
        "last_7d_cod_rejected": sum(r.cod_rejected for r in recent_7),
        "last_7d_refunds": sum(r.refund_count for r in recent_7),
        "last_7d_cancelled": sum(r.cancelled_orders for r in recent_7),
        "currency": store_currency,
    }

    # Generate LLM narrative (non-blocking, with fallback)
    narrative_raw = await generate_llm_narrative(signals, metrics_summary, lang)
    narrative = None
    if narrative_raw:
        try:
            narrative = json.loads(narrative_raw)
        except json.JSONDecodeError:
            narrative = {"summary": narrative_raw, "top_actions": [], "outlook": ""}

    return {
        "signals": signals,
        "narrative": narrative,
        "metrics_summary": metrics_summary,
        "generated_at": datetime.now(UTC).isoformat(),
    }
