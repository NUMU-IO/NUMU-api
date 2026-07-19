"""Metafield routes nested under stores.

URL: /stores/{store_id}/metafields

Two resources:
  - ``/definitions`` — CRUD on the typed field schema (per store).
  - ``/owners/{owner_type}/{owner_id}`` — set/get concrete values on a
    specific product/collection/page.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.repositories import (
    get_metafield_definition_repository,
    get_metafield_value_repository,
)
from src.api.dependencies.services import get_product_cache_service
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.metafield import (
    CreateMetafieldDefinitionRequest,
    MetafieldDefinitionResponse,
    MetafieldValueResponse,
    SetMetafieldValueRequest,
    UpdateMetafieldDefinitionRequest,
)
from src.core.entities.metafield import (
    MetafieldDefinition,
    MetafieldOwnerType,
    MetafieldValue,
    coerce_metafield_value,
    serialize_metafield_value,
)
from src.core.entities.store import Store
from src.infrastructure.cache import ProductCacheService
from src.infrastructure.repositories.metafield_repository import (
    MetafieldDefinitionRepository,
    MetafieldValueRepository,
)

router = APIRouter(prefix="/{store_id}/metafields")


async def _invalidate_owner_cache(
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


def _definition_response(entity: MetafieldDefinition) -> MetafieldDefinitionResponse:
    return MetafieldDefinitionResponse(
        id=str(entity.id),
        store_id=str(entity.store_id),
        owner_type=entity.owner_type,
        namespace=entity.namespace,
        key=entity.key,
        type=entity.type,
        name=entity.name,
        description=entity.description,
        is_public=entity.is_public,
        created_at=str(entity.created_at),
        updated_at=str(entity.updated_at),
    )


def _value_response(
    value: MetafieldValue, definition: MetafieldDefinition
) -> MetafieldValueResponse:
    return MetafieldValueResponse(
        id=str(value.id),
        definition_id=str(value.definition_id),
        owner_id=str(value.owner_id),
        namespace=definition.namespace,
        key=definition.key,
        type=definition.type,
        value=coerce_metafield_value(definition.type, value.value),
        raw_value=value.value,
        created_at=str(value.created_at),
        updated_at=str(value.updated_at),
    )


# ── Definitions ──────────────────────────────────────────────────────────────


@router.get(
    "/definitions",
    response_model=SuccessResponse[list[MetafieldDefinitionResponse]],
    summary="List metafield definitions",
    operation_id="list_metafield_definitions",
)
async def list_metafield_definitions(
    store: Annotated[Store, Depends(verify_store_ownership)],
    def_repo: Annotated[
        MetafieldDefinitionRepository, Depends(get_metafield_definition_repository)
    ],
    owner_type: Annotated[
        MetafieldOwnerType | None, Query(description="Filter by owner type")
    ] = None,
):
    """List the store's typed metafield definitions."""
    definitions = await def_repo.get_by_store(store.id, owner_type=owner_type)
    return SuccessResponse(
        data=[_definition_response(d) for d in definitions],
        message="Metafield definitions retrieved successfully",
    )


@router.post(
    "/definitions",
    response_model=SuccessResponse[MetafieldDefinitionResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a metafield definition",
    operation_id="create_metafield_definition",
)
async def create_metafield_definition(
    request: CreateMetafieldDefinitionRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    def_repo: Annotated[
        MetafieldDefinitionRepository, Depends(get_metafield_definition_repository)
    ],
):
    """Declare a new typed field. The ``namespace.key`` must be unique per
    ``(store, owner_type)``."""
    existing = await def_repo.get_by_key(
        store.id, request.owner_type, request.namespace, request.key
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A {request.owner_type.value} metafield "
                f"'{request.namespace}.{request.key}' already exists"
            ),
        )
    definition = MetafieldDefinition(
        store_id=store.id,
        tenant_id=store.tenant_id,
        owner_type=request.owner_type,
        namespace=request.namespace,
        key=request.key,
        type=request.type,
        name=request.name,
        description=request.description,
        is_public=request.is_public,
    )
    created = await def_repo.create(definition)
    return SuccessResponse(
        data=_definition_response(created),
        message="Metafield definition created successfully",
    )


@router.put(
    "/definitions/{definition_id}",
    response_model=SuccessResponse[MetafieldDefinitionResponse],
    summary="Update a metafield definition",
    operation_id="update_metafield_definition",
)
async def update_metafield_definition(
    definition_id: Annotated[UUID, Path(description="Definition UUID")],
    request: UpdateMetafieldDefinitionRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    def_repo: Annotated[
        MetafieldDefinitionRepository, Depends(get_metafield_definition_repository)
    ],
):
    """Update display metadata / type / visibility (address is immutable)."""
    definition = await def_repo.get_by_id(definition_id)
    if not definition or definition.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metafield definition not found",
        )
    if request.type is not None:
        definition.type = request.type
    if request.name is not None:
        definition.name = request.name
    if request.description is not None:
        definition.description = request.description
    if request.is_public is not None:
        definition.is_public = request.is_public
    updated = await def_repo.update(definition)
    return SuccessResponse(
        data=_definition_response(updated),
        message="Metafield definition updated successfully",
    )


@router.delete(
    "/definitions/{definition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a metafield definition",
    operation_id="delete_metafield_definition",
)
async def delete_metafield_definition(
    definition_id: Annotated[UUID, Path(description="Definition UUID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    def_repo: Annotated[
        MetafieldDefinitionRepository, Depends(get_metafield_definition_repository)
    ],
):
    """Delete a definition and (via FK cascade) all its values."""
    definition = await def_repo.get_by_id(definition_id)
    if not definition or definition.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metafield definition not found",
        )
    await def_repo.delete(definition_id)
    return None


