"""Lamaa (``ai-catalog``): reads and writes of the app's own four tables, plus
the product and FAQ reads its screens need. Callers commit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services import ai_catalog_service as rules
from src.config import settings as config
from src.core.entities.product import ProductStatus
from src.infrastructure.database.models.tenant.ai_catalog import (
    AiCatalogBulkRunModel as Run,
)
from src.infrastructure.database.models.tenant.ai_catalog import (
    AiCatalogSettingsModel as Settings,
)
from src.infrastructure.database.models.tenant.ai_catalog import (
    AiCatalogSuggestionModel as Suggestion,
)
from src.infrastructure.database.models.tenant.ai_catalog import (
    AiCatalogUsageModel as Usage,
)
from src.infrastructure.database.models.tenant.metafield import (
    MetafieldDefinitionModel,
    MetafieldValueModel,
)
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.repositories.order_tracking_repository import _insert

TABLES = (Suggestion, Run, Usage, Settings)
#: Suggestions are history for 90 days (Phase K cleanup).
KEEP = timedelta(days=90)
#: A run whose items changed this recently has a live worker between two
#: claims (it re-enqueues itself after each item).
ACTIVE_WINDOW = timedelta(minutes=2)
#: A bulk item claimed this long ago lost its worker (the sweep re-queues it).
#: Longer than a provider call can take with every retry and the repair
#: (about 11 minutes: 45 s per try), so a slow item is never handed to a
#: second worker while the first still has it.
CLAIM_TIMEOUT = timedelta(minutes=15)


# ─── Settings ──────────────────────────────────────────────────────────


async def get_settings(db: AsyncSession, store_id: UUID) -> Settings | None:
    return await db.get(Settings, store_id)


async def ensure_settings(
    db: AsyncSession, store_id: UUID, tenant_id: UUID
) -> Settings:
    """The store's row, created with the defaults; two first calls at once
    share one row."""
    await db.execute(
        _insert(db, Settings)
        .values(
            store_id=store_id,
            tenant_id=tenant_id,
            arabic_style="egyptian",
            languages=["ar", "en"],
            store_facts="",
            brand_voice="",
        )
        .on_conflict_do_nothing(index_elements=["store_id"])
    )
    row = await db.get(Settings, store_id, populate_existing=True)
    assert row is not None
    return row


def run_cap(row: Settings | None) -> int:
    return (
        row.run_cap
        if row and row.run_cap is not None
        else config.ai_catalog_free_monthly_runs
    )


# ─── Usage (AIC-I-07) ──────────────────────────────────────────────────


async def reserve(
    db: AsyncSession, store_id: UUID, tenant_id: UUID, cap: int, *, runs: int = 1
) -> bool:
    """Take ``runs`` from this month's allowance, atomically; ``False`` when
    the store would pass its cap. Parallel callers can never pass it."""
    month = rules.month_key()
    await db.execute(
        _insert(db, Usage)
        .values(
            store_id=store_id,
            month=month,
            tenant_id=tenant_id,
            runs_used=0,
            cost_micros=0,
        )
        .on_conflict_do_nothing(index_elements=["store_id", "month"])
    )
    taken = await db.scalar(
        update(Usage)
        .where(
            Usage.store_id == store_id,
            Usage.month == month,
            Usage.runs_used + runs <= cap,
        )
        .values(runs_used=Usage.runs_used + runs)
        .returning(Usage.runs_used)
    )
    return taken is not None


async def refund(db: AsyncSession, store_id: UUID, *, runs: int = 1) -> None:
    await db.execute(
        update(Usage)
        .where(
            Usage.store_id == store_id,
            Usage.month == rules.month_key(),
            Usage.runs_used >= runs,
        )
        .values(runs_used=Usage.runs_used - runs)
    )


async def add_cost(db: AsyncSession, store_id: UUID, micros: int) -> None:
    if micros:
        await db.execute(
            update(Usage)
            .where(Usage.store_id == store_id, Usage.month == rules.month_key())
            .values(cost_micros=Usage.cost_micros + micros)
        )


async def usage(db: AsyncSession, store_id: UUID) -> tuple[int, int]:
    row = await db.get(Usage, (store_id, rules.month_key()))
    return (row.runs_used, row.cost_micros) if row else (0, 0)


async def budget_left() -> bool:
    """Whether every store together is under this month's budget. Reads all
    stores in its own short session, outside the request's tenant scope."""
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.tenancy.rls import enable_rls_bypass

    async with AsyncSessionLocal() as s:
        await enable_rls_bypass(s)
        spent = await s.scalar(
            select(func.coalesce(func.sum(Usage.cost_micros), 0)).where(
                Usage.month == rules.month_key()
            )
        )
    return int(spent or 0) < config.ai_catalog_monthly_budget_usd * 1_000_000


