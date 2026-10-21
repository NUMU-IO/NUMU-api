"""``/api/v1/apps/ai-catalog/…``: the merchant screens of «لمعة» (Lamaa).

Called by the app's front inside the hub with the hub's session token; the
store always comes from the token (``AppSession.store_id``), never from the
request. Nothing reaches a product until the merchant accepts it field by
field. docs/Plans/APPS/04-ai-catalog.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.app_session import (
    AppSession,
    app_router,
    require_app_session,
)
from src.api.dependencies.database import get_db
from src.application.services import ai_catalog_actions as actions
from src.application.services import ai_catalog_generate as gen
from src.application.services import ai_catalog_service as rules
from src.core.entities.store import Store
from src.infrastructure.database.connection import commit_and_restore_rls
from src.infrastructure.database.models.tenant.ai_catalog import (
    AiCatalogSuggestionModel as Suggestion,
)
from src.infrastructure.repositories import ai_catalog_repository as repo
from src.infrastructure.repositories.store_repository import StoreRepository

router = app_router(rules.SLUG)
Session = Annotated[AppSession, Depends(require_app_session(rules.SLUG))]
Db = Annotated[AsyncSession, Depends(get_db)]
PER_PAGE = 50
WEAK = 60
#: Products a bulk run takes at most; more waits for the next run.
BULK_MAX = 100

#: ActionError code → HTTP status the front reads it from.
STATUS = {
    "gone": 404,
    "allowance": 429,
    "budget": 429,
    "paused": 503,
    "not_configured": 503,
    "busy": 503,
    "rejected": 502,
    "invalid": 502,
}

Group = Literal["name", "description", "seo", "alts", "faq", "slug"]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _store(db: AsyncSession, session: AppSession) -> Store:
    store = await StoreRepository(db).get_by_id(session.store_id)
    if store is None:
        raise HTTPException(status_code=404, detail="Store not found")
    return store


def _fail(exc: actions.ActionError) -> HTTPException:
    return HTTPException(
        status_code=STATUS.get(exc.code, 409),
        detail={"code": exc.code, "fields": exc.fields},
    )


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _suggestion_out(s: Suggestion | None) -> dict | None:
    if s is None:
        return None
    return {
        "id": str(s.id),
        "kind": s.kind,
        "status": s.status,
        "fields": s.fields,
        "current": s.current,
        "suggested": s.suggested,
        "applied": s.applied,
        "error_code": s.error_code,
        "created_at": _iso(s.created_at),
        "applied_at": _iso(s.applied_at),
    }


def _social_out(s: Suggestion | None, store: Store, slug: str | None) -> dict | None:
    """A post pack with the product's link, which the posts never contain."""
    out = _suggestion_out(s)
    if out is not None:
        out["link"] = f"{store.store_url}/products/{slug}" if slug else store.store_url
    return out


def _card(p: Any, snap: dict[str, Any], score: int, passed: dict[str, bool]) -> dict:
    return {
        "id": str(p.id),
        "name": snap["name"] or snap["name_ar"],
        "name_ar": snap["name_ar"],
        "image": (p.images or [None])[0],
        "status": str(getattr(p.status, "value", p.status)),
        "score": score,
        "failing": [name for name, ok in passed.items() if not ok],
    }


async def _scored(db: AsyncSession, store_id: UUID) -> list[dict]:
    """Every live product with its score, computed from NUMU's own rows on
    each request (README "No score table")."""
    rows = await repo.products(db, store_id)
    faqs = await repo.faq_values(db, store_id)
    out = []
    for p in rows:
        snap = rules.snapshot(p, faqs.get(p.id))
        score, passed = rules.score(snap)
        out.append(_card(p, snap, score, passed))
    return out


# ─── Settings ──────────────────────────────────────────────────────────


def _settings_out(row: Any, used: int) -> dict:
    return {
        "arabic_style": row.arabic_style,
        "languages": row.languages,
        "store_facts": row.store_facts,
        "brand_voice": row.brand_voice,
        "allowance": {"used": used, "cap": repo.run_cap(row)},
    }


@router.get("/settings")
async def get_settings(session: Session, db: Db) -> dict:
    store = await _store(db, session)
    row = await repo.ensure_settings(db, store.id, store.tenant_id)
    used, _ = await repo.usage(db, store.id)
    return _settings_out(row, used)


