"""Lamaa: write a suggestion, apply it to NUMU, undo it. Shared by the
merchant routes and the bulk tasks.

Writes follow README "Writing to NUMU without losing data": the product row
is locked; only name, description, slug, the SEO pair and ``attributes``
reach ``UpdateProductUseCase`` (``attributes`` as the fresh copy plus the
accepted Arabic keys); a field changed since the suggestion was made is
refused; the result is re-read and checked; caches are busted only after the
commit. No transaction stays open during a provider call.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.application.dto.product import UpdateProductDTO
from src.application.services import ai_catalog_generate as gen
from src.application.services import ai_catalog_service as rules
from src.application.services import metafield_values
from src.application.use_cases.products.update_product import (
    SlugTakenError,
    UpdateProductUseCase,
)
from src.core.entities.metafield import (
    MetafieldDefinition,
    MetafieldOwnerType,
    MetafieldType,
)
from src.core.entities.store import Store
from src.core.logging import get_logger
from src.infrastructure.database.models.tenant.ai_catalog import (
    AiCatalogSuggestionModel as Suggestion,
)
from src.infrastructure.repositories import ai_catalog_repository as repo
from src.infrastructure.repositories.metafield_repository import (
    MetafieldDefinitionRepository,
    MetafieldValueRepository,
)
from src.infrastructure.repositories.product_repository import ProductRepository
from src.infrastructure.repositories.store_repository import StoreRepository

logger = get_logger(__name__)

#: Ends the transaction: the route's commit-and-restore-RLS, or a task's commit.
Commit = Callable[[AsyncSession], Awaitable[None]]


async def _release(db: AsyncSession) -> None:
    """End the transaction before a provider call and open none: the call can
    outlast Postgres's idle-in-transaction timeout. The next ``commit`` (a
    no-op commit plus the tenant scope) resumes."""
    await db.commit()


class ActionError(Exception):
    """``code``: ``allowance`` · ``budget`` · ``gone`` · ``stale`` ·
    ``language_slot`` · ``slug_taken`` · ``slug_invalid`` · ``faq_clash`` · ``verify`` ·
    ``nothing`` · ``not_ready``, or a provider code from ``GenerationError``."""

    def __init__(self, code: str, fields: list[str] | None = None) -> None:
        self.code = code
        self.fields = fields or []
        super().__init__(code)


async def _faq(db: AsyncSession, store_id: UUID, product_id: UUID) -> Any:
    return (await repo.faq_values(db, store_id, [product_id])).get(product_id)


async def read(
    db: AsyncSession, store_id: UUID, product_id: UUID
) -> tuple[Any, dict[str, Any]] | None:
    """The product entity and NUMU's current values of every field."""
    entity = await ProductRepository(db).get_by_id(product_id)
    if entity is None or entity.store_id != store_id:
        return None
    return entity, rules.snapshot(entity, await _faq(db, store_id, product_id))


# ─── Generate ──────────────────────────────────────────────────────────


