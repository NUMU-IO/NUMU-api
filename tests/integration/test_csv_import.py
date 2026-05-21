"""Integration tests for CSV product import/export."""

import csv
import io
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.application.use_cases.products.export_products import ExportProductsUseCase
from src.application.use_cases.products.import_products import (
    CSV_COLUMNS,
    ImportProductsUseCase,
)
from src.core.entities.product import Product, ProductStatus, ProductType
from src.core.entities.store import Store, StoreStatus
from src.core.value_objects.money import Currency, Money


def _build_csv(rows: list[dict]) -> bytes:
    """Build CSV bytes from a list of row dicts."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue().encode("utf-8")


def _mock_store(store_id, owner_id):
    return Store(
        id=store_id,
        owner_id=owner_id,
        name="Test Store",
        slug="test-store",
        status=StoreStatus.ACTIVE,
        default_currency=Currency.EGP,
    )


# =============================================================================
# CSV Template Tests
# =============================================================================


class TestCSVTemplate:
    """Tests for the CSV template download."""

    def test_csv_columns_are_defined(self):
        assert "name" in CSV_COLUMNS
        assert "price" in CSV_COLUMNS
        assert "sku" in CSV_COLUMNS
        assert "quantity" in CSV_COLUMNS
        assert len(CSV_COLUMNS) == 15


# =============================================================================
# CSV Import Tests
# =============================================================================


class TestCSVImport:
    """Tests for ImportProductsUseCase."""

    def setup_method(self):
        self.store_id = uuid4()
        self.user_id = uuid4()
        self.store = _mock_store(self.store_id, self.user_id)

        self.product_repo = AsyncMock()
        self.store_repo = AsyncMock()
        self.store_repo.get_by_id = AsyncMock(return_value=self.store)
        self.product_repo.get_by_sku = AsyncMock(return_value=None)
        self.product_repo.get_by_slug = AsyncMock(return_value=None)
        self.product_repo.create = AsyncMock(side_effect=lambda p: p)
        self.product_repo.update = AsyncMock(side_effect=lambda p: p)

    def _make_use_case(self):
        return ImportProductsUseCase(
            product_repository=self.product_repo,
            store_repository=self.store_repo,
        )

    @pytest.mark.asyncio
    async def test_import_valid_csv(self):
        rows = [
            {"name": "Product A", "price": "10.50", "sku": "SKU-A", "quantity": "5"},
            {"name": "Product B", "price": "20.00", "sku": "SKU-B", "quantity": "10"},
            {"name": "Product C", "price": "5.99", "sku": "SKU-C", "quantity": "0"},
        ]
        csv_bytes = _build_csv(rows)

        result = await self._make_use_case().execute(
            csv_bytes, self.store_id, self.user_id
        )

        assert result.total_rows == 3
        assert result.created == 3
        assert result.updated == 0
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_import_csv_missing_required_fields(self):
        rows = [
            {"name": "", "price": "10.50"},  # missing name
            {"name": "Valid", "price": ""},  # missing price
        ]
        csv_bytes = _build_csv(rows)

        result = await self._make_use_case().execute(
            csv_bytes, self.store_id, self.user_id
        )

        assert result.total_rows == 2
        assert result.created == 0
        assert len(result.errors) == 2
        assert result.errors[0].field == "name"
        assert result.errors[1].field == "price"

    @pytest.mark.asyncio
    async def test_import_csv_invalid_price(self):
        rows = [{"name": "Bad Price", "price": "not_a_number"}]
        csv_bytes = _build_csv(rows)

        result = await self._make_use_case().execute(
            csv_bytes, self.store_id, self.user_id
        )

        assert result.created == 0
        assert len(result.errors) == 1
        assert result.errors[0].field == "price"
        assert "Invalid price" in result.errors[0].message

    @pytest.mark.asyncio
    async def test_import_csv_update_existing_by_sku(self):
        existing = Product(
            id=uuid4(),
            store_id=self.store_id,
            name="Old Name",
            slug="old-name",
            sku="SKU-EXISTING",
            product_type=ProductType.PHYSICAL,
            status=ProductStatus.DRAFT,
            price=Money(amount=Decimal("5.00"), currency=Currency.USD),
            quantity=1,
        )
        self.product_repo.get_by_sku = AsyncMock(return_value=existing)

        rows = [
            {
                "name": "Updated Name",
                "price": "15.00",
                "sku": "SKU-EXISTING",
                "quantity": "10",
            }
        ]
        csv_bytes = _build_csv(rows)

        result = await self._make_use_case().execute(
            csv_bytes, self.store_id, self.user_id
        )

        assert result.created == 0
        assert result.updated == 1
        assert result.errors == []
        self.product_repo.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_import_empty_csv(self):
        csv_bytes = _build_csv([])

        result = await self._make_use_case().execute(
            csv_bytes, self.store_id, self.user_id
        )

        assert result.total_rows == 0
        assert result.created == 0
        assert result.updated == 0

    @pytest.mark.asyncio
    async def test_import_csv_mixed_results(self):
        rows = [
            {"name": "Good Product", "price": "10.00", "sku": "GOOD-1"},
            {"name": "", "price": "5.00"},  # missing name
            {"name": "Another Good", "price": "20.00", "sku": "GOOD-2"},
        ]
        csv_bytes = _build_csv(rows)

        result = await self._make_use_case().execute(
            csv_bytes, self.store_id, self.user_id
        )

        assert result.total_rows == 3
        assert result.created == 2
        assert len(result.errors) == 1


# =============================================================================
# CSV Export Tests
# =============================================================================


class TestCSVExport:
    """Tests for ExportProductsUseCase."""

    def setup_method(self):
        self.store_id = uuid4()
        self.user_id = uuid4()
        self.store = _mock_store(self.store_id, self.user_id)

        self.product_repo = AsyncMock()
        self.store_repo = AsyncMock()
        self.store_repo.get_by_id = AsyncMock(return_value=self.store)

    def _make_use_case(self):
        return ExportProductsUseCase(
            product_repository=self.product_repo,
            store_repository=self.store_repo,
        )

    @pytest.mark.asyncio
    async def test_export_products_csv(self):
        products = [
            Product(
                id=uuid4(),
                store_id=self.store_id,
                name="Exported Product",
                slug="exported-product",
                sku="EXP-001",
                product_type=ProductType.PHYSICAL,
                status=ProductStatus.ACTIVE,
                price=Money(amount=Decimal("25.50"), currency=Currency.EGP),
                quantity=100,
                tags=["sale", "featured"],
            ),
        ]
        self.product_repo.get_by_store = AsyncMock(return_value=products)

        csv_str = await self._make_use_case().execute(self.store_id, self.user_id)

        reader = csv.DictReader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["name"] == "Exported Product"
        assert rows[0]["sku"] == "EXP-001"
        assert rows[0]["price"] == "25.50"
        assert rows[0]["tags"] == "sale|featured"

    @pytest.mark.asyncio
    async def test_export_empty_store(self):
        self.product_repo.get_by_store = AsyncMock(return_value=[])

        csv_str = await self._make_use_case().execute(self.store_id, self.user_id)

        reader = csv.DictReader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 0
        # Verify headers are still present
        assert reader.fieldnames == CSV_COLUMNS
