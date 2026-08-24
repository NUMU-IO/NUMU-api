"""Unit tests for the retired-slug lookups behind the storefront's 301s.

`find_by_previous_slug` is what turns a renamed product's / collection's old
URL back into the live row. It runs only after the by-slug lookup misses, so
these tests pin the two things that make it safe: the predicate is a JSONB
containment match on `previous_slugs` (not a LIKE over the whole array, which
would resolve `scarf` from `silk-scarf`), and it is scoped to the store.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.entities.product import ProductStatus, ProductType
from src.infrastructure.repositories.category_repository import CategoryRepository
from src.infrastructure.repositories.product_repository import ProductRepository


def _compiled_sql(mock_session) -> str:
    """The SQL of the statement the repository handed to the session."""
    statement = mock_session.execute.call_args.args[0]
    return str(statement.compile(compile_kwargs={"literal_binds": False}))


class TestProductFindByPreviousSlug:
    """Tests for ProductRepository.find_by_previous_slug."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_session = MagicMock()
        self.mock_session.execute = AsyncMock()
        self.repository = ProductRepository(self.mock_session)

    def _mock_model(self, store_id):
        model = MagicMock()
        model.id = uuid4()
        model.store_id = store_id
        model.tenant_id = uuid4()
        model.name = "Silk Scarf"
        model.slug = "silk-scarf-2026"
        model.previous_slugs = ["silk-scarf"]
        model.sku = "SCARF-001"
        model.description = None
        model.short_description = None
        model.product_type = ProductType.PHYSICAL
        model.status = ProductStatus.ACTIVE
        model.price_amount = 49900
        model.price_currency = "EGP"
        model.compare_at_price = None
        model.cost_price = None
        # Commerce columns. Spelled out like every other field here — the
        # mock stands in for a real row, and a MagicMock left to auto-create
        # `sale_price` makes the repository try to build Money from a mock.
        model.sale_price = None
        model.sale_starts_at = None
        model.sale_ends_at = None
        model.requires_shipping = True
        model.tax_exempt = False
        model.related_product_ids = None
        model.quantity = 10
        model.low_stock_threshold = 5
        model.weight = None
        model.dimensions = {}
        model.images = []
        model.category_id = None
        model.tags = []
        model.attributes = {}
        model.brand = None
        model.seo_title = None
        model.seo_description = None
        model.template_suffix = None
        model.robots_noindex = False
        model.canonical_url = None
        model.sitemap_exclude = False
        model.meta_catalog_id = None
        model.extra_data = {}
        model.created_at = datetime.utcnow()
        model.updated_at = datetime.utcnow()
        return model

    @pytest.mark.asyncio
    async def test_returns_the_product_that_owns_the_retired_slug(self):
        store_id = uuid4()
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = self._mock_model(store_id)
        self.mock_session.execute.return_value = mock_result

        product = await self.repository.find_by_previous_slug(store_id, "silk-scarf")

        # The CURRENT slug is what comes back — that is what the storefront
        # redirects to.
        assert product is not None
        assert product.slug == "silk-scarf-2026"
        assert product.previous_slugs == ["silk-scarf"]

    @pytest.mark.asyncio
    async def test_returns_none_when_no_row_ever_had_the_slug(self):
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        self.mock_session.execute.return_value = mock_result

        assert await self.repository.find_by_previous_slug(uuid4(), "nope") is None

    @pytest.mark.asyncio
    async def test_query_is_store_scoped_jsonb_containment(self):
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        self.mock_session.execute.return_value = mock_result

        await self.repository.find_by_previous_slug(uuid4(), "silk-scarf")

        sql = _compiled_sql(self.mock_session)
        assert "products.previous_slugs @>" in sql
        assert "products.store_id =" in sql


class TestCategoryFindByPreviousSlug:
    """Tests for CategoryRepository.find_by_previous_slug."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_session = MagicMock()
        self.mock_session.execute = AsyncMock()
        self.repository = CategoryRepository(self.mock_session)

    @pytest.mark.asyncio
    async def test_returns_the_category_that_owns_the_retired_slug(self):
        store_id = uuid4()
        model = MagicMock()
        model.id = uuid4()
        model.store_id = store_id
        model.tenant_id = uuid4()
        model.name = "Silk Scarves"
        model.slug = "silk-scarves"
        model.previous_slugs = ["scarves"]
        model.description = None
        model.image_url = None
        model.parent_id = None
        model.position = 0
        model.is_active = True
        model.seo_title = None
        model.seo_description = None
        model.social_image_url = None
        model.template_suffix = None
        model.robots_noindex = False
        model.canonical_url = None
        model.sitemap_exclude = False
        model.extra_data = {}
        model.created_at = datetime.utcnow()
        model.updated_at = datetime.utcnow()

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = model
        self.mock_session.execute.return_value = mock_result

        category = await self.repository.find_by_previous_slug(store_id, "scarves")

        assert category is not None
        assert category.slug == "silk-scarves"
        assert category.previous_slugs == ["scarves"]

    @pytest.mark.asyncio
    async def test_query_is_store_scoped_jsonb_containment(self):
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        self.mock_session.execute.return_value = mock_result

        await self.repository.find_by_previous_slug(uuid4(), "scarves")

        sql = _compiled_sql(self.mock_session)
        assert "categories.previous_slugs @>" in sql
        assert "categories.store_id =" in sql
