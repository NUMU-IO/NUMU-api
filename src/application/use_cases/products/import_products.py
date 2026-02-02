"""Import products from CSV use case."""

import csv
import io
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from uuid import UUID

from slugify import slugify

from src.application.dto.base import BaseDTO
from src.core.entities.product import Product, ProductStatus, ProductType
from src.core.exceptions import AuthorizationError, EntityNotFoundError, ValidationError
from src.core.interfaces.repositories.product_repository import IProductRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.core.value_objects.money import Currency, Money

CSV_COLUMNS = [
    "name",
    "sku",
    "description",
    "short_description",
    "product_type",
    "status",
    "price",
    "price_currency",
    "compare_at_price",
    "cost_price",
    "quantity",
    "low_stock_threshold",
    "category_id",
    "tags",
    "images",
]

MAX_CSV_SIZE = 5 * 1024 * 1024  # 5 MB

VALID_PRODUCT_TYPES = {t.value for t in ProductType}
VALID_STATUSES = {s.value for s in ProductStatus}
VALID_CURRENCIES = {c.value for c in Currency}


@dataclass
class ImportRowError(BaseDTO):
    """Error for a single CSV row."""

    row: int
    field: str
    message: str


@dataclass
class ImportResultDTO(BaseDTO):
    """Result of a CSV import operation."""

    total_rows: int = 0
    created: int = 0
    updated: int = 0
    errors: list[ImportRowError] = field(default_factory=list)


