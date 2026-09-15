"""The SEO fields the category and product routes accept must reach the entity.

The routes validated seo_title, seo_description, social_image_url,
canonical_url, robots_noindex and sitemap_exclude and built them into the DTO,
but the use cases never copied them onto the entity, so every write answered
200 and persisted nothing.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.application.dto.category import CreateCategoryDTO, UpdateCategoryDTO
from src.application.dto.product import UpdateProductDTO
from src.application.use_cases.categories.create_category import CreateCategoryUseCase
from src.application.use_cases.categories.update_category import UpdateCategoryUseCase
from src.application.use_cases.products.update_product import UpdateProductUseCase
from src.core.entities.category import Category
from src.core.entities.product import Product
from src.core.entities.store import Store, StoreStatus
from src.core.value_objects.money import Currency, Money

SEO = {
    "seo_title": "Romance Books in Egypt",
    "seo_description": "Shop romance books on white or creamy paper.",
    "social_image_url": "https://cdn.example.com/romance.jpg",
    "canonical_url": "https://shop.example.com/collections/romance",
    "robots_noindex": True,
    "sitemap_exclude": True,
}


@pytest.fixture
def ids():
    return {"user": uuid4(), "store": uuid4(), "entity": uuid4()}


@pytest.fixture
def store_repo(ids):
    repo = MagicMock()
    repo.get_by_id = AsyncMock(
        return_value=Store(
            id=ids["store"],
            owner_id=ids["user"],
            name="Test Store",
            slug="test-store",
            status=StoreStatus.ACTIVE,
            default_currency=Currency.EGP,
        )
    )
    return repo


def _echo_repo():
    repo = MagicMock()
    repo.get_by_id = AsyncMock()
    repo.get_by_slug = AsyncMock(return_value=None)
    repo.create = AsyncMock(side_effect=lambda e: e)
    repo.update = AsyncMock(side_effect=lambda e: e)
    return repo


def _category(ids, **overrides):
    return Category(
        id=ids["entity"],
        store_id=ids["store"],
        name="Romance",
        slug="romance",
        **overrides,
    )


@pytest.mark.asyncio
async def test_create_category_persists_seo_fields(ids, store_repo):
    repo = _echo_repo()
    await CreateCategoryUseCase(repo, store_repo).execute(
        dto=CreateCategoryDTO(name="Romance", **SEO),
        store_id=ids["store"],
        user_id=ids["user"],
    )
    saved = repo.create.call_args.args[0]
    for key, value in SEO.items():
        assert getattr(saved, key) == value, key


@pytest.mark.asyncio
async def test_update_category_persists_seo_fields(ids, store_repo):
    repo = _echo_repo()
    repo.get_by_id.return_value = _category(ids)
    await UpdateCategoryUseCase(repo, store_repo).execute(
        category_id=ids["entity"],
        dto=UpdateCategoryDTO(**SEO),
        user_id=ids["user"],
        store_id=ids["store"],
    )
    saved = repo.update.call_args.args[0]
    for key, value in SEO.items():
        assert getattr(saved, key) == value, key


@pytest.mark.asyncio
async def test_update_category_without_flags_keeps_them(ids, store_repo):
    repo = _echo_repo()
    repo.get_by_id.return_value = _category(
        ids, robots_noindex=True, sitemap_exclude=True
    )
    await UpdateCategoryUseCase(repo, store_repo).execute(
        category_id=ids["entity"],
        dto=UpdateCategoryDTO(description="Only the description changes."),
        user_id=ids["user"],
        store_id=ids["store"],
    )
    saved = repo.update.call_args.args[0]
    assert saved.robots_noindex is True
    assert saved.sitemap_exclude is True


@pytest.mark.asyncio
async def test_update_product_persists_indexing_fields(ids, store_repo):
    repo = _echo_repo()
    repo.get_by_id.return_value = Product(
        id=ids["entity"],
        store_id=ids["store"],
        name="Fourth Wing",
        slug="fourth-wing",
        price=Money(amount=Decimal("180.00"), currency=Currency.EGP),
    )
    use_case = UpdateProductUseCase(
        product_repository=repo, store_repository=store_repo
    )
    await use_case.execute(
        product_id=ids["entity"],
        store_id=ids["store"],
        dto=UpdateProductDTO(
            canonical_url=SEO["canonical_url"],
            robots_noindex=True,
            sitemap_exclude=True,
        ),
        user_id=ids["user"],
    )
    saved = repo.update.call_args.args[0]
    assert saved.canonical_url == SEO["canonical_url"]
    assert saved.robots_noindex is True
    assert saved.sitemap_exclude is True

    await use_case.execute(
        product_id=ids["entity"],
        store_id=ids["store"],
        dto=UpdateProductDTO(seo_title="Fourth Wing by Rebecca Yarros"),
        user_id=ids["user"],
    )
    saved = repo.update.call_args.args[0]
    assert saved.robots_noindex is True
    assert saved.sitemap_exclude is True
