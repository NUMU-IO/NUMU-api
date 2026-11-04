"""Core fixes App 04 («لمعة») needs, each proved against the code it changes:

- P1: the merchant product GET, list and PATCH return the saved alt text, so
  the hub's alt inputs stop loading empty and wiping it on blur;
- P3: a slug another product uses is refused, in the use case (hub, MCP and
  apps all go through it) and as a bilingual 409 on the route;
- P4: ``seo_title`` takes at most 60 characters, the column's size;
- P6: metafield values are written and validated by one service;
- P7: the shared Gemini client and JSON parser live outside the route.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.api.v1.routes.stores import ai as ai_routes
from src.api.v1.routes.stores import products as product_routes
from src.api.v1.schemas.tenant.product import (
    CreateProductRequest,
    UpdateProductRequest,
)
from src.application.dto.product import UpdateProductDTO
from src.application.services import metafield_values
from src.application.use_cases.products.update_product import (
    SlugTakenError,
    UpdateProductUseCase,
)
from src.core.entities.metafield import MetafieldOwnerType
from src.core.entities.product import ProductStatus
from src.infrastructure.database.models.tenant.metafield import (
    MetafieldDefinitionModel,
)
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.external_services import google_ai_client
from src.infrastructure.repositories.metafield_repository import (
    MetafieldDefinitionRepository,
    MetafieldValueRepository,
)
from src.infrastructure.repositories.product_repository import ProductRepository
from src.infrastructure.repositories.store_repository import StoreRepository
from tests.unit import app_platform_rows as rows

IMG = "https://cdn.numueg.app/products/a.webp"


@pytest.fixture
async def shop(test_session):
    owner = await rows.user(test_session)
    store_row = await rows.store(test_session, owner)
    products = [
        ProductModel(
            id=uuid4(),
            tenant_id=store_row.tenant_id,
            store_id=store_row.id,
            name=name,
            slug=slug,
            status=ProductStatus.ACTIVE,
            quantity=3,
            images=[IMG],
            extra_data={"media_urls": {IMG: {"alt": "فستان أزرق على مانيكان"}}},
        )
        for name, slug in (("Dress", "dress"), ("Scarf", "scarf"))
    ]
    test_session.add_all(products)
    await test_session.flush()
    store = await StoreRepository(test_session).get_by_id(store_row.id)
    return SimpleNamespace(
        db=test_session, store=store, owner=owner, dress=products[0], scarf=products[1]
    )


def _use_case(db):
    return UpdateProductUseCase(
        product_repository=ProductRepository(db), store_repository=StoreRepository(db)
    )


# ─── P1 ────────────────────────────────────────────────────────────────


async def test_merchant_reads_return_the_saved_alt_text(shop):
    repo = ProductRepository(shop.db)
    one = await product_routes.get_product(
        product_id=shop.dress.id, store=shop.store, product_repo=repo
    )
    assert one.data.image_alts == {IMG: "فستان أزرق على مانيكان"}

    page = await product_routes.list_products(
        store=shop.store,
        product_repo=repo,
        category_id=None,
        page=1,
        limit=20,
        product_status=None,
        search=None,
        sku=None,
        price_min=None,
        price_max=None,
        sort_by=None,
        sort_order="asc",
        has_cost=None,
    )
    items = page.data.items if hasattr(page.data, "items") else page.data["items"]
    assert {p.image_alts[IMG] for p in items} == {"فستان أزرق على مانيكان"}


async def test_the_update_response_carries_the_alt_text(shop):
    with patch(
        "src.infrastructure.external_services.nextjs_revalidation.revalidate_on_product_change",
        AsyncMock(),
    ):
        out = await product_routes.update_product(
            product_id=shop.dress.id,
            request=UpdateProductRequest(name="Blue dress"),
            store=shop.store,
            product_repo=ProductRepository(shop.db),
            store_repo=StoreRepository(shop.db),
        )
    assert out.data.name == "Blue dress"
    assert out.data.image_alts == {IMG: "فستان أزرق على مانيكان"}


# ─── P3 ────────────────────────────────────────────────────────────────


async def test_the_use_case_refuses_another_products_slug(shop):
    with pytest.raises(SlugTakenError) as caught:
        await _use_case(shop.db).execute(
            product_id=shop.dress.id,
            dto=UpdateProductDTO(slug="scarf"),
            user_id=shop.store.owner_id,
            store_id=shop.store.id,
        )
    assert caught.value.slug == "scarf"
    await shop.db.refresh(shop.dress)
    assert shop.dress.slug == "dress"


async def test_its_own_or_a_free_slug_still_saves_and_keeps_history(shop):
    uc = _use_case(shop.db)
    same = await uc.execute(
        product_id=shop.dress.id,
        dto=UpdateProductDTO(slug="dress"),
        user_id=shop.store.owner_id,
        store_id=shop.store.id,
    )
    assert same.slug == "dress"
    moved = await uc.execute(
        product_id=shop.dress.id,
        dto=UpdateProductDTO(slug="blue-dress"),
        user_id=shop.store.owner_id,
        store_id=shop.store.id,
    )
    assert moved.slug == "blue-dress"
    await shop.db.refresh(shop.dress)
    assert "dress" in (shop.dress.previous_slugs or [])


async def test_the_route_answers_a_taken_slug_with_a_bilingual_409(shop):
    with pytest.raises(HTTPException) as caught:
        await product_routes.update_product(
            product_id=shop.dress.id,
            request=UpdateProductRequest(slug="scarf"),
            store=shop.store,
            product_repo=ProductRepository(shop.db),
            store_repo=StoreRepository(shop.db),
        )
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "slug_taken"
    assert "scarf" in caught.value.detail["message"]
    assert "scarf" in caught.value.detail["message_ar"]


# ─── P4 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("model", [CreateProductRequest, UpdateProductRequest])
def test_seo_title_takes_at_most_the_columns_60_characters(model):
    extra = {"name": "Dress", "price": 100} if model is CreateProductRequest else {}
    assert model(seo_title="x" * 60, **extra).seo_title == "x" * 60
    with pytest.raises(ValidationError):
        model(seo_title="x" * 61, **extra)


# ─── P6 ────────────────────────────────────────────────────────────────


async def test_the_metafield_service_validates_and_upserts(shop):
    definition = MetafieldDefinitionModel(
        id=uuid4(),
        tenant_id=shop.store.tenant_id,
        store_id=shop.store.id,
        owner_type=MetafieldOwnerType.PRODUCT.value,
        namespace="faq",
        key="items",
        name="FAQ",
        type="json",
        is_public=True,
    )
    shop.db.add(definition)
    await shop.db.flush()
    found = await MetafieldDefinitionRepository(shop.db).get_by_key(
        shop.store.id, MetafieldOwnerType.PRODUCT, "faq", "items"
    )
    values = MetafieldValueRepository(shop.db)
    first = await metafield_values.write_value(
        values, shop.store, found, shop.dress.id, {"ar": [], "en": []}
    )
    again = await metafield_values.write_value(
        values,
        shop.store,
        found,
        shop.dress.id,
        {"ar": [{"question": "س", "answer": "ج"}]},
    )
    assert again.id == first.id
    assert "س" in str(again.value)

    number = await MetafieldDefinitionRepository(shop.db).get_by_key(
        shop.store.id, MetafieldOwnerType.PRODUCT, "faq", "items"
    )
    number.type = "number"
    with pytest.raises(ValueError):
        await metafield_values.write_value(
            values, shop.store, number, shop.dress.id, "not a number"
        )


# ─── P7 ────────────────────────────────────────────────────────────────


def test_the_shared_ai_client_and_parser_live_outside_the_route(monkeypatch):
    monkeypatch.setattr(google_ai_client.settings, "google_ai_api_key", None)
    with pytest.raises(google_ai_client.AIServiceNotConfigured):
        google_ai_client.numu_ai_client()
    with pytest.raises(HTTPException) as caught:
        ai_routes._numu_ai_client()
    assert caught.value.status_code == 503

    monkeypatch.setattr(google_ai_client.settings, "google_ai_api_key", "k")
    assert ai_routes._numu_ai_client().base_url is not None
    assert google_ai_client.parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert google_ai_client.parse_json_object("not json") == {}
    assert ai_routes.parse_json_object is google_ai_client.parse_json_object