class ImportProductsUseCase:
    """Use case for importing products from a CSV file."""

    def __init__(
        self,
        product_repository: IProductRepository,
        store_repository: IStoreRepository,
    ) -> None:
        self.product_repository = product_repository
        self.store_repository = store_repository

    async def execute(
        self,
        csv_content: bytes,
        store_id: UUID,
        user_id: UUID,
    ) -> ImportResultDTO:
        """Import products from CSV content.

        Args:
            csv_content: Raw CSV bytes.
            store_id: The store UUID.
            user_id: The user UUID (for authorization).

        Returns:
            ImportResultDTO with counts and any row-level errors.

        Raises:
            EntityNotFoundError: If store not found.
            AuthorizationError: If user doesn't own the store.
            ValidationError: If CSV is too large or unparseable.
        """
        if len(csv_content) > MAX_CSV_SIZE:
            raise ValidationError("CSV file exceeds maximum size of 5 MB")

        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))

        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to import products to this store"
            )

        try:
            text = csv_content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValidationError("CSV file must be UTF-8 encoded")

        reader = csv.DictReader(io.StringIO(text))

        result = ImportResultDTO()

        for row_num, row in enumerate(reader, start=2):  # row 1 is header
            result.total_rows += 1
            row_errors: list[ImportRowError] = []

            # Validate required: name
            name = (row.get("name") or "").strip()
            if not name:
                row_errors.append(ImportRowError(row=row_num, field="name", message="Name is required"))

            # Validate required: price
            price_str = (row.get("price") or "").strip()
            price_decimal: Decimal | None = None
            if not price_str:
                row_errors.append(ImportRowError(row=row_num, field="price", message="Price is required"))
            else:
                try:
                    price_decimal = Decimal(price_str)
                    if price_decimal <= 0:
                        row_errors.append(ImportRowError(row=row_num, field="price", message="Price must be greater than 0"))
                except InvalidOperation:
                    row_errors.append(ImportRowError(row=row_num, field="price", message=f"Invalid price value: {price_str}"))

            # Validate product_type
            product_type_str = (row.get("product_type") or "physical").strip().lower()
            if product_type_str not in VALID_PRODUCT_TYPES:
                row_errors.append(ImportRowError(row=row_num, field="product_type", message=f"Invalid product type: {product_type_str}"))
                product_type_str = "physical"

            # Validate status
            status_str = (row.get("status") or "draft").strip().lower()
            if status_str not in VALID_STATUSES:
                row_errors.append(ImportRowError(row=row_num, field="status", message=f"Invalid status: {status_str}"))
                status_str = "draft"

            # Validate currency
            currency_str = (row.get("price_currency") or "USD").strip().upper()
            if currency_str not in VALID_CURRENCIES:
                row_errors.append(ImportRowError(row=row_num, field="price_currency", message=f"Invalid currency: {currency_str}"))
                currency_str = "USD"

            # Parse optional decimal fields
            compare_at_price = self._parse_optional_decimal(row.get("compare_at_price"), row_num, "compare_at_price", row_errors)
            cost_price = self._parse_optional_decimal(row.get("cost_price"), row_num, "cost_price", row_errors)

            # Parse optional integer fields
            quantity = self._parse_optional_int(row.get("quantity"), row_num, "quantity", row_errors, default=0)
            low_stock_threshold = self._parse_optional_int(row.get("low_stock_threshold"), row_num, "low_stock_threshold", row_errors, default=5)

            if row_errors:
                result.errors.extend(row_errors)
                continue

            # At this point name and price_decimal are valid
            assert price_decimal is not None

            currency = Currency(currency_str)
            sku = (row.get("sku") or "").strip() or None
            description = (row.get("description") or "").strip() or None
            short_description = (row.get("short_description") or "").strip() or None
            category_id_str = (row.get("category_id") or "").strip()
            category_id: UUID | None = None
            if category_id_str:
                try:
                    category_id = UUID(category_id_str)
                except ValueError:
                    result.errors.append(ImportRowError(row=row_num, field="category_id", message=f"Invalid UUID: {category_id_str}"))
                    continue

            tags_str = (row.get("tags") or "").strip()
            tags = [t.strip() for t in tags_str.split("|") if t.strip()] if tags_str else []

            images_str = (row.get("images") or "").strip()
            images = [u.strip() for u in images_str.split("|") if u.strip()] if images_str else []

            # Check if product with this SKU already exists in store (update path)
            existing_product = None
            if sku:
                existing_product = await self.product_repository.get_by_sku(store_id, sku)

            if existing_product:
                # Update existing product
                existing_product.name = name
                existing_product.price = Money(amount=price_decimal, currency=currency)
                if description is not None:
                    existing_product.description = description
                if short_description is not None:
                    existing_product.short_description = short_description
                existing_product.product_type = ProductType(product_type_str)
                existing_product.status = ProductStatus(status_str)
                existing_product.quantity = quantity
                existing_product.low_stock_threshold = low_stock_threshold
                if compare_at_price is not None:
                    existing_product.compare_at_price = Money(amount=compare_at_price, currency=currency)
                if cost_price is not None:
                    existing_product.cost_price = Money(amount=cost_price, currency=currency)
                if category_id:
                    existing_product.category_id = category_id
                if tags:
                    existing_product.tags = tags
                if images:
                    existing_product.images = images

                await self.product_repository.update(existing_product)
                result.updated += 1
            else:
                # Create new product
                slug = slugify(name)
                existing_slug = await self.product_repository.get_by_slug(store_id, slug)
                if existing_slug:
                    import uuid as uuid_mod
                    slug = f"{slug}-{str(uuid_mod.uuid4())[:8]}"

                price = Money(amount=price_decimal, currency=currency)
                compare_money = Money(amount=compare_at_price, currency=currency) if compare_at_price else None
                cost_money = Money(amount=cost_price, currency=currency) if cost_price else None

                product = Product(
                    store_id=store_id,
                    name=name,
                    slug=slug,
                    sku=sku,
                    description=description,
                    short_description=short_description,
                    product_type=ProductType(product_type_str),
                    status=ProductStatus(status_str),
                    price=price,
                    compare_at_price=compare_money,
                    cost_price=cost_money,
                    quantity=quantity,
                    low_stock_threshold=low_stock_threshold,
                    images=images,
                    category_id=category_id,
                    tags=tags,
                )

                await self.product_repository.create(product)
                result.created += 1

        return result

    @staticmethod
    def _parse_optional_decimal(
        value: str | None,
        row_num: int,
        field_name: str,
        errors: list[ImportRowError],
    ) -> Decimal | None:
        val = (value or "").strip()
        if not val:
            return None
        try:
            d = Decimal(val)
            if d < 0:
                errors.append(ImportRowError(row=row_num, field=field_name, message=f"{field_name} cannot be negative"))
                return None
            return d
        except InvalidOperation:
            errors.append(ImportRowError(row=row_num, field=field_name, message=f"Invalid decimal value: {val}"))
            return None

    @staticmethod
    def _parse_optional_int(
        value: str | None,
        row_num: int,
        field_name: str,
        errors: list[ImportRowError],
        default: int = 0,
    ) -> int:
        val = (value or "").strip()
        if not val:
            return default
        try:
            i = int(val)
            if i < 0:
                errors.append(ImportRowError(row=row_num, field=field_name, message=f"{field_name} cannot be negative"))
                return default
            return i
        except ValueError:
            errors.append(ImportRowError(row=row_num, field=field_name, message=f"Invalid integer value: {val}"))
            return default
