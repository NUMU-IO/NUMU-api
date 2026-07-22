"""Get product use case."""

from uuid import UUID

from src.application.dto.product import ProductDTO
from src.core.exceptions import EntityNotFoundError
from src.core.interfaces.repositories.product_repository import IProductRepository


class GetProductUseCase:
    """Use case for getting a product."""

    def __init__(self, product_repository: IProductRepository) -> None:
        self.product_repository = product_repository

    async def execute(self, product_id: UUID, store_id: UUID) -> ProductDTO:
        """Get a product by ID, scoped to the store that owns it.

        `store_id` is REQUIRED and is the store the caller has already been
        authorised for. Without it this was a cross-tenant read: the route
        authenticates `/stores/{store_id}/products/{product_id}` via
        `verify_store_ownership`, but the lookup ignored the path store, so
        any authenticated merchant could read ANY product on the platform by
        id — across owners and tenants (verified 2026-07-21, CL-1).

        The repository's `_tenant_filter` does not save us here: tenant
        context is derived from the request's Host subdomain, and merchant
        traffic arrives on the apex host, so the filter is inert on this path.

        A foreign product raises EntityNotFoundError, not a permission error —
        the caller must not be able to distinguish "exists elsewhere" from
        "does not exist", or this becomes an id-enumeration oracle.
        """
        product = await self.product_repository.get_by_id(product_id)
        if not product or product.store_id != store_id:
            raise EntityNotFoundError("Product", str(product_id))
        return ProductDTO.from_entity(product)

    async def by_slug(self, store_id: UUID, slug: str) -> ProductDTO:
        """Get a product by slug within a store."""
        product = await self.product_repository.get_by_slug(store_id, slug)
        if not product:
            raise EntityNotFoundError("Product", slug)
        return ProductDTO.from_entity(product)
