"""Meta Commerce Catalog client."""

from typing import Any

from src.config.logging_config import get_logger
from src.infrastructure.external_services.meta.graph_client import (
    MetaGraphClient,
)

logger = get_logger(__name__)


class CatalogClient:
    """Client for Meta Commerce Catalog API."""

    def __init__(self, catalog_id: str, access_token: str):
        self.catalog_id = catalog_id
        self.client = MetaGraphClient(access_token)

    async def close(self) -> None:
        await self.client.close()

    async def get_catalog(self) -> dict[str, Any]:
        """Get catalog details."""
        endpoint = f"{self.catalog_id}"
        params = {
            "fields": "id,name,vertical,product_count,merchant_id",
        }

        logger.debug("catalog_get", catalog_id=self.catalog_id)

        return await self.client.get(endpoint, params)

    async def get_products(
        self,
        limit: int = 25,
        after: str | None = None,
    ) -> dict[str, Any]:
        """Get products in the catalog."""
        endpoint = f"{self.catalog_id}/products"
        params = {
            "fields": "id,name,description,image_url,price,availability,extra_data",
            "limit": limit,
        }
        if after:
            params["after"] = after

        logger.debug("catalog_get_products", catalog_id=self.catalog_id, limit=limit)

        return await self.client.get(endpoint, params)

    async def get_product(self, product_id: str) -> dict[str, Any]:
        """Get a specific product."""
        endpoint = f"{product_id}"
        params = {
            "fields": "id,name,description,image_url,price,availability,extra_data,retailer_id",
        }

        logger.debug("catalog_get_product", product_id=product_id)

        return await self.client.get(endpoint, params)

    async def create_product(
        self,
        retailer_id: str,
        name: str,
        description: str | None = None,
        price: float | None = None,
        currency: str = "EGP",
        image_url: str | None = None,
        availability: str = "in stock",
        extra_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new product in the catalog."""
        endpoint = f"{self.catalog_id}/products"
        data: dict[str, Any] = {
            "retailer_id": retailer_id,
            "name": name,
            "availability": availability,
        }

        if description:
            data["description"] = description
        if price is not None:
            data["price"] = {"amount": str(price), "currency": currency}
        if image_url:
            data["image_url"] = image_url
        if extra_data:
            data["extra_data"] = extra_data

        logger.info("catalog_create_product", retailer_id=retailer_id, name=name)

        return await self.client.post(endpoint, data)

    async def update_product(
        self,
        product_id: str,
        name: str | None = None,
        description: str | None = None,
        price: float | None = None,
        currency: str = "EGP",
        image_url: str | None = None,
        availability: str | None = None,
        extra_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update an existing product."""
        data: dict[str, Any] = {}

        if name is not None:
            data["name"] = name
        if description is not None:
            data["description"] = description
        if price is not None:
            data["price"] = {"amount": str(price), "currency": currency}
        if image_url is not None:
            data["image_url"] = image_url
        if availability is not None:
            data["availability"] = availability
        if extra_data is not None:
            data["extra_data"] = extra_data

        if not data:
            raise ValueError("No fields to update")

        endpoint = product_id

        logger.info(
            "catalog_update_product", product_id=product_id, fields=list(data.keys())
        )

        return await self.client.post(endpoint, data)

    async def delete_product(self, product_id: str) -> bool:
        """Delete a product from the catalog."""
        endpoint = product_id

        logger.info("catalog_delete_product", product_id=product_id)

        await self.client.delete(endpoint)
        return True

    async def batch_upsert(
        self,
        entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Batch create/update products using Batch API."""
        endpoint = f"{self.catalog_id}/products/batch"
        data = {
            "entries": entries,
        }

        logger.info("catalog_batch_upsert", entry_count=len(entries))

        return await self.client.post(endpoint, data)

    async def update_availability(
        self,
        product_id: str,
        availability: str,
    ) -> dict[str, Any]:
        """Update product availability (in stock / out of stock)."""
        return await self.update_product(product_id, availability=availability)

    async def update_visibility(
        self,
        product_id: str,
        visible: bool,
    ) -> dict[str, Any]:
        """Update product visibility."""
        visibility = "active" if visible else "hidden"
        return await self.update_product(product_id, availability=visibility)
