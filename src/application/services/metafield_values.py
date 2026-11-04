"""Metafield value writes and their cache busts, shared by the merchant route
and in-process writers (the Lamaa app's product FAQs).

``write_value`` validates against the definition's type and upserts; the
caller commits, then calls the two busts, so a reader never repopulates a
cache from the pre-commit row.
"""

from uuid import UUID

from src.core.entities.metafield import (
    MetafieldDefinition,
    MetafieldOwnerType,
    MetafieldValue,
    serialize_metafield_value,
)
from src.core.entities.store import Store
from src.infrastructure.cache import ProductCacheService
from src.infrastructure.repositories.metafield_repository import (
    MetafieldValueRepository,
)


async def invalidate_owner_cache(
    cache: ProductCacheService,
    store_id: UUID,
    owner_type: MetafieldOwnerType,
    owner_id: UUID,
) -> None:
    """Bust the storefront cache for the owner whose metafields changed, so a
    value edit shows up without waiting out the cached-payload TTL. Products
    carry metafields on their detail payload; collections on the category
    listing. Best-effort — a cache miss must never fail the write."""
    try:
        if owner_type == MetafieldOwnerType.PRODUCT:
            await cache.invalidate_product(store_id, owner_id)
        elif owner_type == MetafieldOwnerType.COLLECTION:
            await cache.invalidate_categories(store_id)
    except Exception:  # noqa: BLE001 — cache invalidation is best-effort
        pass


async def revalidate_storefront(store: Store) -> None:
    """Push the metafield change through to the Next.js storefront's ISR cache.

    Busting the API's Redis cache only makes the *API* correct — the storefront
    holds its own cached copy of the product payload in a different process, and
    until it is told otherwise it keeps serving the pre-change metafields to
    shoppers. For a field flipped public→private that is the difference between
    "private" and "private in about a minute", which is not what the setting
    promises. Best-effort: the helper logs and swallows its own failures.
    """
    if not store.subdomain:
        return
    from src.infrastructure.external_services.nextjs_revalidation import (
        revalidate_on_metafield_change,
    )

    await revalidate_on_metafield_change(
        subdomain=store.subdomain, store_id=str(store.id)
    )


async def write_value(
    value_repo: MetafieldValueRepository,
    store: Store,
    definition: MetafieldDefinition,
    owner_id: UUID,
    value: object,
) -> MetafieldValue:
    """Validate ``value`` against the definition's type and upsert it.
    Raises ``ValueError`` when the value does not fit the type."""
    serialized = serialize_metafield_value(definition.type, value)
    existing = await value_repo.get_by_definition_and_owner(definition.id, owner_id)
    if existing:
        existing.value = serialized
        return await value_repo.update(existing)
    return await value_repo.create(
        MetafieldValue(
            store_id=store.id,
            tenant_id=store.tenant_id,
            definition_id=definition.id,
            owner_id=owner_id,
            value=serialized,
        )
    )
