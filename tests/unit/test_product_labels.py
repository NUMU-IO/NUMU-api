"""Unit tests for the Product Label feature (v1).

Covers the three verification gates:
  (a) product update round-trips ``attributes.label`` (set → get → clear)
      through UpdateProductUseCase's replace-attributes semantics,
  (b) product payloads (merchant + storefront share ProductResponse)
      surface a first-class ``label`` derived from ``attributes.label``,
  (c) the custom-labels settings endpoint never drops sibling settings
      keys (``settings.payment`` in particular).
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api.v1.routes.stores.settings import (
    get_product_labels,
    update_product_labels,
)
from src.api.v1.schemas.tenant.product import (
    CreateProductRequest,
    ProductResponse,
    UpdateProductRequest,
)
from src.api.v1.schemas.tenant.settings import (
    ProductLabelDef,
    UpdateProductLabelsRequest,
)
from src.application.dto.product import UpdateProductDTO
from src.application.use_cases.products.update_product import UpdateProductUseCase
from src.core.entities.product import Product, ProductStatus, ProductType
from src.core.value_objects.money import Currency, Money

SALE_LABEL = {"key": "sale", "text_en": "Sale", "text_ar": "تخفيض"}


def _make_product(**overrides) -> Product:
    defaults = {
        "id": uuid4(),
        "store_id": uuid4(),
        "name": "Test Product",
        "slug": "test-product",
        "sku": "SKU-001",
        "product_type": ProductType.PHYSICAL,
        "status": ProductStatus.ACTIVE,
        "price": Money(amount=Decimal("49.99"), currency=Currency.EGP),
        "quantity": 100,
        "attributes": {},
    }
    defaults.update(overrides)
    return Product(**defaults)


def _response_kwargs(**overrides) -> dict:
    base = {
        "id": str(uuid4()),
        "store_id": str(uuid4()),
        "name": "Test Product",
        "slug": "test-product",
        "sku": None,
        "description": None,
        "short_description": None,
        "product_type": "physical",
        "status": "active",
        "price": "49.99",
        "price_currency": "EGP",
        "compare_at_price": None,
        "cost_price": None,
        "quantity": 5,
        "is_in_stock": True,
        "is_low_stock": False,
        "is_on_sale": False,
        "images": [],
        "category_id": None,
        "tags": [],
        "attributes": {},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Request-schema normalization (attributes.label)
# ---------------------------------------------------------------------------


class TestLabelRequestNormalization:
    def test_create_request_keeps_valid_label_and_drops_extras(self):
        req = CreateProductRequest(
            name="x",
            price="10.00",
            attributes={"label": {**SALE_LABEL, "color": "#f00"}},
        )
        assert req.attributes["label"] == SALE_LABEL

    def test_create_request_drops_non_dict_label(self):
        req = CreateProductRequest(
            name="x", price="10.00", attributes={"label": "garbage"}
        )
        assert "label" not in req.attributes

    def test_create_request_drops_keyless_label(self):
        req = CreateProductRequest(
            name="x", price="10.00", attributes={"label": {"text_en": "Sale"}}
        )
        assert "label" not in req.attributes

    def test_update_request_normalizes_label(self):
        req = UpdateProductRequest(
            attributes={"label": {"key": "custom:eid-drop", "text_en": "Eid Drop"}}
        )
        assert req.attributes["label"] == {
            "key": "custom:eid-drop",
            "text_en": "Eid Drop",
            "text_ar": "",
        }

    def test_update_request_none_attributes_untouched(self):
        req = UpdateProductRequest(name="renamed")
        assert req.attributes is None

    def test_label_text_length_capped(self):
        with pytest.raises(ValidationError):
            CreateProductRequest(
                name="x",
                price="10.00",
                attributes={"label": {"key": "sale", "text_en": "x" * 81}},
            )


# ---------------------------------------------------------------------------
# (a) Update use case round-trip: set → get → clear
# ---------------------------------------------------------------------------


class TestLabelRoundTrip:
    def _use_case(self, product: Product) -> UpdateProductUseCase:
        product_repo = AsyncMock()
        product_repo.get_by_id.return_value = product
        # update() returns the (mutated) entity, mirroring the repository.
        product_repo.update.side_effect = lambda p: p
        store_repo = AsyncMock()
        store_repo.get_by_id.return_value = SimpleNamespace(owner_id=self.owner_id)
        return UpdateProductUseCase(product_repo, store_repo)

    def setup_method(self):
        self.owner_id = uuid4()

    @pytest.mark.asyncio
    async def test_set_then_clear_label(self):
        product = _make_product(attributes={"nameAr": "منتج"})
        use_case = self._use_case(product)

        # SET — hub sends full attributes including label.
        dto = UpdateProductDTO(attributes={"nameAr": "منتج", "label": dict(SALE_LABEL)})
        result = await use_case.execute(
            product.id, dto, self.owner_id, store_id=product.store_id
        )
        assert result.attributes["label"] == SALE_LABEL

        # GET — reading the entity back reflects the stored label.
        assert product.attributes["label"] == SALE_LABEL

        # CLEAR — hub resends full attributes WITHOUT the label key;
        # replace semantics must drop it.
        dto2 = UpdateProductDTO(attributes={"nameAr": "منتج"})
        result2 = await use_case.execute(
            product.id, dto2, self.owner_id, store_id=product.store_id
        )
        assert "label" not in result2.attributes
        assert "label" not in product.attributes

    @pytest.mark.asyncio
    async def test_unrelated_update_leaves_attributes_alone(self):
        product = _make_product(attributes={"label": dict(SALE_LABEL)})
        use_case = self._use_case(product)
        dto = UpdateProductDTO(name="Renamed")  # attributes=None → no touch
        result = await use_case.execute(
            product.id, dto, self.owner_id, store_id=product.store_id
        )
        assert result.attributes["label"] == SALE_LABEL


# ---------------------------------------------------------------------------
# (b) Response payloads carry a first-class `label`
# ---------------------------------------------------------------------------


class TestProductResponseLabel:
    def test_label_derived_from_attributes(self):
        resp = ProductResponse(
            **_response_kwargs(attributes={"label": dict(SALE_LABEL)})
        )
        assert resp.label is not None
        assert resp.label.key == "sale"
        assert resp.label.text_en == "Sale"
        assert resp.label.text_ar == SALE_LABEL["text_ar"]

    def test_label_none_when_absent(self):
        resp = ProductResponse(**_response_kwargs())
        assert resp.label is None

    def test_malformed_stored_label_degrades_to_none(self):
        resp = ProductResponse(**_response_kwargs(attributes={"label": {"key": ""}}))
        assert resp.label is None

    def test_label_serialized_in_json_payload(self):
        resp = ProductResponse(
            **_response_kwargs(attributes={"label": dict(SALE_LABEL)})
        )
        dumped = resp.model_dump()
        assert dumped["label"] == SALE_LABEL


# ---------------------------------------------------------------------------
# (c) Settings endpoint safety + parsing
# ---------------------------------------------------------------------------


class TestProductLabelsSettingsEndpoint:
    @pytest.mark.asyncio
    async def test_update_preserves_sibling_settings_keys(self):
        store = SimpleNamespace(
            settings={
                "payment": {"paymob": {"enabled": True, "api_key": "SECRET"}},
                "password_protected": {"enabled": False},
            }
        )
        store_repo = AsyncMock()
        request = UpdateProductLabelsRequest(
            labels=[
                ProductLabelDef(
                    key="custom:eid-drop", text_en="Eid Drop", text_ar="عرض العيد"
                )
            ]
        )

        result = await update_product_labels(request, store, store_repo, AsyncMock())

        # payment (and any other sibling key) must survive untouched.
        assert store.settings["payment"] == {
            "paymob": {"enabled": True, "api_key": "SECRET"}
        }
        assert store.settings["password_protected"] == {"enabled": False}
        assert store.settings["product_labels"] == [
            {"key": "custom:eid-drop", "text_en": "Eid Drop", "text_ar": "عرض العيد"}
        ]
        store_repo.update.assert_awaited_once_with(store)
        assert result.data.labels[0].key == "custom:eid-drop"

    @pytest.mark.asyncio
    async def test_update_dedupes_keys_keeping_last(self):
        store = SimpleNamespace(settings=None)
        request = UpdateProductLabelsRequest(
            labels=[
                ProductLabelDef(key="custom:drop", text_en="First"),
                ProductLabelDef(key="custom:drop", text_en="Second"),
            ]
        )
        result = await update_product_labels(request, store, AsyncMock(), AsyncMock())
        assert len(result.data.labels) == 1
        assert result.data.labels[0].text_en == "Second"

    @pytest.mark.asyncio
    async def test_rename_propagates_to_labeled_products(self):
        store = SimpleNamespace(
            id=uuid4(),
            settings={
                "product_labels": [
                    {"key": "custom:drop", "text_en": "Drop", "text_ar": "دروب"},
                    {"key": "custom:keep", "text_en": "Keep", "text_ar": ""},
                ]
            },
        )
        product_repo = AsyncMock()
        request = UpdateProductLabelsRequest(
            labels=[
                ProductLabelDef(
                    key="custom:drop", text_en="Summer Drop", text_ar="دروب الصيف"
                ),
                ProductLabelDef(key="custom:keep", text_en="Keep", text_ar=""),
            ]
        )

        await update_product_labels(request, store, AsyncMock(), product_repo)

        # Only the renamed key fans out; the unchanged one doesn't.
        product_repo.propagate_label_text.assert_awaited_once_with(
            store.id, "custom:drop", "Summer Drop", "دروب الصيف"
        )
        product_repo.clear_label.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_clears_label_from_products(self):
        store = SimpleNamespace(
            id=uuid4(),
            settings={
                "product_labels": [
                    {"key": "custom:gone", "text_en": "Gone", "text_ar": ""},
                    {"key": "custom:keep", "text_en": "Keep", "text_ar": ""},
                ]
            },
        )
        product_repo = AsyncMock()
        request = UpdateProductLabelsRequest(
            labels=[ProductLabelDef(key="custom:keep", text_en="Keep", text_ar="")]
        )

        await update_product_labels(request, store, AsyncMock(), product_repo)

        product_repo.clear_label.assert_awaited_once_with(store.id, "custom:gone")
        product_repo.propagate_label_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unchanged_definitions_touch_no_products(self):
        store = SimpleNamespace(
            id=uuid4(),
            settings={
                "product_labels": [
                    {"key": "custom:same", "text_en": "Same", "text_ar": "نفسه"}
                ]
            },
        )
        product_repo = AsyncMock()
        request = UpdateProductLabelsRequest(
            labels=[ProductLabelDef(key="custom:same", text_en="Same", text_ar="نفسه")]
        )

        await update_product_labels(request, store, AsyncMock(), product_repo)

        product_repo.propagate_label_text.assert_not_awaited()
        product_repo.clear_label.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_skips_malformed_rows(self):
        store = SimpleNamespace(
            settings={
                "product_labels": [
                    {"key": "custom:good", "text_en": "Good", "text_ar": ""},
                    {"key": "not-custom-prefixed", "text_en": "Bad"},
                    "garbage",
                    {"text_en": "keyless"},
                ]
            }
        )
        result = await get_product_labels(store)
        assert [label.key for label in result.data.labels] == ["custom:good"]

    @pytest.mark.asyncio
    async def test_get_empty_when_no_settings(self):
        result = await get_product_labels(SimpleNamespace(settings=None))
        assert result.data.labels == []

    def test_label_def_rejects_bad_key_format(self):
        for bad in ["sale", "custom:", "custom:UPPER", "custom:has space", "x:y"]:
            with pytest.raises(ValidationError):
                ProductLabelDef(key=bad, text_en="x")
