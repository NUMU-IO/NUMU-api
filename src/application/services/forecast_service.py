"""Sales forecasting using statsmodels Holt-Winters exponential smoothing.

Uses the battle-tested ExponentialSmoothing from statsmodels which handles:
- Parameter optimization (auto alpha/beta/gamma)
- Seasonal decomposition (when enough data)
- Convergence issues and numerical stability
- Proper confidence intervals

Edge cases:
- <14 days data → insufficient, return empty
- All-zero data → zero forecast
- <28 days → additive trend only (no seasonality)
- ≥28 days → additive trend + weekly seasonality (period=7)
- Forecast goes negative → clamped to 0
- Gaps in dates → interpolated with 0
- Flat series (zero variance) → returns mean-based forecast
"""

import logging
import math
from collections.abc import Sequence
from datetime import timedelta

import numpy as np

from src.infrastructure.database.models.tenant.analytics_rollup import (
    AnalyticsDailyRollupModel,
)

logger = logging.getLogger(__name__)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def _run_holt_winters(
    series: np.ndarray,
    horizon: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Run Holt-Winters and return (predicted, lower, upper) arrays.

    Automatically selects:
    - Additive trend + weekly seasonality if ≥28 days (4 full weeks)
    - Additive trend only if <28 days
    - Simple exponential smoothing if trend fails

    Falls back gracefully on any convergence error.
    """
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    n = len(series)
    predicted = np.zeros(horizon)
    lower = np.zeros(horizon)
    upper = np.zeros(horizon)

    # Check for zero-variance (flat or all-zero series)
    if np.std(series) < 1e-6:
        mean_val = float(np.mean(series))
        predicted[:] = max(0, mean_val)
        lower[:] = max(0, mean_val * 0.8)
        upper[:] = mean_val * 1.2
        return predicted, lower, upper

    fit = None

    # Try 1: Trend + weekly seasonality (needs ≥28 days = 4 full weeks)
    if n >= 28:
        try:
            model = ExponentialSmoothing(
                series,
                trend="add",
                seasonal="add",
                seasonal_periods=7,
                initialization_method="estimated",
            )
            fit = model.fit(optimized=True, use_brute=True)
        except Exception:
            logger.debug("holt_winters_seasonal_failed, falling back to trend-only")
            fit = None

    # Try 2: Trend only (no seasonality)
    if fit is None:
        try:
            model = ExponentialSmoothing(
                series,
                trend="add",
                seasonal=None,
                initialization_method="estimated",
            )
            fit = model.fit(optimized=True, use_brute=True)
        except Exception:
            logger.debug("holt_winters_trend_failed, falling back to simple")
            fit = None

    # Try 3: Simple exponential smoothing (no trend, no seasonality)
    if fit is None:
        try:
            model = ExponentialSmoothing(
                series,
                trend=None,
                seasonal=None,
                initialization_method="estimated",
            )
            fit = model.fit(optimized=True)
        except Exception:
            logger.warning("all_smoothing_methods_failed, using mean fallback")
            mean_val = float(np.mean(series[-14:]))
            std_val = float(np.std(series[-14:]))
            predicted[:] = max(0, mean_val)
            for h in range(horizon):
                interval = 1.96 * std_val * math.sqrt(h + 1)
                lower[h] = max(0, mean_val - interval)
                upper[h] = mean_val + interval
            return predicted, lower, upper

    # Generate forecast
    forecast = fit.forecast(horizon)

    # Calculate prediction intervals from in-sample residuals
    residuals = series - fit.fittedvalues
    residual_std = float(np.std(residuals))

    for h in range(horizon):
        pred = (
            float(forecast.iloc[h]) if hasattr(forecast, "iloc") else float(forecast[h])
        )
        interval = 1.96 * residual_std * math.sqrt(h + 1)
        predicted[h] = max(0, pred)
        lower[h] = max(0, pred - interval)
        upper[h] = pred + interval

    return predicted, lower, upper


def generate_forecast(
    rollups: Sequence[AnalyticsDailyRollupModel],
    horizon: int = 30,
) -> dict:
    """Generate revenue forecast from rollup data.

    Returns dict with:
    - historical: [{date, revenue, orders}]
    - forecast: [{date, predicted, lower, upper}]
    - metadata: status, stats, trend direction
    """
    sorted_rollups = sorted(rollups, key=lambda r: r.rollup_date)

    if len(sorted_rollups) < 14:
        return {
            "historical": [],
            "forecast": [],
            "metadata": {
                "status": "insufficient_data",
                "days_available": len(sorted_rollups),
                "days_required": 14,
                "message_en": f"Need at least 14 days of data. You have {len(sorted_rollups)}.",
                "message_ar": f"محتاجين ١٤ يوم بيانات على الأقل. عندك {len(sorted_rollups)}.",
            },
        }

    # Build gap-filled daily series
    first_date = sorted_rollups[0].rollup_date
    last_date = sorted_rollups[-1].rollup_date
    rollup_map = {r.rollup_date: r for r in sorted_rollups}

    historical = []
    revenue_series: list[float] = []
    current = first_date
    while current <= last_date:
        r = rollup_map.get(current)
        revenue = float(r.total_revenue_cents) if r else 0.0
        orders = r.total_orders if r else 0
        historical.append({
            "date": current.isoformat(),
            "revenue": round(revenue),
            "orders": orders,
        })
        revenue_series.append(revenue)
        current += timedelta(days=1)

    # All zeros check
    if all(v == 0 for v in revenue_series):
        return {
            "historical": historical,
            "forecast": [
                {
                    "date": (last_date + timedelta(days=i + 1)).isoformat(),
                    "predicted": 0,
                    "lower": 0,
                    "upper": 0,
                }
                for i in range(horizon)
            ],
            "metadata": {
                "status": "no_revenue",
                "days_available": len(revenue_series),
                "message_en": "No revenue recorded yet. Forecast will appear after your first sales.",
                "message_ar": "مفيش إيرادات مسجلة. التوقعات هتظهر بعد أول عملية بيع.",
            },
        }

    # Run statsmodels Holt-Winters
    series = np.array(revenue_series, dtype=float)
    predicted, lower_arr, upper_arr = _run_holt_winters(series, horizon)

    # Build forecast output
    forecast = []
    for i in range(horizon):
        forecast_date = last_date + timedelta(days=i + 1)
        forecast.append({
            "date": forecast_date.isoformat(),
            "predicted": round(float(predicted[i])),
            "lower": round(float(lower_arr[i])),
            "upper": round(float(upper_arr[i])),
        })

    # Metadata
    recent_7 = revenue_series[-7:]
    avg_daily = _mean(recent_7)
    total_predicted = sum(f["predicted"] for f in forecast)
    forecast_avg = total_predicted / horizon if horizon > 0 else 0

    if forecast_avg > avg_daily * 1.05:
        trend = "up"
    elif forecast_avg < avg_daily * 0.95:
        trend = "down"
    else:
        trend = "stable"

    return {
        "historical": historical,
        "forecast": forecast,
        "metadata": {
            "status": "ok",
            "days_available": len(revenue_series),
            "horizon_days": horizon,
            "method": "holt_winters_seasonal"
            if len(revenue_series) >= 28
            else "holt_winters_trend",
            "avg_daily_revenue_7d": round(avg_daily),
            "forecast_total": total_predicted,
            "forecast_daily_avg": round(forecast_avg),
            "trend": trend,
        },
    }