class SettingsBody(Body):
    arabic_style: Literal["egyptian", "formal"] | None = None
    languages: list[Literal["ar", "en"]] | None = Field(
        default=None, min_length=1, max_length=2
    )
    store_facts: str | None = Field(default=None, max_length=600)
    brand_voice: str | None = Field(default=None, max_length=300)


@router.put("/settings")
async def put_settings(body: SettingsBody, session: Session, db: Db) -> dict:
    store = await _store(db, session)
    row = await repo.ensure_settings(db, store.id, store.tenant_id)
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(
            row,
            key,
            sorted(set(value))
            if key == "languages"
            else value.strip()
            if isinstance(value, str)
            else value,
        )
    used, _ = await repo.usage(db, store.id)
    return _settings_out(row, used)


# ─── Overview and products (Phase G) ───────────────────────────────────


@router.get("/overview")
async def overview(session: Session, db: Db) -> dict:
    store = await _store(db, session)
    cards = await _scored(db, store.id)
    row = await repo.ensure_settings(db, store.id, store.tenant_id)
    used, _ = await repo.usage(db, store.id)
    improved, pairs = await repo.improved_this_month(db, store.id)
    failing: dict[str, int] = {name: 0 for name, _ in rules.CHECKS}
    for c in cards:
        for name in c["failing"]:
            failing[name] += 1
    return {
        "products": len(cards),
        "average_score": round(sum(c["score"] for c in cards) / len(cards))
        if cards
        else None,
        "weak": sum(1 for c in cards if c["score"] < WEAK),
        "failing": failing,
        "improved_this_month": improved,
        "score_before": round(sum(b for b, _ in pairs) / len(pairs)) if pairs else None,
        "score_after": round(sum(a for _, a in pairs) / len(pairs)) if pairs else None,
        "allowance": {"used": used, "cap": repo.run_cap(row)},
        "paused": await gen.paused() or not await repo.budget_left(),
        "weakest": sorted(cards, key=lambda c: c["score"])[:5],
    }


@router.get("/products")
async def list_products(
    session: Session,
    db: Db,
    show: Annotated[str, Query(pattern=r"^(all|weak|missing:[a-z_]+)$")] = "all",
    q: Annotated[str | None, Query(max_length=100)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
) -> dict:
    cards = await _scored(db, session.store_id)
    if show == "weak":
        cards = [c for c in cards if c["score"] < WEAK]
    elif show.startswith("missing:"):
        check = show.split(":", 1)[1]
        cards = [c for c in cards if check in c["failing"]]
    if q:
        needle = q.strip().lower()
        cards = [
            c
            for c in cards
            if needle in (c["name"] or "").lower()
            or needle in (c["name_ar"] or "").lower()
        ]
    cards.sort(key=lambda c: (c["score"], c["name"] or ""))
    start = (page - 1) * PER_PAGE
    return {
        "items": cards[start : start + PER_PAGE],
        "total": len(cards),
        "page": page,
        "per_page": PER_PAGE,
    }


@router.get("/products/{product_id}")
async def get_product(product_id: UUID, session: Session, db: Db) -> dict:
    found = await actions.read(db, session.store_id, product_id)
    if found is None:
        raise HTTPException(status_code=404, detail={"code": "gone", "fields": []})
    entity, snap = found
    store = await _store(db, session)
    score, passed = rules.score(snap)
    pending = await repo.latest(
        db, session.store_id, product_id, status=("ready", "running", "failed")
    )
    social = await repo.latest(
        db, session.store_id, product_id, kind="social", status=("ready",)
    )
    applied = await repo.latest(db, session.store_id, product_id, status=("applied",))
    return {
        "product": {
            "id": str(entity.id),
            "slug": entity.slug,
            "status": str(getattr(entity.status, "value", entity.status)),
            "images": list(entity.images or []),
        },
        "current": snap,
        "score": score,
        "checks": passed,
        "suggestion": _suggestion_out(pending),
        "social": _social_out(social, store, entity.slug),
        "can_undo": applied is not None,
    }


# ─── Improve, apply, undo (Phase H, I) ─────────────────────────────────


class GenerateBody(Body):
    groups: list[Group] = Field(min_length=1, max_length=6)


@router.post("/products/{product_id}/generate")
async def generate(
    product_id: UUID, body: GenerateBody, session: Session, db: Db
) -> dict:
    store = await _store(db, session)
    try:
        row = await actions.generate(
            db,
            store,
            product_id,
            list(dict.fromkeys(body.groups)),
            commit=commit_and_restore_rls,
        )
    except actions.ActionError as exc:
        raise _fail(exc) from exc
    return _suggestion_out(row)


Key = Literal[
    "name",
    "name_ar",
    "description",
    "description_ar",
    "seo_title",
    "seo_title_ar",
    "seo_description",
    "seo_description_ar",
    "alts",
    "faq",
    "slug",
]


class ApplyBody(Body):
    fields: list[Key] = Field(min_length=1, max_length=11)
    edits: dict[Key, Any] = Field(default_factory=dict)


@router.post("/suggestions/{suggestion_id}/apply")
async def apply(suggestion_id: UUID, body: ApplyBody, session: Session, db: Db) -> dict:
    store = await _store(db, session)
    try:
        return await actions.apply(
            db,
            store,
            suggestion_id,
            list(dict.fromkeys(body.fields)),
            user_id=session.user_id,
            commit=commit_and_restore_rls,
            edits=body.edits,
        )
    except actions.ActionError as exc:
        raise _fail(exc) from exc


@router.post("/suggestions/{suggestion_id}/dismiss")
async def dismiss(suggestion_id: UUID, session: Session, db: Db) -> dict:
    row = await repo.suggestion(db, session.store_id, suggestion_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "gone", "fields": []})
    if row.status in ("ready", "failed"):
        row.status = "dismissed"
    return {"status": row.status}


