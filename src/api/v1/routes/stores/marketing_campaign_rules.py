"""Auto-match rules CRUD — feature 002 US4.

Endpoints under /api/v1/stores/{store_id}/marketing/campaigns/{campaign_id}/auto-match-rules.
A rule "group" (UI unit) maps to N database rows sharing a group_id,
combined per the group's combinator (AND/OR).
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.dependencies import get_current_user_id, verify_store_ownership
from src.api.responses import SuccessResponse
from src.application.services.audit_service import AuditService, EventType
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.repositories.campaign_auto_match_repository import (
    CampaignAutoMatchRepository,
    RuleCondition,
)
from src.infrastructure.repositories.marketing_campaign_repository import (
    MarketingCampaignRepository,
)

router = APIRouter(
    prefix="/{store_id}/marketing/campaigns/{campaign_id}/auto-match-rules",
    tags=["Marketing Campaign Auto-Match Rules"],
    dependencies=[Depends(verify_store_ownership)],
)


# ── Schemas ──────────────────────────────────────────────────────


class ConditionInput(BaseModel):
    field: Literal["utm_source", "utm_medium", "utm_campaign"]
    operator: Literal["equals", "starts_with", "contains"]
    value: str = Field(min_length=1, max_length=200)


class CreateRuleRequest(BaseModel):
    combinator: Literal["AND", "OR"]
    priority: int = Field(ge=0)
    conditions: list[ConditionInput] = Field(min_length=1, max_length=10)


class ConditionResponse(BaseModel):
    field: str
    operator: str
    value: str


class RuleWarning(BaseModel):
    code: str
    message: str


class RuleResponse(BaseModel):
    group_id: str
    campaign_id: str
    combinator: str
    priority: int
    conditions: list[ConditionResponse]


class CreateRuleResponse(BaseModel):
    rule: RuleResponse
    warnings: list[RuleWarning] = []


# ── Routes ───────────────────────────────────────────────────────


async def _load_campaign_or_404(store_id: UUID, campaign_id: UUID):
    async with AsyncSessionLocal() as session:
        repo = MarketingCampaignRepository(session)
        campaign = await repo.get_by_id(campaign_id)
        if campaign is None or campaign.store_id != store_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Campaign not found",
            )
        return campaign


def _normalize_value(value: str) -> str:
    """UTM normalization at write time — must match the read-side
    sanitize_utm semantics (lowercased + trimmed) so rule comparisons
    succeed against the already-normalized UTMs flowing through ingest.
    """
    return value.strip().lower()


@router.get(
    "",
    response_model=SuccessResponse[list[RuleResponse]],
    summary="List auto-match rules for a campaign",
    operation_id="list_campaign_auto_match_rules",
)
async def list_auto_match_rules(store_id: UUID, campaign_id: UUID):
    await _load_campaign_or_404(store_id, campaign_id)
    async with AsyncSessionLocal() as session:
        repo = CampaignAutoMatchRepository(session)
        groups = await repo.list_for_campaign(campaign_id)
    return SuccessResponse(
        data=[
            RuleResponse(
                group_id=str(g.group_id),
                campaign_id=str(g.campaign_id),
                combinator=g.combinator,
                priority=g.priority,
                conditions=[ConditionResponse(**c.__dict__) for c in g.conditions],
            )
            for g in groups
        ],
        message="Auto-match rules for campaign",
    )


@router.post(
    "",
    response_model=SuccessResponse[CreateRuleResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create an auto-match rule group",
    operation_id="create_campaign_auto_match_rule",
)
async def create_auto_match_rule(
    store_id: UUID,
    campaign_id: UUID,
    body: CreateRuleRequest,
    user_id: UUID = Depends(get_current_user_id),
):
    campaign = await _load_campaign_or_404(store_id, campaign_id)

    # Server-side normalization: lowercase + trim so rule matches the
    # already-normalized UTM strings flowing through ingest.
    conditions = [
        RuleCondition(
            field=c.field,
            operator=c.operator,
            value=_normalize_value(c.value),
        )
        for c in body.conditions
    ]

    async with AsyncSessionLocal() as session:
        repo = CampaignAutoMatchRepository(session)

        # SEC-006: overlap check is store-scoped (no cross-tenant leak).
        # Surfaced as non-blocking warnings so the merchant sees the
        # intercept risk before committing.
        overlaps = await repo.overlaps_existing(
            store_id=store_id,
            new_conditions=conditions,
            new_priority=body.priority,
        )

        try:
            group = await repo.create_group(
                tenant_id=campaign.tenant_id,
                store_id=store_id,
                campaign_id=campaign_id,
                combinator=body.combinator,
                priority=body.priority,
                conditions=conditions,
                created_by=user_id,
            )
        except Exception as exc:
            # Priority unique-per-store collision falls here as the
            # only realistic IntegrityError. Friendlier 422 than the
            # raw DB error.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Could not create rule: {exc!s}",
            ) from exc

        # SEC-002a — audit log
        await AuditService(session).log(
            event_type=EventType.CAMPAIGN_AUTO_MATCH_RULE_CREATE,
            action="create",
            resource_type="campaign_auto_match_rule",
            resource_id=str(group.group_id),
            user_id=user_id,
            store_id=store_id,
            tenant_id=campaign.tenant_id,
            new_value={
                "campaign_id": str(campaign_id),
                "combinator": body.combinator,
                "priority": body.priority,
                "conditions_count": len(conditions),
            },
        )

        await session.commit()

    warnings: list[RuleWarning] = []
    if overlaps:
        warnings.append(
            RuleWarning(
                code="rule_overlap",
                message=(
                    f"This rule overlaps {len(overlaps)} higher-priority rule(s) "
                    f"in this store — those rules win (lower priority = higher precedence)"
                ),
            )
        )

    return SuccessResponse(
        data=CreateRuleResponse(
            rule=RuleResponse(
                group_id=str(group.group_id),
                campaign_id=str(group.campaign_id),
                combinator=group.combinator,
                priority=group.priority,
                conditions=[ConditionResponse(**c.__dict__) for c in group.conditions],
            ),
            warnings=warnings,
        ),
        message="Rule created",
    )


@router.delete(
    "/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an auto-match rule group",
    operation_id="delete_campaign_auto_match_rule",
)
async def delete_auto_match_rule(
    store_id: UUID,
    campaign_id: UUID,
    group_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
):
    campaign = await _load_campaign_or_404(store_id, campaign_id)

    async with AsyncSessionLocal() as session:
        repo = CampaignAutoMatchRepository(session)
        affected = await repo.delete_group(group_id)
        if affected == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Rule not found",
            )

        # SEC-002a — audit log
        await AuditService(session).log(
            event_type=EventType.CAMPAIGN_AUTO_MATCH_RULE_DELETE,
            action="delete",
            resource_type="campaign_auto_match_rule",
            resource_id=str(group_id),
            user_id=user_id,
            store_id=store_id,
            tenant_id=campaign.tenant_id,
            old_value={
                "campaign_id": str(campaign_id),
                "rows_deleted": affected,
            },
        )

        await session.commit()

    return None
