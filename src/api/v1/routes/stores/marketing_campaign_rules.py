"""Auto-match rules CRUD — feature 002 US4.

Stub. Full implementation in T055.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(
    prefix="/{store_id}/marketing/campaigns/{campaign_id}/auto-match-rules",
    tags=["Marketing Campaign Auto-Match Rules"],
)
