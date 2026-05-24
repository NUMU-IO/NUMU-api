"""CampaignActivity core entity — feature 002 US5."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class ActivityType(StrEnum):
    """Activity type taxonomy. v1 has one entry; extend here as future
    merchant-initiated campaign actions ship (e.g. ``recompute_attribution``).
    """

    BACKFILL_ATTRIBUTION = "backfill_attribution"


class ActivityStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class CampaignActivity:
    """Audit log row for a merchant-initiated campaign action."""

    id: UUID
    tenant_id: UUID
    store_id: UUID
    campaign_id: UUID
    type: ActivityType
    status: ActivityStatus
    payload: dict[str, Any]
    run_at: datetime
    run_by: UUID
    affected_count: int | None = None
    skipped_count: int | None = None
    error_message: str | None = None
    completed_at: datetime | None = None
