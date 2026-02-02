"""Export products to CSV use case."""

import csv
import io
from uuid import UUID

from src.application.use_cases.products.import_products import CSV_COLUMNS
from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.product_repository import IProductRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository


class ExportProductsUseCase:
    """Use case for exporting store products to CSV."""

    def __init__(
        self,
        product_repository: IProductRepository,
        store_repository: IStoreRepository,
    ) -> None:
        self.product_repository = product_repository
        self.store_repository = store_repository

    async def execute(
        self,
        store_id: UUID,
        user_id: UUID,
    ) -> str:
        """Export all products for a store as CSV.

        Args:
            store_id: The store UUID.
            user_id: The user UUID (for authorization).

        Returns:
            CSV content as a string.

        Raises:
            EntityNotFoundError: If store not found.
            AuthorizationError: If user doesn't own the store.
        """
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))

        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to export products from this store"
            )

        products = await self.product_repository.get_by_store(
            store_id, skip=0, limit=10000
        )

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
        writer.writeheader()

        for product in products:
            writer.writerow(
                {
                    "name": product.name,
                    "sku": product.sku or "",
                    "description": product.description or "",
                    "short_description": product.short_description or "",
                    "product_type": product.product_type.value,
                    "status": product.status.value,
                    "price": str(product.price.amount),
                    "price_currency": product.price.currency.value,
                    "compare_at_price": str(product.compare_at_price.amount) if product.compare_at_price else "",
                    "cost_price": str(product.cost_price.amount) if product.cost_price else "",
                    "quantity": product.quantity,
                    "low_stock_threshold": product.low_stock_threshold,
                    "category_id": str(product.category_id) if product.category_id else "",
                    "tags": "|".join(product.tags) if product.tags else "",
                    "images": "|".join(product.images) if product.images else "",
                }
            )

        return output.getvalue()
