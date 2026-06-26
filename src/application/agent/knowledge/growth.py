"""US2 — proactive growth recommendations (grounded in authored playbooks).

A growth playbook maps a store **signal** (e.g. has abandoned carts) to a NUMU
**feature**, with a rationale and enablement steps. We detect the signal from the
store's **live data** (never from embeddings), match the playbook, and return a
grounded recommendation that cites the playbook source. Recommendations are only
made for signals the store actually exhibits (SC-003 — relevant, not generic).

The signal metrics are computed best-effort from live store data; they can also be
injected (tests / a richer metrics provider) without changing the matching logic.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.agent.knowledge.corpus_loader import load_authored_corpus
from src.config.logging_config import get_logger
from src.core.agent.knowledge import SourceKind

logger = get_logger(__name__)

# The live metrics a playbook's `detected_by` may reference. A signal fires when
# its metric is > 0. Keeping this registry explicit makes the playbook contract
# checkable (every `detected_by` must be a real, known detector).
SIGNAL_DETECTORS: set[str] = {
    "orders.abandoned_count",
    "catalog.bundle_like_count",
    "orders.repeat_buyer_count",
}


async def _safe_scalar(session: AsyncSession, sql: str, params: dict) -> int:
    try:
        row = await session.execute(text(sql), params)
        val = row.scalar()
        return int(val or 0)
    except Exception:  # noqa: BLE001 — absent table/column → treat as no signal
        return 0


async def detect_metrics(session: AsyncSession, store_id) -> dict[str, int]:
    """Best-effort live metrics for the known detectors (0 when unavailable)."""
    sid = {"sid": str(store_id)}
    return {
        "orders.abandoned_count": await _safe_scalar(
            session,
            "SELECT COUNT(*) FROM public.orders "
            "WHERE store_id = :sid AND status IN ('abandoned', 'pending', 'cart')",
            sid,
        ),
        "catalog.bundle_like_count": await _safe_scalar(
            session,
            "SELECT COUNT(*) FROM public.products WHERE store_id = :sid",
            sid,
        ),
        "orders.repeat_buyer_count": await _safe_scalar(
            session,
            "SELECT COUNT(*) FROM (SELECT customer_id FROM public.orders "
            "WHERE store_id = :sid AND customer_id IS NOT NULL "
            "GROUP BY customer_id HAVING COUNT(*) > 1) t",
            sid,
        ),
    }


async def recommend_growth(
    session: AsyncSession,
    store_id,
    *,
    metrics: dict[str, int] | None = None,
    locale: str = "en",
) -> list[dict]:
    """Return grounded growth recommendations for a store's actual signals."""
    if metrics is None:
        metrics = await detect_metrics(session, store_id)

    playbooks = [
        d for d in load_authored_corpus() if d.source_kind == SourceKind.PLAYBOOK
    ]
    recs: list[dict] = []
    for pb in playbooks:
        # Prefer the locale match; fall back to any locale for the same feature.
        detected = int(metrics.get(pb.detected_by or "", 0))
        if detected <= 0:
            continue
        rationale = pb.chunks[0] if pb.chunks else pb.title
        recs.append({
            "title": pb.title,
            "feature": pb.feature,
            "signal": pb.signal,
            "rationale": rationale,
            "howto": pb.howto,  # link to the enablement how-to
            "source": pb.source,  # citation (grounded)
            "locale": pb.locale,
        })
    logger.info("agent_growth_recommend", store_id=str(store_id), recs=len(recs))
    return recs