# ─── Products and their FAQs ───────────────────────────────────────────


def _products(store_id: UUID) -> Select:
    return select(ProductModel).where(
        ProductModel.store_id == store_id, ProductModel.status != ProductStatus.ARCHIVED
    )


async def products(db: AsyncSession, store_id: UUID) -> list[ProductModel]:
    return list(
        (
            await db.execute(_products(store_id).order_by(ProductModel.created_at))
        ).scalars()
    )


async def product(
    db: AsyncSession, store_id: UUID, product_id: UUID, *, lock: bool = False
) -> ProductModel | None:
    stmt = select(ProductModel).where(
        ProductModel.store_id == store_id, ProductModel.id == product_id
    )
    if lock:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def faq_definition(
    db: AsyncSession, store_id: UUID
) -> MetafieldDefinitionModel | None:
    return (
        await db.execute(
            select(MetafieldDefinitionModel).where(
                MetafieldDefinitionModel.store_id == store_id,
                MetafieldDefinitionModel.owner_type == "product",
                MetafieldDefinitionModel.namespace == "faq",
                MetafieldDefinitionModel.key == "items",
            )
        )
    ).scalar_one_or_none()


async def faq_values(
    db: AsyncSession, store_id: UUID, product_ids: list[UUID] | None = None
) -> dict[UUID, Any]:
    """``faq.items`` per product, in one query."""
    definition = await faq_definition(db, store_id)
    if definition is None:
        return {}
    stmt = select(MetafieldValueModel.owner_id, MetafieldValueModel.value).where(
        MetafieldValueModel.definition_id == definition.id
    )
    if product_ids is not None:
        stmt = stmt.where(MetafieldValueModel.owner_id.in_(product_ids))
    return dict((await db.execute(stmt)).all())


# ─── Suggestions ───────────────────────────────────────────────────────


async def suggestion(
    db: AsyncSession, store_id: UUID, suggestion_id: UUID, *, lock: bool = False
) -> Suggestion | None:
    stmt = select(Suggestion).where(
        Suggestion.store_id == store_id, Suggestion.id == suggestion_id
    )
    if lock:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def latest(
    db: AsyncSession,
    store_id: UUID,
    product_id: UUID,
    *,
    kind: str = "catalog",
    status: tuple[str, ...] | None = None,
) -> Suggestion | None:
    stmt = select(Suggestion).where(
        Suggestion.store_id == store_id,
        Suggestion.product_id == product_id,
        Suggestion.kind == kind,
    )
    if status:
        stmt = stmt.where(Suggestion.status.in_(status))
    # Applied rows by when they were applied, as History lists them: a bulk
    # item made earlier can be applied later than a single improve.
    newest = func.coalesce(Suggestion.applied_at, Suggestion.created_at)
    return (
        await db.execute(stmt.order_by(newest.desc()).limit(1))
    ).scalar_one_or_none()


