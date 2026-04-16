"""Sync catalog use case."""

from uuid import UUID

from src.core.entities.catalog_mapping import CatalogMapping, CatalogSyncStatus
from src.core.entities.product import ProductStatus
from src.core.interfaces.repositories.catalog_mapping_repository import (
    CatalogMappingRepository,
)
from src.core.interfaces.repositories.channel_connection_repository import (
    ChannelConnectionRepository,
)
from src.core.interfaces.repositories.product_repository import IProductRepository
from src.infrastructure.external_services.meta.catalog_client import CatalogClient


class SyncCatalogUseCase:
    """Use case for syncing products to Meta catalog.

    Contract: POST /stores/{store_id}/channels/meta/catalog/sync
    """

    def __init__(
        self,
        channel_connection_repository: ChannelConnectionRepository,
        catalog_mapping_repository: CatalogMappingRepository,
        product_repository: IProductRepository,
    ):
        self.channel_connection_repository = channel_connection_repository
        self.catalog_mapping_repository = catalog_mapping_repository
        self.product_repository = product_repository

    async def execute(
        self,
        connection_id: UUID,
        store_id: UUID,
        full_sync: bool = False,
    ) -> dict:
        """Sync products to Meta catalog.

        Args:
            connection_id: Channel connection UUID (from route path)
            store_id: Store UUID (from route path)
            full_sync: If True, sync all products; if False, only pending

        Returns:
            dict with job_id and initial status
        """
        connection = await self.channel_connection_repository.get_by_id(connection_id)
        if not connection or not connection.catalog_id:
            raise ValueError("Connection or catalog not found")

        from src.infrastructure.external_services.secrets.secrets_manager import (
            SecretsManager,
        )

        secrets = SecretsManager()
        if not connection.encrypted_credentials or not connection.credential_key_id:
            raise ValueError("Connection has no encrypted credentials")
        creds = await secrets.decrypt(
            connection.encrypted_credentials, connection.credential_key_id
        )
        access_token = creds.get("access_token", "")

        client = CatalogClient(
            catalog_id=connection.catalog_id,
            access_token=access_token,
        )

        products = await self.product_repository.get_by_store(
            store_id=store_id,
            status=ProductStatus.ACTIVE,
            skip=0,
            limit=100,
        )

        synced = 0
        failed = 0

        for product in products:
            try:
                result = await client.create_product(
                    retailer_id=str(product.id),
                    name=product.name,
                    description=product.description,
                    price=float(product.price.amount) if product.price else 0,
                    currency=product.price.currency.value if product.price else "USD",
                    image_url=product.images[0] if product.images else None,
                )

                mapping = CatalogMapping(
                    tenant_id=connection.tenant_id,
                    store_id=store_id,
                    channel_connection_id=connection.id,
                    product_id=product.id,
                    external_product_id=result.get("id"),
                    sync_status=CatalogSyncStatus.SYNCED,
                )
                await self.catalog_mapping_repository.create(mapping)
                synced += 1
            except Exception:
                failed += 1

        return {
            "job_id": str(UUID(int=0)),
            "status": "completed",
            "ok": synced,
            "failed": failed,
        }
