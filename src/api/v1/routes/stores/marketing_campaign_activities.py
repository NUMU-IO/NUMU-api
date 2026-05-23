"""Campaign activities (manual attribution backfill) — feature 002 US5.

Stub. Full implementation in T066.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(
    prefix="/{store_id}/marketing/campaigns/{campaign_id}/activities",
    tags=["Marketing Campaign Activities"],
)