async def history(
    db: AsyncSession, store_id: UUID, *, page: int, per_page: int = 30
) -> tuple[list[Suggestion], int]:
    base = select(Suggestion).where(
        Suggestion.store_id == store_id,
        Suggestion.status.in_(("applied", "undone", "failed_verify")),
    )
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (
        await db.execute(
            base.order_by(
                Suggestion.applied_at.desc().nulls_last(), Suggestion.created_at.desc()
            )
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).scalars()
    return list(rows), int(total)


async def improved_this_month(
    db: AsyncSession, store_id: UUID
) -> tuple[int, list[tuple[int, int]]]:
    """Products with an apply this month, and their scores before and after."""
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = (
        await db.execute(
            select(Suggestion.product_id, Suggestion.applied).where(
                Suggestion.store_id == store_id,
                Suggestion.status == "applied",
                Suggestion.applied_at >= start,
            )
        )
    ).all()
    pairs = [
        (int(a.get("score_before", 0)), int(a.get("score_after", 0)))
        for _, a in rows
        if isinstance(a, dict) and "score_after" in a
    ]
    return len({pid for pid, _ in rows}), pairs


# ─── Bulk runs ─────────────────────────────────────────────────────────


async def run(db: AsyncSession, store_id: UUID, run_id: UUID) -> Run | None:
    return (
        await db.execute(select(Run).where(Run.store_id == store_id, Run.id == run_id))
    ).scalar_one_or_none()


async def runs(db: AsyncSession, store_id: UUID, *, limit: int = 10) -> list[Run]:
    return list(
        (
            await db.execute(
                select(Run)
                .where(Run.store_id == store_id)
                .order_by(Run.created_at.desc())
                .limit(limit)
            )
        ).scalars()
    )


async def run_counts(db: AsyncSession, run_id: UUID) -> dict[str, int]:
    rows = (
        await db.execute(
            select(Suggestion.status, func.count())
            .where(Suggestion.bulk_run_id == run_id)
            .group_by(Suggestion.status)
        )
    ).all()
    return {status: int(n) for status, n in rows}


async def run_items(db: AsyncSession, run_id: UUID) -> list[Suggestion]:
    return list(
        (
            await db.execute(
                select(Suggestion)
                .where(Suggestion.bulk_run_id == run_id)
                .order_by(Suggestion.created_at)
            )
        ).scalars()
    )


async def claim(db: AsyncSession, run_id: UUID) -> Suggestion | None:
    """One queued item of the run, locked for this worker alone."""
    row = (
        await db.execute(
            select(Suggestion)
            .where(Suggestion.bulk_run_id == run_id, Suggestion.status == "queued")
            .order_by(Suggestion.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if row is not None:
        row.status = "running"
        row.claimed_at = datetime.now(UTC)
    return row


async def stalled_runs(db: AsyncSession) -> list[tuple[UUID, UUID]]:
    """(run, store) of running runs with queued work and no live claim:
    their worker message was lost (prod Redis is allkeys-lru)."""
    now = datetime.now(UTC)
    cutoff = now - CLAIM_TIMEOUT
    # Runs with a worker: an item claimed within the timeout, or any item
    # written in the last two minutes (a worker between two claims). Read
    # before the re-queue below, which touches updated_at itself.
    live = list(
        (
            await db.execute(
                select(Suggestion.bulk_run_id)
                .where(
                    Suggestion.bulk_run_id.is_not(None),
                    or_(
                        and_(
                            Suggestion.status == "running",
                            Suggestion.claimed_at >= cutoff,
                        ),
                        Suggestion.updated_at >= now - ACTIVE_WINDOW,
                    ),
                )
                .distinct()
            )
        ).scalars()
    )
    # Items a dead worker claimed go back to the queue.
    await db.execute(
        update(Suggestion)
        .where(
            Suggestion.status == "running",
            Suggestion.bulk_run_id.is_not(None),
            Suggestion.claimed_at < cutoff,
        )
        .values(status="queued", claimed_at=None)
    )
    rows = (
        await db.execute(
            select(Run.id, Run.store_id)
            .join(Suggestion, Suggestion.bulk_run_id == Run.id)
            .where(
                Run.status == "running",
                Suggestion.status == "queued",
                Run.id.not_in(live),
            )
            .distinct()
        )
    ).all()
    return [(r, s) for r, s in rows]


# ─── Uninstall, cleanup, data rights ───────────────────────────────────


async def purge_store(db: AsyncSession, store_id: UUID) -> None:
    """The app's rows only; product copy, alt text and FAQs the merchant
    applied are the store's own data and stay."""
    for model in TABLES:
        await db.execute(delete(model).where(model.store_id == store_id))


async def cleanup(db: AsyncSession, *, now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(UTC)) - KEEP
    result = await db.execute(delete(Suggestion).where(Suggestion.created_at < cutoff))
    return int(result.rowcount or 0)