async def generate(
    db: AsyncSession,
    store: Store,
    product_id: UUID,
    groups: list[str],
    *,
    commit: Commit,
    item: Suggestion | None = None,
    reserved: bool = False,
) -> Suggestion:
    """One provider call for one product, saved as a ``ready`` suggestion (or
    ``failed``). ``item`` is a claimed bulk row whose run already reserved the
    allowance; a single improve reserves its own run here."""
    settings_row = await repo.ensure_settings(db, store.id, store.tenant_id)
    found = await read(db, store.id, product_id)
    if found is None:
        raise ActionError("gone")
    entity, snap = found
    keys = rules.keys_for(groups, list(settings_row.languages or rules.LANGUAGES))
    if not keys:
        raise ActionError("nothing")
    if not reserved:
        if not await repo.budget_left():
            raise ActionError("budget")
        if not await repo.reserve(
            db, store.id, store.tenant_id, repo.run_cap(settings_row)
        ):
            raise ActionError("allowance")
    row = item or Suggestion(
        store_id=store.id,
        tenant_id=store.tenant_id,
        product_id=product_id,
        kind="catalog",
    )
    row.fields = keys
    row.status = "running"
    row.current = snap
    row.base_updated_at = entity.updated_at
    if item is None:
        db.add(row)
    await db.flush()
    style = settings_row.arabic_style
    facts, voice = settings_row.store_facts, settings_row.brand_voice
    category = await _category_name(db, entity)
    alt_lang = (store.default_language or "ar")[:2]
    await _release(db)

    try:
        images = await gen.load_images(list(entity.images or []))
        answer = await gen.ask(
            rules.build_messages(
                snap=snap,
                keys=keys,
                style=style,
                store_name=store.name,
                category=category,
                store_facts=facts,
                brand_voice=voice,
                alt_lang=alt_lang,
                images=images,
            ),
            rules.answer_schema(keys),
            clean=lambda raw: rules.clean_answer(raw, keys, snap=snap),
        )
    except gen.GenerationError as exc:
        await commit(db)  # the tenant scope back before any statement
        row.status = "failed"
        row.error_code = exc.code
        await repo.refund(db, store.id)
        await commit(db)
        raise ActionError(exc.code) from exc
    await commit(db)
    row.status = "ready"
    row.suggested = answer.data
    row.model = answer.model
    row.input_tokens, row.output_tokens = answer.input_tokens, answer.output_tokens
    row.cost_micros = answer.cost_micros
    if "slug" in answer.data and not rules.valid_slug(
        answer.data["slug"], snap["slug"]
    ):
        answer.data.pop("slug")
    await repo.add_cost(db, store.id, row.cost_micros)
    await commit(db)
    return row


async def _category_name(db: AsyncSession, entity: Any) -> str | None:
    if not getattr(entity, "category_id", None):
        return None
    from src.infrastructure.database.models.tenant.category import CategoryModel

    category = await db.get(CategoryModel, entity.category_id)
    return category.name if category else None


# ─── Apply ─────────────────────────────────────────────────────────────


def _edited(key: str, value: Any, snap: dict[str, Any]) -> Any:
    """A merchant's edit of one field, shaped and clipped like the model's."""
    if key in rules.TEXT_FIELDS:
        return rules.clip(
            str(value or "").strip(), rules.LIMITS[rules.TEXT_FIELDS[key][0]]
        )
    if key == "slug":
        return str(value or "").strip().lower()
    if key == "alts":
        urls = set(snap["alts"])
        return {
            u: rules.clip(str(a or "").strip(), rules.LIMITS["alt"])
            for u, a in (value or {}).items()
            if u in urls
        }
    if key == "faq":
        return rules.faq_value(value)
    return None


def _empty(key: str, snap: dict[str, Any]) -> bool:
    if key == "faq":
        return not (snap["faq"]["ar"] or snap["faq"]["en"])
    return not snap.get(key)


async def _write(
    db: AsyncSession, store: Store, product_id: UUID, values: dict[str, Any]
) -> None:
    """Write already-checked values into NUMU: text through the update use
    case, alt text through ``set_image_alt``, FAQs through the metafield
    service. The caller holds the product lock and commits."""
    products = ProductRepository(db)
    entity = await products.get_by_id(product_id)
    text = {k: v for k, v in values.items() if k in rules.TEXT_FIELDS or k == "slug"}
    if text:
        try:
            await UpdateProductUseCase(products, StoreRepository(db)).execute(
                product_id=product_id,
                dto=UpdateProductDTO(**rules.update_input(text, entity.attributes)),
                user_id=store.owner_id,
                store_id=store.id,
            )
        except SlugTakenError as exc:
            raise ActionError("slug_taken", ["slug"]) from exc
    if values.get("alts"):
        entity = await products.get_by_id(product_id)
        for url, alt in values["alts"].items():
            if url in (entity.images or []):
                entity.set_image_alt(url, alt)
        await products.update(entity)
    if "faq" in values:
        definition = await _faq_definition(db, store)
        await metafield_values.write_value(
            MetafieldValueRepository(db), store, definition, product_id, values["faq"]
        )


