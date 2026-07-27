"""Unit tests for slug rename history on the catalogue update use cases.

Renaming a product or a category rewrites its storefront URL. These tests
pin the guarantee that makes the rename survivable: the retired slug is
recorded on the entity handed to the repository, so the storefront can
resolve an old URL and 301 it instead of 404ing away its ranking.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.application.dto.category import UpdateCategoryDTO
from src.application.dto.product import UpdateProductDTO
from src.application.use_cases.categories.update_category import UpdateCategoryUseCase
from src.application.use_cases.products.update_product import UpdateProductUseCase
from src.core.entities.category import Category
from src.core.entities.product import Product
from src.core.entities.store import Store, StoreStatus
from src.core.utils.slug_history import MAX_PREVIOUS_SLUGS
from src.core.value_objects.money import Currency, Money


class TestProductSlugRenameHistory:
    """Tests for UpdateProductUseCase slug-history bookkeeping."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_product_repo = MagicMock()
        self.mock_product_repo.get_by_id = AsyncMock()
        # The repository echoes back whatever the use case saved, so the
        # assertions can read the entity exactly as it was persisted.
        self.mock_product_repo.update = AsyncMock(side_effect=lambda p: p)

        self.mock_store_repo = MagicMock()
        self.mock_store_repo.get_by_id = AsyncMock()

        self.use_case = UpdateProductUseCase(
            product_repository=self.mock_product_repo,
            store_repository=self.mock_store_repo,
        )

        self.user_id = uuid4()
        self.store_id = uuid4()
        self.product_id = uuid4()

        self.mock_store_repo.get_by_id.return_value = Store(
            id=self.store_id,
            owner_id=self.user_id,
            name="Test Store",
            slug="test-store",
            status=StoreStatus.ACTIVE,
            default_currency=Currency.EGP,
        )

    def _product(self, **overrides) -> Product:
        defaults = {
            "id": self.product_id,
            "store_id": self.store_id,
            "name": "Silk Scarf",
            "slug": "silk-scarf",
            "price": Money(amount=Decimal("499.00"), currency=Currency.EGP),
        }
        defaults.update(overrides)
        product = Product(**defaults)
        self.mock_product_repo.get_by_id.return_value = product
        return product

    async def _rename(self, new_slug: str):
        return await self.use_case.execute(
            product_id=self.product_id,
            store_id=self.store_id,
            dto=UpdateProductDTO(slug=new_slug),
            user_id=self.user_id,
        )

    @pytest.mark.asyncio
    async def test_rename_records_the_old_slug(self):
        self._product()

        result = await self._rename("silk-scarf-2026")

        saved = self.mock_product_repo.update.call_args.args[0]
        assert result.slug == "silk-scarf-2026"
        assert saved.previous_slugs == ["silk-scarf"]

    @pytest.mark.asyncio
    async def test_resaving_the_same_slug_records_nothing(self):
        # The hub resends the full form on every save, so an unchanged slug
        # arrives on the wire constantly — it must not grow the history.
        self._product()

        await self._rename("silk-scarf")

        saved = self.mock_product_repo.update.call_args.args[0]
        assert saved.slug == "silk-scarf"
        assert saved.previous_slugs == []

    @pytest.mark.asyncio
    async def test_successive_renames_keep_every_old_slug(self):
        product = self._product(previous_slugs=["scarf"])

        await self._rename("silk-scarf-2026")

        assert product.previous_slugs == ["scarf", "silk-scarf"]

    @pytest.mark.asyncio
    async def test_renaming_back_drops_the_reused_slug_from_history(self):
        # Otherwise the current URL would redirect to itself.
        product = self._product(slug="silk-scarf-2026", previous_slugs=["silk-scarf"])

        await self._rename("silk-scarf")

        assert product.previous_slugs == ["silk-scarf-2026"]

    @pytest.mark.asyncio
    async def test_history_is_bounded(self):
        product = self._product(
            previous_slugs=[f"slug-{i}" for i in range(MAX_PREVIOUS_SLUGS)]
        )

        await self._rename("silk-scarf-2026")

        assert len(product.previous_slugs) == MAX_PREVIOUS_SLUGS
        assert product.previous_slugs[-1] == "silk-scarf"
        assert "slug-0" not in product.previous_slugs

    @pytest.mark.asyncio
    async def test_editing_other_fields_leaves_the_history_alone(self):
        product = self._product(previous_slugs=["scarf"])

        await self.use_case.execute(
            product_id=self.product_id,
            store_id=self.store_id,
            dto=UpdateProductDTO(name="Silk Scarf (Ivory)"),
            user_id=self.user_id,
        )

        assert product.slug == "silk-scarf"
        assert product.previous_slugs == ["scarf"]


class TestCategorySlugRenameHistory:
    """Tests for UpdateCategoryUseCase slug-history bookkeeping."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_category_repo = MagicMock()
        self.mock_category_repo.get_by_id = AsyncMock()
        self.mock_category_repo.get_by_slug = AsyncMock(return_value=None)
        self.mock_category_repo.update = AsyncMock(side_effect=lambda c: c)

        self.mock_store_repo = MagicMock()
        self.mock_store_repo.get_by_id = AsyncMock()

        self.use_case = UpdateCategoryUseCase(
            category_repository=self.mock_category_repo,
            store_repository=self.mock_store_repo,
        )

        self.user_id = uuid4()
        self.store_id = uuid4()
        self.category_id = uuid4()

        self.mock_store_repo.get_by_id.return_value = Store(
            id=self.store_id,
            owner_id=self.user_id,
            name="Test Store",
            slug="test-store",
            status=StoreStatus.ACTIVE,
            default_currency=Currency.EGP,
        )

    def _category(self, **overrides) -> Category:
        defaults = {
            "id": self.category_id,
            "store_id": self.store_id,
            "name": "Scarves",
            "slug": "scarves",
        }
        defaults.update(overrides)
        category = Category(**defaults)
        self.mock_category_repo.get_by_id.return_value = category
        return category

    async def _update(self, dto: UpdateCategoryDTO):
        return await self.use_case.execute(
            category_id=self.category_id,
            store_id=self.store_id,
            dto=dto,
            user_id=self.user_id,
        )

    @pytest.mark.asyncio
    async def test_explicit_rename_records_the_old_slug(self):
        category = self._category()

        await self._update(UpdateCategoryDTO(slug="silk-scarves"))

        assert category.slug == "silk-scarves"
        assert category.previous_slugs == ["scarves"]

    @pytest.mark.asyncio
    async def test_rename_driven_by_the_name_also_records_it(self):
        # This is the dangerous path: a merchant edits only the display NAME
        # and the slug — the URL — silently changes underneath them.
        category = self._category()

        await self._update(UpdateCategoryDTO(name="Silk Scarves"))

        assert category.slug == "silk-scarves"
        assert category.previous_slugs == ["scarves"]

    @pytest.mark.asyncio
    async def test_resaving_the_same_slug_records_nothing(self):
        category = self._category()

        await self._update(UpdateCategoryDTO(slug="scarves"))

        assert category.previous_slugs == []

    @pytest.mark.asyncio
    async def test_no_history_when_the_target_slug_is_taken(self):
        # The auto-rename branch declines the collision and keeps the current
        # slug; recording a rename that did not happen would create a
        # redirect from the live URL.
        category = self._category()
        self.mock_category_repo.get_by_slug.return_value = Category(
            id=uuid4(),
            store_id=self.store_id,
            name="Silk Scarves",
            slug="silk-scarves",
        )

        await self._update(UpdateCategoryDTO(name="Silk Scarves"))

        assert category.slug == "scarves"
        assert category.previous_slugs == []