# ── Values (per owner) ───────────────────────────────────────────────────────


@router.get(
    "/owners/{owner_type}/{owner_id}",
    response_model=SuccessResponse[list[MetafieldValueResponse]],
    summary="List an owner's metafield values",
    operation_id="list_owner_metafield_values",
)
async def list_owner_metafield_values(
    owner_type: Annotated[MetafieldOwnerType, Path(description="Owner resource type")],
    owner_id: Annotated[UUID, Path(description="Owner UUID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    def_repo: Annotated[
        MetafieldDefinitionRepository, Depends(get_metafield_definition_repository)
    ],
    value_repo: Annotated[
        MetafieldValueRepository, Depends(get_metafield_value_repository)
    ],
):
    """List all metafield values (public + private) set on one owner."""
    definitions = {
        d.id: d for d in await def_repo.get_by_store(store.id, owner_type=owner_type)
    }
    values = await value_repo.get_for_owner(store.id, owner_id)
    data = [
        _value_response(v, definitions[v.definition_id])
        for v in values
        if v.definition_id in definitions
    ]
    return SuccessResponse(data=data, message="Metafield values retrieved successfully")


@router.put(
    "/owners/{owner_type}/{owner_id}",
    response_model=SuccessResponse[MetafieldValueResponse],
    summary="Set (upsert) a metafield value on an owner",
    operation_id="set_owner_metafield_value",
)
async def set_owner_metafield_value(
    owner_type: Annotated[MetafieldOwnerType, Path(description="Owner resource type")],
    owner_id: Annotated[UUID, Path(description="Owner UUID")],
    request: SetMetafieldValueRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    def_repo: Annotated[
        MetafieldDefinitionRepository, Depends(get_metafield_definition_repository)
    ],
    value_repo: Annotated[
        MetafieldValueRepository, Depends(get_metafield_value_repository)
    ],
    product_cache: Annotated[ProductCacheService, Depends(get_product_cache_service)],
):
    """Set a value for a defined field. The value is validated against the
    definition's declared type before it is stored."""
    definition = await def_repo.get_by_key(
        store.id, owner_type, request.namespace, request.key
    )
    if not definition:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No {owner_type.value} metafield definition "
                f"'{request.namespace}.{request.key}' — define it first"
            ),
        )
    try:
        serialized = serialize_metafield_value(definition.type, request.value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )

    existing = await value_repo.get_by_definition_and_owner(definition.id, owner_id)
    if existing:
        existing.value = serialized
        saved = await value_repo.update(existing)
    else:
        saved = await value_repo.create(
            MetafieldValue(
                store_id=store.id,
                tenant_id=store.tenant_id,
                definition_id=definition.id,
                owner_id=owner_id,
                value=serialized,
            )
        )
    await _invalidate_owner_cache(product_cache, store.id, owner_type, owner_id)
    return SuccessResponse(
        data=_value_response(saved, definition),
        message="Metafield value saved successfully",
    )


@router.delete(
    "/owners/{owner_type}/{owner_id}/{namespace}/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unset a metafield value on an owner",
    operation_id="unset_owner_metafield_value",
)
async def unset_owner_metafield_value(
    owner_type: Annotated[MetafieldOwnerType, Path(description="Owner resource type")],
    owner_id: Annotated[UUID, Path(description="Owner UUID")],
    namespace: Annotated[str, Path(description="Definition namespace")],
    key: Annotated[str, Path(description="Definition key")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    def_repo: Annotated[
        MetafieldDefinitionRepository, Depends(get_metafield_definition_repository)
    ],
    value_repo: Annotated[
        MetafieldValueRepository, Depends(get_metafield_value_repository)
    ],
    product_cache: Annotated[ProductCacheService, Depends(get_product_cache_service)],
):
    """Remove one owner's value for a field (the definition stays). The
    storefront then renders the setting's default — the contract's
    missing-value behavior, not an error."""
    definition = await def_repo.get_by_key(store.id, owner_type, namespace, key)
    if not definition:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {owner_type.value} metafield definition '{namespace}.{key}'",
        )
    existing = await value_repo.get_by_definition_and_owner(definition.id, owner_id)
    if existing:
        await value_repo.delete(existing.id)
        await _invalidate_owner_cache(product_cache, store.id, owner_type, owner_id)
    return None


@router.delete(
    "/owners/{owner_type}/{owner_id}/{namespace}/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a metafield value from an owner",
    operation_id="delete_owner_metafield_value",
)
async def delete_owner_metafield_value(
    owner_type: Annotated[MetafieldOwnerType, Path(description="Owner resource type")],
    owner_id: Annotated[UUID, Path(description="Owner UUID")],
    namespace: Annotated[str, Path(description="Definition namespace")],
    key: Annotated[str, Path(description="Definition key")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    def_repo: Annotated[
        MetafieldDefinitionRepository, Depends(get_metafield_definition_repository)
    ],
    value_repo: Annotated[
        MetafieldValueRepository, Depends(get_metafield_value_repository)
    ],
):
    """Delete the value for a (definition, owner) pair."""
    definition = await def_repo.get_by_key(store.id, owner_type, namespace, key)
    if not definition:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metafield definition not found",
        )
    value = await value_repo.get_by_definition_and_owner(definition.id, owner_id)
    if not value:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metafield value not found",
        )
    await value_repo.delete(value.id)
    return None