async def _faq_definition(db: AsyncSession, store: Store) -> MetafieldDefinition:
    """The store's public ``faq.items`` product definition, created the first
    time; another type under that key is the merchant's own field: refused."""
    definitions = MetafieldDefinitionRepository(db)
    found = await definitions.get_by_key(
        store.id, MetafieldOwnerType.PRODUCT, "faq", "items"
    )
    if found is None:
        return await definitions.create(
            MetafieldDefinition(
                store_id=store.id,
                tenant_id=store.tenant_id,
                owner_type=MetafieldOwnerType.PRODUCT,
                namespace="faq",
                key="items",
                type=MetafieldType.JSON,
                name="الأسئلة الشائعة",
                description="Product FAQs, shown by the FAQ section on the product page.",
                is_public=True,
            )
        )
    if found.type != MetafieldType.JSON or not found.is_public:
        raise ActionError("faq_clash", ["faq"])
    return found


async def _after_commit(
    store: Store, product_id: UUID, snap: dict[str, Any], faq: bool
) -> None:
    """Shoppers see the change: caches busted and pages revalidated against
    committed rows; webhooks and the cache handler hear the update."""
    from src.infrastructure.cache.product_cache import get_product_cache
    from src.infrastructure.events.setup import get_event_bus

    try:
        await get_product_cache().invalidate_product(store.id, product_id)
    except Exception:  # noqa: BLE001 — best-effort, like the merchant routes
        pass
    if faq:
        await metafield_values.revalidate_storefront(store)
    if store.subdomain and snap["slug"]:
        from src.infrastructure.external_services.nextjs_revalidation import (
            revalidate_on_product_change,
        )

        await revalidate_on_product_change(
            subdomain=store.subdomain,
            store_id=str(store.id),
            product_slug=snap["slug"],
            product_id=str(product_id),
        )
    try:
        from src.core.events.product_events import ProductUpdatedEvent

        get_event_bus().publish(
            ProductUpdatedEvent(
                product_id=product_id,
                store_id=store.id,
                name=snap["name"] or snap["name_ar"],
            )
        )
    except Exception:  # noqa: BLE001 — the write is done; the event is a courtesy
        pass


def _same(key: str, a: Any, b: Any) -> bool:
    if key == "alts":
        return all(b.get(u) == alt for u, alt in a.items())
    if key == "faq":
        return rules.faq_lists(a) == rules.faq_lists(b)
    return a == b


async def apply(
    db: AsyncSession,
    store: Store,
    suggestion_id: UUID,
    accepted: list[str],
    *,
    user_id: UUID | None,
    commit: Commit,
    edits: dict[str, Any] | None = None,
    fill_empty: bool = False,
) -> dict[str, Any]:
    """Write the accepted fields of a ``ready`` suggestion into NUMU."""
    row = await repo.suggestion(db, store.id, suggestion_id, lock=True)
    if row is None or row.kind != "catalog":
        raise ActionError("gone")
    if row.status == "applied":
        return {"status": "applied", "applied": row.applied}
    if row.status != "ready":
        raise ActionError("not_ready")
    if await repo.product(db, store.id, row.product_id, lock=True) is None:
        raise ActionError("gone")
    found = await read(db, store.id, row.product_id)
    assert found is not None
    entity, fresh = found
    suggested = row.suggested or {}
    edits = edits or {}
    accepted = accepted or list(suggested)  # a bulk apply takes every field
    values = {
        k: (_edited(k, edits[k], fresh) if k in edits else suggested[k])
        for k in accepted
        if k in suggested or k in edits
    }
    if fill_empty:
        values = {
            k: v for k, v in values.items() if k != "alts" and _empty(k, fresh)
        } | (
            {
                "alts": {
                    u: a for u, a in values["alts"].items() if not fresh["alts"].get(u)
                }
            }
            if "alts" in values
            else {}
        )
        values = {k: v for k, v in values.items() if v}
    if not values:
        raise ActionError("nothing")
    stale = rules.stale_fields(list(values), row.current or {}, fresh)
    if stale:
        raise ActionError("stale", stale)
    refused = rules.language_slot_refusals(values, fresh)
    if refused:
        raise ActionError("language_slot", refused)
    if "slug" in values and not rules.valid_slug(values["slug"], fresh["slug"]):
        raise ActionError("slug_invalid", ["slug"])

    untouched = dict(entity.attributes or {})
    await _write(db, store, row.product_id, values)
    before = rules.score(fresh)[0]
    row.status = "applied"
    row.applied_by = user_id
    row.applied_at = datetime.now(UTC)
    row.applied = values
    await commit(db)

    after = await read(db, store.id, row.product_id)
    assert after is not None
    entity2, numu = after
    wrong = [k for k, v in values.items() if not _same(k, v, numu.get(k))]
    touched = {
        rules.TEXT_FIELDS[k][1]
        for k in values
        if k in rules.TEXT_FIELDS and rules.TEXT_FIELDS[k][1]
    }
    if any(
        (entity2.attributes or {}).get(k) != v
        for k, v in untouched.items()
        if k not in touched
    ):
        wrong.append("attributes")
    row.applied = {
        **values,
        "score_before": before,
        "score_after": rules.score(numu)[0],
    }
    if wrong:
        row.status = "failed_verify"
        row.error_code = "verify"
        logger.warning("ai_catalog_verify_failed", fields=wrong)
    await commit(db)
    await _after_commit(store, row.product_id, numu, "faq" in values)
    if wrong:
        raise ActionError("verify", wrong)
    return {"status": "applied", "applied": row.applied}


