"""Product repository implementation."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Text, cast, func, literal, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.product import Product, ProductStatus
from src.core.interfaces.repositories.product_repository import IProductRepository
from src.core.value_objects.money import Currency, Money
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models import ProductModel
from src.infrastructure.database.models.tenant.variant import VariantModel


class ProductRepository(IProductRepository):
    """Product repository implementation using SQLAlchemy.

    All queries include an explicit tenant_id filter as a defense-in-depth
    measure alongside PostgreSQL RLS policies.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        """Apply tenant_id filter if a tenant context is active."""
        tid = get_tenant_id()
        if tid:
            return query.where(ProductModel.tenant_id == tid)
        return query

    def _to_entity(self, model: ProductModel) -> Product:
        """Convert database model to domain entity."""
        currency = (
            Currency(model.price_currency) if model.price_currency else Currency.USD
        )
        return Product(
            id=model.id,
            store_id=model.store_id,
            tenant_id=model.tenant_id,
            name=model.name,
            slug=model.slug,
            previous_slugs=list(model.previous_slugs or []),
            sku=model.sku,
            description=model.description,
            short_description=model.short_description,
            product_type=model.product_type,
            status=model.status,
            price=Money.from_cents(model.price_amount, currency),
            compare_at_price=Money.from_cents(model.compare_at_price, currency)
            if model.compare_at_price
            else None,
            cost_price=Money.from_cents(model.cost_price, currency)
            if model.cost_price
            else None,
            sale_price=Money.from_cents(model.sale_price, currency)
            if model.sale_price is not None
            else None,
            sale_starts_at=model.sale_starts_at,
            sale_ends_at=model.sale_ends_at,
            # Columns added after rows existed, so an un-migrated or
            # partially-migrated row reads as the pre-existing behaviour.
            requires_shipping=(
                True if model.requires_shipping is None else model.requires_shipping
            ),
            tax_exempt=bool(model.tax_exempt),
            related_product_ids=[
                UUID(str(pid)) for pid in (model.related_product_ids or [])
            ],
            quantity=model.quantity,
            low_stock_threshold=model.low_stock_threshold,
            weight=model.weight,
            dimensions=model.dimensions,
            images=model.images or [],
            category_id=model.category_id,
            tags=model.tags or [],
            attributes=model.attributes,
            # Phase 8.1 option axes. Omitting this left Product.options at its
            # [] default on every repository read, so the storefront's
            # _resolve_options_for_product could never see the axes the hub
            # and the product CRUD write — no V3 theme could render a
            # size/colour selector even with a fully populated variant matrix.
            options=list(model.options or []),
            metadata=model.extra_data or {},
            brand=model.brand,
            robots_noindex=model.robots_noindex,
            canonical_url=model.canonical_url,
            sitemap_exclude=model.sitemap_exclude,
            seo_title=model.seo_title,
            seo_description=model.seo_description,
            template_suffix=model.template_suffix,
            meta_catalog_id=model.meta_catalog_id,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: Product) -> ProductModel:
        """Convert domain entity to database model."""
        return ProductModel(
            id=entity.id,
            store_id=entity.store_id,
            tenant_id=entity.tenant_id,
            name=entity.name,
            slug=entity.slug,
            previous_slugs=list(entity.previous_slugs or []),
            sku=entity.sku,
            description=entity.description,
            short_description=entity.short_description,
            product_type=entity.product_type,
            status=entity.status,
            price_amount=entity.price.cents,
            price_currency=entity.price.currency.value,
            compare_at_price=entity.compare_at_price.cents
            if entity.compare_at_price
            else None,
            cost_price=entity.cost_price.cents if entity.cost_price else None,
            sale_price=entity.sale_price.cents if entity.sale_price else None,
            sale_starts_at=entity.sale_starts_at,
            sale_ends_at=entity.sale_ends_at,
            requires_shipping=entity.requires_shipping,
            tax_exempt=entity.tax_exempt,
            related_product_ids=[str(pid) for pid in entity.related_product_ids]
            or None,
            quantity=entity.quantity,
            low_stock_threshold=entity.low_stock_threshold,
            weight=entity.weight,
            dimensions=entity.dimensions,
            images=entity.images,
            category_id=entity.category_id,
            tags=entity.tags,
            attributes=entity.attributes,
            extra_data=entity.metadata,
            brand=entity.brand,
            robots_noindex=entity.robots_noindex,
            canonical_url=entity.canonical_url,
            sitemap_exclude=entity.sitemap_exclude,
            seo_title=entity.seo_title,
            seo_description=entity.seo_description,
            template_suffix=entity.template_suffix,
            meta_catalog_id=entity.meta_catalog_id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> Product | None:
        """Get product by ID."""
        query = select(ProductModel).where(ProductModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_ids(self, entity_ids: list[UUID]) -> list[Product]:
        """Bulk-fetch products by ID.

        Hot-path replacement for callers that previously looped
        ``get_by_id`` per ID (e.g. ``GET /storefront/me/cart`` building
        the per-line product snapshot). Single ``WHERE id IN (...)``
        query instead of N round-trips.

        Returned list is in arbitrary order. Duplicate IDs in the
        input list yield one entity each in the output. Missing IDs
        are silently skipped (callers can detect with a set diff).
        """
        if not entity_ids:
            return []
        query = select(ProductModel).where(ProductModel.id.in_(entity_ids))
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(model) for model in result.scalars().all()]

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Product]:
        """Get all products with pagination."""
        query = select(ProductModel).offset(skip).limit(limit)
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(model) for model in result.scalars().all()]

    async def create(self, entity: Product) -> Product:
        """Create a new product."""
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: Product) -> Product:
        """Update an existing product."""
        query = select(ProductModel).where(ProductModel.id == entity.id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model:
            model.name = entity.name
            model.slug = entity.slug
            model.previous_slugs = list(entity.previous_slugs or [])
            model.sku = entity.sku
            model.description = entity.description
            model.short_description = entity.short_description
            model.product_type = entity.product_type
            model.status = entity.status
            model.price_amount = entity.price.cents
            model.price_currency = entity.price.currency.value
            model.compare_at_price = (
                entity.compare_at_price.cents if entity.compare_at_price else None
            )
            model.cost_price = entity.cost_price.cents if entity.cost_price else None
            model.sale_price = entity.sale_price.cents if entity.sale_price else None
            model.sale_starts_at = entity.sale_starts_at
            model.sale_ends_at = entity.sale_ends_at
            model.requires_shipping = entity.requires_shipping
            model.tax_exempt = entity.tax_exempt
            model.related_product_ids = [
                str(pid) for pid in entity.related_product_ids
            ] or None
            model.quantity = entity.quantity
            model.low_stock_threshold = entity.low_stock_threshold
            model.weight = entity.weight
            model.dimensions = entity.dimensions
            model.images = entity.images
            model.category_id = entity.category_id
            model.tags = entity.tags
            model.attributes = entity.attributes
            model.extra_data = entity.metadata
            model.brand = entity.brand
            model.robots_noindex = entity.robots_noindex
            model.canonical_url = entity.canonical_url
            model.sitemap_exclude = entity.sitemap_exclude
            model.seo_title = entity.seo_title
            model.seo_description = entity.seo_description
            model.template_suffix = entity.template_suffix
            model.meta_catalog_id = entity.meta_catalog_id
            await self.session.flush()
            await self.session.refresh(model)
            return self._to_entity(model)
        raise ValueError(f"Product with id {entity.id} not found")

    async def delete(self, entity_id: UUID) -> bool:
        """Delete a product by ID."""
        query = select(ProductModel).where(ProductModel.id == entity_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        """Get total count of products."""
        result = await self.session.execute(select(func.count(ProductModel.id)))
        return result.scalar() or 0

    async def get_by_store(
        self,
        store_id: UUID,
        skip: int = 0,
        limit: int = 100,
        status: ProductStatus | None = None,
    ) -> list[Product]:
        """Get all products for a store."""
        query = select(ProductModel).where(ProductModel.store_id == store_id)
        if status:
            query = query.where(ProductModel.status == status)
        query = query.offset(skip).limit(limit)
        result = await self.session.execute(query)
        return [self._to_entity(model) for model in result.scalars().all()]

    async def get_by_slug(self, store_id: UUID, slug: str) -> Product | None:
        """Get product by slug within a store."""
        result = await self.session.execute(
            select(ProductModel).where(
                ProductModel.store_id == store_id,
                ProductModel.slug == slug,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def find_by_previous_slug(self, store_id: UUID, slug: str) -> Product | None:
        """Get the product that USED to live at ``slug`` (rename history).

        Called only after ``get_by_slug`` misses, so the storefront can 301 an
        old URL to the current one instead of 404ing it.
        """
        # JSONB containment: previous_slugs @> '["<slug>"]'
        query = select(ProductModel).where(
            ProductModel.store_id == store_id,
            ProductModel.previous_slugs.contains([slug]),
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalars().first()
        return self._to_entity(model) if model else None

    async def get_by_sku(self, store_id: UUID, sku: str) -> Product | None:
        """Get product by SKU within a store."""
        result = await self.session.execute(
            select(ProductModel).where(
                ProductModel.store_id == store_id,
                ProductModel.sku == sku,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_category(
        self,
        store_id: UUID,
        category_id: UUID,
        skip: int = 0,
        limit: int = 100,
        is_active: bool | None = None,
    ) -> list[Product]:
        """Get products in a category, scoped to a store.

        store_id scoping is required: category ids are not guaranteed unique
        across tenants, so an unscoped lookup leaks another store's catalog
        (including unpublished drafts). Pass ``is_active=True`` from public
        storefront callers to restrict to published products.

        Shares `_apply_product_filters` with `count_with_filters` so a
        collection page's item list and its reported total can never drift
        apart. Parent categories include their descendants' products — see
        `_category_tree_ids`.
        """
        query = self._apply_product_filters(
            select(ProductModel),
            store_id=store_id,
            category_id=category_id,
            is_active=is_active,
        )
        # Deterministic order: without one, Postgres is free to return rows in
        # any order per execution, so paging through a collection could repeat
        # or skip products between page 1 and page 2.
        query = query.order_by(ProductModel.created_at.desc(), ProductModel.id.desc())
        result = await self.session.execute(query.offset(skip).limit(limit))
        return [self._to_entity(model) for model in result.scalars().all()]

    async def search(
        self,
        store_id: UUID,
        query: str,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Product]:
        """Search products by name or description."""
        search_term = f"%{query}%"
        result = await self.session.execute(
            select(ProductModel)
            .where(
                ProductModel.store_id == store_id,
                or_(
                    ProductModel.name.ilike(search_term),
                    ProductModel.description.ilike(search_term),
                ),
            )
            .offset(skip)
            .limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def count_search(self, store_id: UUID, query: str) -> int:
        """Count products matching `search`, mirroring `search` exactly.

        Kept beside `search` on purpose: it matches the same two columns, so
        the two must be edited together or a search result page will report a
        total it cannot deliver.
        """
        search_term = f"%{query}%"
        result = await self.session.execute(
            select(func.count(ProductModel.id)).where(
                ProductModel.store_id == store_id,
                or_(
                    ProductModel.name.ilike(search_term),
                    ProductModel.description.ilike(search_term),
                ),
            )
        )
        return result.scalar() or 0

    async def get_low_stock(
        self,
        store_id: UUID,
        threshold: int | None = None,
        limit: int = 100,
    ) -> list[Product]:
        """Get products with low stock."""
        result = await self.session.execute(
            select(ProductModel)
            .where(
                ProductModel.store_id == store_id,
                ProductModel.quantity
                <= (threshold or ProductModel.low_stock_threshold),
                ProductModel.quantity > 0,
            )
            .limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def get_out_of_stock(
        self,
        store_id: UUID,
        limit: int = 100,
    ) -> list[Product]:
        """Get products that are out of stock."""
        result = await self.session.execute(
            select(ProductModel)
            .where(
                ProductModel.store_id == store_id,
                ProductModel.quantity == 0,
            )
            .limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def count_by_store(self, store_id: UUID) -> int:
        """Get total count of products for a store."""
        result = await self.session.execute(
            select(func.count(ProductModel.id)).where(ProductModel.store_id == store_id)
        )
        return result.scalar() or 0

    async def count_active(self, store_id: UUID) -> int:
        """Count of active products. Used by the SEO sitemap-feed endpoint
        to surface pagination totals without loading rows."""
        result = await self.session.execute(
            select(func.count(ProductModel.id)).where(
                ProductModel.store_id == store_id,
                ProductModel.status == ProductStatus.ACTIVE.value,
            )
        )
        return result.scalar() or 0

    async def list_sitemap_feed(
        self,
        *,
        store_id: UUID,
        skip: int,
        limit: int,
    ) -> list[dict]:
        """Lean sitemap feed — slug + updated_at + first image only.

        Returns plain dicts (not Product entities) so we don't pay for the
        Money/Currency value-object roundtrip we don't need. Used by the
        storefront's `app/(store)/[subdomain]/sitemap.ts` route to avoid
        paginating the full `/products` endpoint when generating sitemaps
        for tenants with thousands of products.

        Ordered by updated_at DESC so the most-recently-edited products
        show up at the top of the sitemap — Google re-crawls those first.
        """
        result = await self.session.execute(
            select(
                ProductModel.id,
                ProductModel.slug,
                ProductModel.updated_at,
                ProductModel.images,
            )
            .where(
                ProductModel.store_id == store_id,
                ProductModel.status == ProductStatus.ACTIVE.value,
                # A sitemap that advertises a URL the page itself noindexes
                # contradicts itself, so both merchant switches drop the row
                # here rather than making every consumer re-filter.
                ProductModel.sitemap_exclude.is_(False),
                ProductModel.robots_noindex.is_(False),
            )
            .order_by(ProductModel.updated_at.desc().nulls_last())
            .offset(skip)
            .limit(limit)
        )
        rows = result.all()
        out: list[dict] = []
        for row in rows:
            images = row.images or []
            first_image: str | None = None
            for entry in images:
                if isinstance(entry, str) and entry.strip():
                    first_image = entry
                    break
                if isinstance(entry, dict):
                    url = entry.get("url") or entry.get("src")
                    if isinstance(url, str) and url.strip():
                        first_image = url
                        break
            out.append({
                "id": str(row.id),
                "slug": row.slug,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "first_image": first_image,
            })
        return out

    async def deduct_stock(
        self,
        product_id: UUID,
        quantity: int,
        allow_negative: bool = False,
    ) -> bool:
        """Atomically deduct stock if sufficient quantity exists.

        Uses a conditional UPDATE that only succeeds when current stock >= requested.
        When ``allow_negative=True`` the condition is dropped — used for products
        flagged ``continue_selling_when_out_of_stock``, where oversell is
        allowed and stock can go negative so the merchant still sees how deep
        they're in the hole.
        """
        conditions = [ProductModel.id == product_id]
        if not allow_negative:
            conditions.append(ProductModel.quantity >= quantity)
        result = await self.session.execute(
            update(ProductModel)
            .where(*conditions)
            .values(quantity=ProductModel.quantity - quantity)
        )
        await self.session.flush()
        return result.rowcount > 0

    async def propagate_label_text(
        self, store_id: UUID, key: str, text_en: str, text_ar: str
    ) -> int:
        """Rewrite the denormalized ``attributes.label`` text on every product
        of the store that carries the given label key. Called when a merchant
        renames a custom label definition so already-labeled products don't
        keep the stale text. Returns the number of products updated."""
        # literal(dict, JSONB) binds a real jsonb OBJECT; the path must bind
        # as text[] — a plain str would bind VARCHAR and jsonb_set(jsonb,
        # varchar, jsonb) doesn't exist.
        new_label = literal(
            {"key": key, "text_en": text_en, "text_ar": text_ar}, type_=JSONB
        )
        label_path = literal(["label"], type_=ARRAY(Text()))
        query = (
            update(ProductModel)
            .where(
                ProductModel.store_id == store_id,
                ProductModel.attributes["label"]["key"].astext == key,
            )
            .values(
                attributes=func.jsonb_set(
                    ProductModel.attributes, label_path, new_label
                )
            )
        )
        result = await self.session.execute(self._tenant_filter(query))
        await self.session.flush()
        return result.rowcount or 0

    async def clear_label(self, store_id: UUID, key: str) -> int:
        """Strip ``attributes.label`` from every product of the store that
        carries the given label key. Called when a custom label definition is
        deleted — affected products safely fall back to "no label". Returns
        the number of products updated."""
        query = (
            update(ProductModel)
            .where(
                ProductModel.store_id == store_id,
                ProductModel.attributes["label"]["key"].astext == key,
            )
            .values(attributes=ProductModel.attributes.op("-")(cast("label", Text)))
        )
        result = await self.session.execute(self._tenant_filter(query))
        await self.session.flush()
        return result.rowcount or 0

    async def deduct_variant_stock(
        self,
        product_id: UUID,
        selections: dict[str, str],
        quantity: int,
        allow_negative: bool = False,
    ) -> tuple[bool, str | None]:
        """Atomically deduct stock from the matching variant combination.

        Locks the product row (SELECT ... FOR UPDATE), finds the combo
        whose ``options`` dict matches ``selections``, and decrements its
        ``stock``. Stock lives under ``attributes.variant_combinations[].stock``
        (stored as a string by the merchant hub) — we cast defensively.

        Returns ``(success, reason)`` where reason is one of:
            None                  — success
            "not_found"           — product doesn't exist
            "no_matching_variant" — selections don't match any combo
            "combo_disabled"      — matched combo has enabled=False
            "insufficient_stock"  — combo stock < quantity (and !allow_negative)
        """
        result = await self.session.execute(
            select(ProductModel).where(ProductModel.id == product_id).with_for_update()
        )
        model = result.scalar_one_or_none()
        if model is None:
            return False, "not_found"

        attrs = dict(model.attributes or {})
        combos = attrs.get("variant_combinations") or []
        if not isinstance(combos, list):
            return False, "no_matching_variant"

        # Normalise both sides to lowercase before comparing — the
        # storefront writes selections with capitalised axis names
        # (`{"Size": "M"}`) while the merchant hub stores combo options
        # lowercase (`{"size": "m"}`). Without normalisation the strict
        # `==` never matched and stock was never deducted from the
        # per-variant counter.
        def _norm(d: dict | None) -> dict:
            return {str(k).lower(): str(v).lower() for k, v in (d or {}).items()}

        target = _norm(selections)

        for combo in combos:
            if not isinstance(combo, dict):
                continue
            if _norm(combo.get("options")) != target:
                continue
            if combo.get("enabled") is False:
                return False, "combo_disabled"
            raw = combo.get("stock")
            try:
                current = int(raw) if raw not in (None, "") else 0
            except (TypeError, ValueError):
                current = 0
            if not allow_negative and current < quantity:
                return False, "insufficient_stock"
            combo["stock"] = str(current - quantity)
            attrs["variant_combinations"] = combos
            model.attributes = attrs  # force SQLAlchemy to pick up the JSONB mutation
            await self.session.flush()
            return True, None

        return False, "no_matching_variant"

    async def restore_stock(self, product_id: UUID, quantity: int) -> None:
        """Atomically restore stock (e.g. on order cancellation)."""
        await self.session.execute(
            update(ProductModel)
            .where(ProductModel.id == product_id)
            .values(quantity=ProductModel.quantity + quantity)
        )
        await self.session.flush()

    async def bulk_update_quantity(
        self,
        updates: list[tuple[UUID, int]],
    ) -> None:
        """Bulk update product quantities."""
        for product_id, delta in updates:
            await self.session.execute(
                update(ProductModel)
                .where(ProductModel.id == product_id)
                .values(quantity=ProductModel.quantity + delta)
            )
        await self.session.flush()

    def _category_tree_ids(self, category_id):
        """Selectable yielding a category's id plus ALL descendant ids.

        Categories nest (parent_id); products live on leaf categories. A
        filter on a parent category must include its children's products or
        every parent collection page renders empty — recursive CTE walks the
        tree in one query.
        """
        from sqlalchemy.orm import aliased

        from src.infrastructure.database.models.tenant.category import (
            CategoryModel,
        )

        tree = (
            select(CategoryModel.id)
            .where(CategoryModel.id == category_id)
            .cte("category_tree", recursive=True)
        )
        child = aliased(CategoryModel)
        tree = tree.union_all(select(child.id).where(child.parent_id == tree.c.id))
        return select(tree.c.id)

    def _apply_product_filters(
        self,
        query,
        *,
        store_id=None,
        category_id=None,
        is_active=None,
        status_filter: ProductStatus | None = None,
        search=None,
        sku=None,
        price_min=None,
        price_max=None,
        has_cost: bool | None = None,
    ):
        """Apply shared filter predicates to a product query."""
        query = self._tenant_filter(query)
        if store_id:
            query = query.where(ProductModel.store_id == store_id)
        if category_id:
            query = query.where(
                ProductModel.category_id.in_(self._category_tree_ids(category_id))
            )
        # status_filter is the 3-state path (active/draft/archived/out_of_stock)
        # and wins over the legacy `is_active` boolean when both are provided.
        # The old boolean couldn't represent ARCHIVED at all, so the merchant
        # hub's Archived tab silently returned every product.
        if status_filter is not None:
            query = query.where(ProductModel.status == status_filter)
        elif is_active is not None:
            target_status = ProductStatus.ACTIVE if is_active else ProductStatus.DRAFT
            query = query.where(ProductModel.status == target_status)
        if sku:
            query = query.where(ProductModel.sku.ilike(f"%{sku}%"))
        if price_min is not None:
            query = query.where(ProductModel.price_amount >= price_min)
        if price_max is not None:
            query = query.where(ProductModel.price_amount <= price_max)
        # Profit-readiness filter: the hub's "N products missing cost" banner
        # and `/products?cost=missing` deep link.
        #
        # A cost on ANY variant counts, because the dashboard's gross-profit
        # maths prefers the variant's own cost over the parent product's. If
        # this looked at `products.cost_price` alone, the tile's "N of M have
        # a cost set" hint and the list its link opens would disagree for
        # every product costed per-SKU.
        if has_cost is not None:
            variant_cost_exists = (
                select(literal(1))
                .where(
                    VariantModel.product_id == ProductModel.id,
                    VariantModel.cost_price.isnot(None),
                )
                .exists()
            )
            costed = or_(ProductModel.cost_price.isnot(None), variant_cost_exists)
            query = query.where(costed if has_cost else ~costed)
        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    ProductModel.name.ilike(search_term),
                    ProductModel.description.ilike(search_term),
                    ProductModel.sku.ilike(search_term),
                )
            )
        return query

    @staticmethod
    def _apply_sort(query, sort_by: str | None, sort_order: str = "asc"):
        """Apply sorting to a product query."""
        sort_columns = {
            "name": ProductModel.name,
            "price": ProductModel.price_amount,
            "created_at": ProductModel.created_at,
            "updated_at": ProductModel.updated_at,
            "quantity": ProductModel.quantity,
        }
        column = sort_columns.get(sort_by, ProductModel.created_at)
        if sort_order == "desc":
            column = column.desc()
        else:
            column = column.asc()
        return query.order_by(column)

    async def list_with_filters(
        self,
        store_id: UUID | None = None,
        category_id: UUID | None = None,
        skip: int = 0,
        limit: int = 100,
        is_active: bool | None = None,
        status_filter: ProductStatus | None = None,
        search: str | None = None,
        sku: str | None = None,
        price_min: int | None = None,
        price_max: int | None = None,
        sort_by: str | None = None,
        sort_order: str = "asc",
        has_cost: bool | None = None,
    ) -> list[Product]:
        """List products with multiple optional filters, price range, and sorting."""
        query = select(ProductModel)
        query = self._apply_product_filters(
            query,
            store_id=store_id,
            category_id=category_id,
            is_active=is_active,
            status_filter=status_filter,
            search=search,
            sku=sku,
            price_min=price_min,
            price_max=price_max,
            has_cost=has_cost,
        )
        query = self._apply_sort(query, sort_by, sort_order)
        query = query.offset(skip).limit(limit)
        result = await self.session.execute(query)
        return [self._to_entity(model) for model in result.scalars().all()]

    async def count_with_filters(
        self,
        store_id: UUID | None = None,
        category_id: UUID | None = None,
        is_active: bool | None = None,
        status_filter: ProductStatus | None = None,
        search: str | None = None,
        sku: str | None = None,
        price_min: int | None = None,
        price_max: int | None = None,
        has_cost: bool | None = None,
    ) -> int:
        """Count products matching the given filters."""
        query = select(func.count(ProductModel.id))
        query = self._apply_product_filters(
            query,
            store_id=store_id,
            category_id=category_id,
            is_active=is_active,
            status_filter=status_filter,
            search=search,
            sku=sku,
            price_min=price_min,
            price_max=price_max,
            has_cost=has_cost,
        )
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def list_with_cursor(
        self,
        store_id: UUID,
        category_id: UUID | None = None,
        cursor_timestamp: str | None = None,
        cursor_id: str | None = None,
        limit: int = 15,
        is_active: bool | None = True,
    ) -> list[Product]:
        """List products with cursor-based pagination.

        Uses (created_at, id) as cursor keys for stable, O(1) pagination.
        Results are ordered by created_at DESC, id DESC (newest first).

        Args:
            store_id: Store to filter by
            category_id: Optional category filter
            cursor_timestamp: Timestamp from cursor (ISO format)
            cursor_id: ID from cursor (UUID string)
            limit: Maximum items to return
            is_active: Filter by active status (default: True for storefront)

        Returns:
            List of products after the cursor position
        """
        query = select(ProductModel).where(ProductModel.store_id == store_id)

        # Apply category filter
        if category_id:
            query = query.where(ProductModel.category_id == category_id)

        # Apply active status filter
        if is_active is not None:
            target_status = ProductStatus.ACTIVE if is_active else ProductStatus.DRAFT
            query = query.where(ProductModel.status == target_status)

        # Apply cursor filter (keyset pagination)
        # For descending order: get items where (created_at, id) < (cursor_ts, cursor_id)
        if cursor_timestamp and cursor_id:
            # Parse the cursor timestamp
            cursor_ts = datetime.fromisoformat(cursor_timestamp)
            cursor_uuid = UUID(cursor_id)

            # Keyset condition for descending order
            query = query.where(
                tuple_(ProductModel.created_at, ProductModel.id)
                < tuple_(cursor_ts, cursor_uuid)
            )

        # Order by created_at DESC, id DESC for consistent ordering
        query = query.order_by(
            ProductModel.created_at.desc(),
            ProductModel.id.desc(),
        )

        # Limit results
        query = query.limit(limit)

        result = await self.session.execute(query)
        return [self._to_entity(model) for model in result.scalars().all()]
