"""Automation rules & logs endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from src.api.dependencies.shopify import (
    get_automation_repo,
    verify_internal_key,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.shopify import (
    AutomationLogResponse,
    AutomationRuleResponse,
    AutomationRuleUpdateRequest,
    CreateFromTemplateRequest,
)
from src.infrastructure.repositories.shopify_repository import AutomationRepository

router = APIRouter(dependencies=[Depends(verify_internal_key)])

# ---------------------------------------------------------------------------
# Built-in automation templates
# ---------------------------------------------------------------------------
TEMPLATES: dict[str, dict] = {
    "cod_confirmation": {
        "name": "COD Order Confirmation",
        "description": "Send WhatsApp confirmation for every COD order before processing",
        "trigger_event": "order.created",
        "conditions": {"payment_method": "cod"},
        "actions": [
            {
                "type": "whatsapp_confirm",
                "template": "cod_confirmation",
                "timeout_hours": 24,
            }
        ],
        "priority": 10,
    },
    "high_value_cod_block": {
        "name": "High-Value COD Block",
        "description": "Automatically hold COD orders above threshold for manual review",
        "trigger_event": "order.created",
        "conditions": {"payment_method": "cod", "amount_gte_cents": 500000},
        "actions": [{"type": "hold_order", "reason": "high_value_cod"}],
        "priority": 20,
    },
    "trusted_customer": {
        "name": "Trusted Customer Fast-Track",
        "description": "Auto-approve orders from customers with 3+ successful orders",
        "trigger_event": "order.created",
        "conditions": {"min_previous_orders": 3, "previous_cancel_rate_lt": 0.1},
        "actions": [{"type": "auto_approve"}],
        "priority": 5,
    },
    "payment_retry": {
        "name": "Payment Retry Notification",
        "description": "Notify customer via WhatsApp when online payment fails",
        "trigger_event": "payment.failed",
        "conditions": {},
        "actions": [
            {
                "type": "whatsapp_notify",
                "template": "payment_retry",
                "include_retry_link": True,
            }
        ],
        "priority": 15,
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _rule_to_response(m) -> AutomationRuleResponse:  # noqa: ANN001
    return AutomationRuleResponse(
        id=str(m.id),
        name=m.name,
        description=m.description,
        is_active=m.is_active,
        priority=m.priority,
        trigger_event=m.trigger_event,
        conditions=m.conditions or {},
        actions=m.actions or [],
        times_triggered=m.times_triggered,
        last_triggered_at=m.last_triggered_at,
        created_at=m.created_at,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get(
    "/{store_id}/automation/rules",
    response_model=SuccessResponse[list[AutomationRuleResponse]],
    summary="List automation rules",
    operation_id="shopify_list_automation_rules",
)
async def list_rules(
    store_id: Annotated[UUID, Path()],
    repo: Annotated[AutomationRepository, Depends(get_automation_repo)],
):
    models = await repo.list_rules(store_id)
    return SuccessResponse(data=[_rule_to_response(m) for m in models])


@router.patch(
    "/{store_id}/automation/rules/{rule_id}",
    response_model=SuccessResponse[AutomationRuleResponse],
    summary="Update an automation rule (toggle, rename, etc.)",
    operation_id="shopify_update_automation_rule",
)
async def update_rule(
    store_id: Annotated[UUID, Path()],
    rule_id: Annotated[UUID, Path()],
    body: AutomationRuleUpdateRequest,
    repo: Annotated[AutomationRepository, Depends(get_automation_repo)],
):
    model = await repo.update_rule(rule_id, body.model_dump(exclude_unset=True))
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found"
        )
    return SuccessResponse(data=_rule_to_response(model), message="Rule updated")


@router.delete(
    "/{store_id}/automation/rules/{rule_id}",
    response_model=SuccessResponse[dict],
    summary="Delete an automation rule",
    operation_id="shopify_delete_automation_rule",
)
async def delete_rule(
    store_id: Annotated[UUID, Path()],
    rule_id: Annotated[UUID, Path()],
    repo: Annotated[AutomationRepository, Depends(get_automation_repo)],
):
    ok = await repo.delete_rule(rule_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found"
        )
    return SuccessResponse(data={"deleted": True}, message="Rule deleted")


@router.post(
    "/{store_id}/automation/rules/from-template",
    response_model=SuccessResponse[AutomationRuleResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a rule from a built-in template",
    operation_id="shopify_create_from_template",
)
async def create_from_template(
    store_id: Annotated[UUID, Path()],
    body: CreateFromTemplateRequest,
    repo: Annotated[AutomationRepository, Depends(get_automation_repo)],
):
    template = TEMPLATES.get(body.template_id)
    if not template:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown template '{body.template_id}'. Valid: {list(TEMPLATES.keys())}",
        )

    model = await repo.create_rule(
        store_id=store_id,
        name=template["name"],
        description=template["description"],
        trigger_event=template["trigger_event"],
        conditions=template["conditions"],
        actions=template["actions"],
        priority=template["priority"],
    )
    return SuccessResponse(
        data=_rule_to_response(model), message="Rule created from template"
    )


@router.get(
    "/{store_id}/automation/logs",
    response_model=SuccessResponse[list[AutomationLogResponse]],
    summary="Automation execution history",
    operation_id="shopify_list_automation_logs",
)
async def list_logs(
    store_id: Annotated[UUID, Path()],
    repo: Annotated[AutomationRepository, Depends(get_automation_repo)],
    limit: int = Query(20, ge=1, le=200),
):
    models = await repo.list_logs(store_id, limit=limit)
    items = [
        AutomationLogResponse(
            id=str(m.id),
            rule_name=m.rule_name,
            order_id=str(m.order_id) if m.order_id else None,
            order_number=m.order_number,
            trigger_event=m.trigger_event,
            actions_executed=m.actions_executed or [],
            status=m.status,
            error_details=m.error_details,
            created_at=m.created_at,
        )
        for m in models
    ]
    return SuccessResponse(data=items)