async def undo(
    db: AsyncSession, store: Store, product_id: UUID, *, commit: Commit
) -> dict[str, Any]:
    """Put back the last apply's fields that still hold what the app wrote."""
    row = await repo.latest(db, store.id, product_id, status=("applied",))
    if row is None:
        raise ActionError("nothing")
    if await repo.product(db, store.id, product_id, lock=True) is None:
        raise ActionError("gone")
    found = await read(db, store.id, product_id)
    assert found is not None
    _, fresh = found
    applied = {
        k: v
        for k, v in (row.applied or {}).items()
        if k not in ("score_before", "score_after")
    }
    values = rules.undo_values(applied, row.current or {}, fresh)
    if values:
        await _write(db, store, product_id, values)
    row.status = "undone"
    await commit(db)
    after = await read(db, store.id, product_id)
    if after is not None:
        await _after_commit(store, product_id, after[1], "faq" in values)
    return {"restored": sorted(values), "kept": sorted(set(applied) - set(values))}


# ─── Social copy ───────────────────────────────────────────────────────


async def social(
    db: AsyncSession, store: Store, product_id: UUID, *, commit: Commit
) -> Suggestion:
    """A post pack in both languages, stored as a ``social`` suggestion and
    never written to the product."""
    settings_row = await repo.ensure_settings(db, store.id, store.tenant_id)
    found = await read(db, store.id, product_id)
    if found is None:
        raise ActionError("gone")
    entity, snap = found
    if not await repo.budget_left():
        raise ActionError("budget")
    if not await repo.reserve(
        db, store.id, store.tenant_id, repo.run_cap(settings_row)
    ):
        raise ActionError("allowance")
    row = Suggestion(
        store_id=store.id,
        tenant_id=store.tenant_id,
        product_id=product_id,
        kind="social",
        status="running",
        fields=["social"],
        current=None,
    )
    db.add(row)
    await db.flush()
    style, voice = settings_row.arabic_style, settings_row.brand_voice
    await _release(db)
    try:
        images = await gen.load_images(list(entity.images or []))
        answer = await gen.ask(
            gen.social_messages(
                snap,
                style=style,
                store_name=store.name,
                brand_voice=voice,
                images=images,
            ),
            gen.social_schema(),
            clean=gen.clean_social,
            max_tokens=gen.SOCIAL_MAX_TOKENS,
        )
    except gen.GenerationError as exc:
        await commit(db)  # the tenant scope back before any statement
        row.status = "failed"
        row.error_code = exc.code
        await repo.refund(db, store.id)
        await commit(db)
        raise ActionError(exc.code) from exc
    await commit(db)
    row.status = "ready"
    row.suggested = answer.data
    row.model = answer.model
    row.input_tokens, row.output_tokens = answer.input_tokens, answer.output_tokens
    row.cost_micros = answer.cost_micros
    await repo.add_cost(db, store.id, row.cost_micros)
    await commit(db)
    return row