@router.post("/products/{product_id}/undo")
async def undo(product_id: UUID, session: Session, db: Db) -> dict:
    store = await _store(db, session)
    try:
        return await actions.undo(db, store, product_id, commit=commit_and_restore_rls)
    except actions.ActionError as exc:
        raise _fail(exc) from exc


@router.get("/history")
async def history(
    session: Session, db: Db, page: Annotated[int, Query(ge=1)] = 1
) -> dict:
    rows, total = await repo.history(db, session.store_id, page=page)
    names = {}
    for r in rows:
        if r.product_id not in names:
            p = await repo.product(db, session.store_id, r.product_id)
            names[r.product_id] = (
                p.name if p else "",
                (p.images or [None])[0] if p else None,
            )
    items = []
    for r in rows:
        applied = r.applied or {}
        items.append({
            "id": str(r.id),
            "product": {
                "id": str(r.product_id),
                "name": names[r.product_id][0],
                "image": names[r.product_id][1],
            },
            "status": r.status,
            "fields": [k for k in applied if k not in ("score_before", "score_after")],
            "score_before": applied.get("score_before"),
            "score_after": applied.get("score_after"),
            "applied_at": _iso(r.applied_at),
        })
    return {"items": items, "total": total, "page": page}


# ─── Social copy, usage (Phase K) ──────────────────────────────────────


@router.post("/products/{product_id}/social")
async def social(product_id: UUID, session: Session, db: Db) -> dict:
    store = await _store(db, session)
    try:
        row = await actions.social(db, store, product_id, commit=commit_and_restore_rls)
    except actions.ActionError as exc:
        raise _fail(exc) from exc
    p = await repo.product(db, store.id, product_id)
    return _social_out(row, store, p.slug if p else None)


@router.get("/usage")
async def usage(session: Session, db: Db) -> dict:
    store = await _store(db, session)
    row = await repo.ensure_settings(db, store.id, store.tenant_id)
    used, _ = await repo.usage(db, store.id)
    return {
        "month": rules.month_key(),
        "used": used,
        "cap": repo.run_cap(row),
        "paused": await gen.paused(),
    }


# ─── Bulk (Phase J) ────────────────────────────────────────────────────


def _run_out(run: Any, counts: dict[str, int]) -> dict:
    return {
        "id": str(run.id),
        "status": run.status,
        "fields": run.fields,
        "mode": run.mode,
        "total": run.total,
        "counts": counts,
        "created_at": _iso(run.created_at),
    }


class BulkBody(Body):
    groups: list[Group] = Field(min_length=1, max_length=6)
    mode: Literal["fill_empty", "rewrite"] = "fill_empty"
    product_ids: list[UUID] = Field(min_length=1, max_length=BULK_MAX)


