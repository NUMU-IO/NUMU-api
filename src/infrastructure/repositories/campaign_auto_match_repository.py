"""Repository for campaign_auto_match_rules — feature 002 US4.

Groups of rule-rows sharing a ``group_id`` form one logical
multi-condition rule (combined per the group's ``combinator``). The
repository exposes group-oriented CRUD (the merchant edits groups,
not individual rows) plus a ``list_for_store`` ordered fetch used by
the ingest-time evaluator (request-cached).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.campaign_auto_match_rule import (
    CampaignAutoMatchRuleModel,
)


@dataclass
class RuleCondition:
    """One row-level condition within a rule group."""

    field: str
    operator: str
    value: str


@dataclass
class RuleGroup:
    """A logical AND/OR group of conditions for one campaign."""

    group_id: UUID
    campaign_id: UUID
    combinator: str
    priority: int
    conditions: list[RuleCondition]
    created_at: object | None = None
    created_by: UUID | None = None


class CampaignAutoMatchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        tid = get_tenant_id()
        if tid:
            return query.where(CampaignAutoMatchRuleModel.tenant_id == tid)
        return query

    # ── Reads ──────────────────────────────────────────────────────────

    async def list_for_store(self, store_id: UUID) -> list[RuleGroup]:
        """All rule groups for a store, ordered by priority ASC.

        Drives the ingest-time evaluator. Cached per-request by the
        service layer; this method is the cold-fetch.
        """
        q = (
            select(CampaignAutoMatchRuleModel)
            .where(CampaignAutoMatchRuleModel.store_id == store_id)
            .order_by(
                CampaignAutoMatchRuleModel.priority.asc(),
                CampaignAutoMatchRuleModel.group_id,
            )
        )
        q = self._tenant_filter(q)
        rows = (await self.session.execute(q)).scalars().all()
        return _rows_to_groups(rows)

    async def list_for_campaign(self, campaign_id: UUID) -> list[RuleGroup]:
        """All rule groups for one campaign, priority ASC."""
        q = (
            select(CampaignAutoMatchRuleModel)
            .where(CampaignAutoMatchRuleModel.campaign_id == campaign_id)
            .order_by(
                CampaignAutoMatchRuleModel.priority.asc(),
                CampaignAutoMatchRuleModel.group_id,
            )
        )
        q = self._tenant_filter(q)
        rows = (await self.session.execute(q)).scalars().all()
        return _rows_to_groups(rows)

    async def overlaps_existing(
        self,
        store_id: UUID,
        new_conditions: list[RuleCondition],
        new_priority: int,
        exclude_group_id: UUID | None = None,
    ) -> list[tuple[UUID, UUID, int]]:
        """Return (campaign_id, group_id, priority) tuples of any higher-priority
        existing rule that contains an identical (field, operator, value) row.

        SEC-006: store-scoped only — never crosses tenants. Used by the
        editor to surface a non-blocking warning when a new rule would
        be intercepted by an existing higher-priority one.
        """
        if not new_conditions:
            return []
        keys = {(c.field, c.operator, c.value) for c in new_conditions}
        q = select(
            CampaignAutoMatchRuleModel.campaign_id,
            CampaignAutoMatchRuleModel.group_id,
            CampaignAutoMatchRuleModel.priority,
            CampaignAutoMatchRuleModel.field,
            CampaignAutoMatchRuleModel.operator,
            CampaignAutoMatchRuleModel.value,
        ).where(
            CampaignAutoMatchRuleModel.store_id == store_id,
            CampaignAutoMatchRuleModel.priority < new_priority,
        )
        if exclude_group_id is not None:
            q = q.where(CampaignAutoMatchRuleModel.group_id != exclude_group_id)
        q = self._tenant_filter(q)
        rows = (await self.session.execute(q)).all()
        seen: set[tuple[UUID, UUID, int]] = set()
        out: list[tuple[UUID, UUID, int]] = []
        for r in rows:
            if (r.field, r.operator, r.value) in keys:
                key = (r.campaign_id, r.group_id, r.priority)
                if key not in seen:
                    seen.add(key)
                    out.append(key)
        return out

    # ── Writes ─────────────────────────────────────────────────────────

    async def create_group(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        campaign_id: UUID,
        combinator: str,
        priority: int,
        conditions: list[RuleCondition],
        created_by: UUID,
    ) -> RuleGroup:
        """Create N rows sharing one group_id."""
        group_id = uuid4()
        rows = [
            CampaignAutoMatchRuleModel(
                tenant_id=tenant_id,
                store_id=store_id,
                campaign_id=campaign_id,
                group_id=group_id,
                combinator=combinator,
                field=c.field,
                operator=c.operator,
                value=c.value,
                priority=priority,
                created_by=created_by,
            )
            for c in conditions
        ]
        self.session.add_all(rows)
        await self.session.flush()
        return RuleGroup(
            group_id=group_id,
            campaign_id=campaign_id,
            combinator=combinator,
            priority=priority,
            conditions=conditions,
            created_by=created_by,
        )

    async def delete_group(self, group_id: UUID) -> int:
        """Delete all rows in a group. Returns rows-affected."""
        stmt = delete(CampaignAutoMatchRuleModel).where(
            CampaignAutoMatchRuleModel.group_id == group_id
        )
        result = await self.session.execute(self._tenant_filter(stmt))
        await self.session.flush()
        return result.rowcount or 0


def _rows_to_groups(
    rows: list[CampaignAutoMatchRuleModel],
) -> list[RuleGroup]:
    """Collapse flat rows into RuleGroup objects (one per group_id)."""
    by_group: dict[UUID, RuleGroup] = {}
    for r in rows:
        existing = by_group.get(r.group_id)
        cond = RuleCondition(field=r.field, operator=r.operator, value=r.value)
        if existing is None:
            by_group[r.group_id] = RuleGroup(
                group_id=r.group_id,
                campaign_id=r.campaign_id,
                combinator=r.combinator,
                priority=r.priority,
                conditions=[cond],
                created_at=r.created_at,
                created_by=r.created_by,
            )
        else:
            existing.conditions.append(cond)
    # Stable ordering by priority then group_id matches the SQL ORDER BY.
    return sorted(by_group.values(), key=lambda g: (g.priority, str(g.group_id)))
