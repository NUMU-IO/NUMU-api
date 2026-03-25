"""Upsell rule routes nested under stores.

URL: /stores/{store_id}/upsells
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from src.api.dependencies import (
    verify_store_ownership,
)
from src.api.dependencies.repositories import get_upsell_rule_repository
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.upsell import (
    CreateUpsellRuleRequest,
    UpdateUpsellRuleRequest,
    UpsellRuleResponse,
)
from src.core.entities.store import Store
from src.infrastructure.repositories.upsell_rule_repository import (
    UpsellRuleRepository,
)

router = APIRouter(prefix="/{store_id}/upsells")


def _rule_response(rule) -> UpsellRuleResponse:
    """Convert UpsellRuleModel to UpsellRuleResponse."""
    return UpsellRuleResponse(
        id=str(rule.id),
        store_id=str(rule.store_id),
        name=rule.name,
        is_active=rule.is_active,
        trigger_type=rule.trigger_type,
        trigger_product_ids=[str(pid) for pid in (rule.trigger_product_ids or [])],
        trigger_category_ids=[str(cid) for cid in (rule.trigger_category_ids or [])],
        trigger_min_cart_value=rule.trigger_min_cart_value,
        offer_product_id=str(rule.offer_product_id),
        discount_type=rule.discount_type,
        discount_value=rule.discount_value,
        priority=rule.priority,
        max_uses=rule.max_uses,
        uses_count=rule.uses_count,
        headline_ar=rule.headline_ar,
        headline_en=rule.headline_en,
        description_ar=rule.description_ar,
        description_en=rule.description_en,
        created_at=str(rule.created_at),
        updated_at=str(rule.updated_at),
    )


@router.post(
    "/",
    response_model=SuccessResponse[UpsellRuleResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create upsell rule",
    operation_id="create_upsell_rule",
)
async def create_upsell_rule(
    request: CreateUpsellRuleRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    upsell_repo: Annotated[UpsellRuleRepository, Depends(get_upsell_rule_repository)],
):
    """Create a new upsell rule for the store."""
    data = {
        "store_id": store.id,
        "tenant_id": store.tenant_id,
        "name": request.name,
        "is_active": request.is_active,
        "trigger_type": request.trigger_type,
        "trigger_product_ids": request.trigger_product_ids or [],
        "trigger_category_ids": request.trigger_category_ids or [],
        "trigger_min_cart_value": request.trigger_min_cart_value,
        "offer_product_id": UUID(request.offer_product_id),
        "discount_type": request.discount_type,
        "discount_value": request.discount_value,
        "priority": request.priority,
        "max_uses": request.max_uses,
        "headline_ar": request.headline_ar,
        "headline_en": request.headline_en,
        "description_ar": request.description_ar,
        "description_en": request.description_en,
    }
    rule = await upsell_repo.create(data)

    return SuccessResponse(
        data=_rule_response(rule),
        message="Upsell rule created successfully",
    )


@router.get(
    "/",
    response_model=SuccessResponse[list[UpsellRuleResponse]],
    summary="List upsell rules",
    operation_id="list_upsell_rules",
)
async def list_upsell_rules(
    store: Annotated[Store, Depends(verify_store_ownership)],
    upsell_repo: Annotated[UpsellRuleRepository, Depends(get_upsell_rule_repository)],
    active_only: bool = Query(False, description="Filter to active rules only"),
):
    """List upsell rules for a store."""
    rules = await upsell_repo.list_by_store(store_id=store.id, active_only=active_only)

    return SuccessResponse(
        data=[_rule_response(r) for r in rules],
        message="Upsell rules retrieved successfully",
    )


@router.get(
    "/{rule_id}",
    response_model=SuccessResponse[UpsellRuleResponse],
    summary="Get upsell rule",
    operation_id="get_upsell_rule",
)
async def get_upsell_rule(
    rule_id: Annotated[UUID, Path(description="Upsell rule ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    upsell_repo: Annotated[UpsellRuleRepository, Depends(get_upsell_rule_repository)],
):
    """Get upsell rule details by ID."""
    rule = await upsell_repo.get_by_id(rule_id)
    if not rule or rule.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Upsell rule not found",
        )

    return SuccessResponse(
        data=_rule_response(rule),
        message="Upsell rule retrieved successfully",
    )


@router.patch(
    "/{rule_id}",
    response_model=SuccessResponse[UpsellRuleResponse],
    summary="Update upsell rule",
    operation_id="update_upsell_rule",
)
async def update_upsell_rule(
    rule_id: Annotated[UUID, Path(description="Upsell rule ID")],
    request: UpdateUpsellRuleRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    upsell_repo: Annotated[UpsellRuleRepository, Depends(get_upsell_rule_repository)],
):
    """Update upsell rule details."""
    rule = await upsell_repo.get_by_id(rule_id)
    if not rule or rule.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Upsell rule not found",
        )

    update_data = request.model_dump(exclude_unset=True)
    if "offer_product_id" in update_data and update_data["offer_product_id"]:
        update_data["offer_product_id"] = UUID(update_data["offer_product_id"])

    for field, value in update_data.items():
        setattr(rule, field, value)

    rule = await upsell_repo.update(rule)

    return SuccessResponse(
        data=_rule_response(rule),
        message="Upsell rule updated successfully",
    )


@router.delete(
    "/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete upsell rule",
    operation_id="delete_upsell_rule",
)
async def delete_upsell_rule(
    rule_id: Annotated[UUID, Path(description="Upsell rule ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    upsell_repo: Annotated[UpsellRuleRepository, Depends(get_upsell_rule_repository)],
):
    """Delete an upsell rule."""
    deleted = await upsell_repo.delete(rule_id=rule_id, store_id=store.id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Upsell rule not found",
        )
    return None
