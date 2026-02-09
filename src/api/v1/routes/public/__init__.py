"""Public API routes (no authentication required).

URL: /api/v1/public/
- POST /waitlist — Join beta waitlist
- GET  /stats    — Platform statistics for landing page
- GET  /features — Feature list for marketing
"""

from fastapi import APIRouter

from src.api.v1.routes.public.landing import router as landing_router
from src.api.v1.routes.public.waitlist import router as waitlist_router

router = APIRouter()

router.include_router(waitlist_router, tags=["Public - Waitlist"])
router.include_router(landing_router, tags=["Public - Landing"])

__all__ = ["router"]
