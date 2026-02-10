"""Admin routes (SUPER_ADMIN only).

URL: /api/v1/admin/
- /waitlist  — Waitlist management
- /feedback  — Feedback aggregation
"""

from fastapi import APIRouter

from src.api.v1.routes.admin.feedback import router as feedback_router
from src.api.v1.routes.admin.waitlist import router as waitlist_router

router = APIRouter()

router.include_router(waitlist_router, prefix="/waitlist", tags=["Admin - Waitlist"])
router.include_router(feedback_router, prefix="/feedback", tags=["Admin - Feedback"])

__all__ = ["router"]
