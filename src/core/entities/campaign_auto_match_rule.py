"""CampaignAutoMatchRule core entity — feature 002 US4."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class RuleCombinator(StrEnum):
    AND = "AND"
    OR = "OR"


class RuleField(StrEnum):
    UTM_SOURCE = "utm_source"
    UTM_MEDIUM = "utm_medium"
    UTM_CAMPAIGN = "utm_campaign"


class RuleOperator(StrEnum):
    EQUALS = "equals"
    STARTS_WITH = "starts_with"
    CONTAINS = "contains"


@dataclass
class CampaignAutoMatchRule:
    """A single row-level condition within a rule group.

    Multiple rows sharing a ``group_id`` form one logical multi-condition
    rule combined per the group's ``combinator``. Per-store priority is
    unique across all groups so precedence is unambiguous.
    """

    id: UUID
    tenant_id: UUID
    store_id: UUID
    campaign_id: UUID
    group_id: UUID
    combinator: RuleCombinator
    field: RuleField
    operator: RuleOperator
    value: str
    priority: int
    created_at: datetime
    created_by: UUID