@router.post("/bulk")
async def start_bulk(body: BulkBody, session: Session, db: Db) -> dict:
    from src.infrastructure.database.models.tenant.ai_catalog import (
        AiCatalogBulkRunModel as Run,
    )
    from src.infrastructure.messaging.tasks import ai_catalog_tasks as tasks

    store = await _store(db, session)
    settings_row = await repo.ensure_settings(db, store.id, store.tenant_id)
    groups = list(dict.fromkeys(body.groups))
    keys = rules.keys_for(groups, list(settings_row.languages))
    ids = list(dict.fromkeys(body.product_ids))
    mine = {p.id for p in await repo.products(db, store.id) if p.id in set(ids)}
    ids = [i for i in ids if i in mine]
    if not ids or not keys:
        raise HTTPException(status_code=422, detail={"code": "nothing", "fields": []})
    if not await repo.budget_left():
        raise HTTPException(status_code=429, detail={"code": "budget", "fields": []})
    # The whole run is taken from the allowance now; failed items refund.
    if not await repo.reserve(
        db, store.id, store.tenant_id, repo.run_cap(settings_row), runs=len(ids)
    ):
        raise HTTPException(status_code=429, detail={"code": "allowance", "fields": []})
    run = Run(
        store_id=store.id,
        tenant_id=store.tenant_id,
        fields=groups,
        mode=body.mode,
        total=len(ids),
        status="running",
        created_by=session.user_id,
    )
    db.add(run)
    await db.flush()
    db.add_all(
        Suggestion(
            store_id=store.id,
            tenant_id=store.tenant_id,
            product_id=pid,
            bulk_run_id=run.id,
            kind="catalog",
            status="queued",
            fields=keys,
        )
        for pid in ids
    )
    await commit_and_restore_rls(db)
    tasks.start_workers(run.id, store.id)
    return _run_out(run, {"queued": len(ids)})


@router.get("/bulk")
async def list_bulk(session: Session, db: Db) -> dict:
    rows = await repo.runs(db, session.store_id)
    return {"items": [_run_out(r, await repo.run_counts(db, r.id)) for r in rows]}


@router.get("/bulk/{run_id}")
async def get_bulk(run_id: UUID, session: Session, db: Db) -> dict:
    run = await repo.run(db, session.store_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "gone", "fields": []})
    items = await repo.run_items(db, run.id)
    names = {p.id: p for p in await repo.products(db, session.store_id)}
    out = _run_out(run, await repo.run_counts(db, run.id))
    out["items"] = [
        {
            **_suggestion_out(s),
            "product": {
                "id": str(s.product_id),
                "name": names[s.product_id].name if s.product_id in names else "",
                "image": (names[s.product_id].images or [None])[0]
                if s.product_id in names
                else None,
            },
        }
        for s in items
    ]
    return out


class BulkApplyBody(Body):
    suggestion_ids: list[UUID] | None = Field(default=None, max_length=BULK_MAX)


@router.post("/bulk/{run_id}/apply")
async def apply_bulk(
    run_id: UUID, body: BulkApplyBody, session: Session, db: Db
) -> dict:
    from src.infrastructure.messaging.tasks import ai_catalog_tasks as tasks

    run = await repo.run(db, session.store_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "gone", "fields": []})
    if run.status not in ("review", "running"):
        raise HTTPException(status_code=409, detail={"code": "not_ready", "fields": []})
    run.status = "applying"
    await commit_and_restore_rls(db)
    tasks.apply_task.delay(
        str(run.id),
        str(session.store_id),
        [str(i) for i in body.suggestion_ids] if body.suggestion_ids else None,
        str(session.user_id),
    )
    return _run_out(run, await repo.run_counts(db, run.id))


@router.post("/bulk/{run_id}/cancel")
async def cancel_bulk(run_id: UUID, session: Session, db: Db) -> dict:
    run = await repo.run(db, session.store_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "gone", "fields": []})
    if run.status in ("running", "review"):
        run.status = "cancelled"
        refund = 0
        for s in await repo.run_items(db, run.id):
            if s.status == "queued":
                s.status = "dismissed"
                refund += 1
        if refund:
            await repo.refund(db, run.store_id, runs=refund)
    return _run_out(run, await repo.run_counts(db, run.id))
