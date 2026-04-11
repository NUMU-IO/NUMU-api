"""Public API routes (no authentication required).

URL: /api/v1/public/
- POST /waitlist     — Join beta waitlist
- GET  /stats        — Platform statistics for landing page
- GET  /features     — Feature list for marketing
- POST /demo/start   — Try-a-Demo: provision a 7-day demo tenant
"""

from fastapi import APIRouter

from src.api.v1.routes.public.demo import router as demo_router
from src.api.v1.routes.public.landing import router as landing_router
from src.api.v1.routes.public.waitlist import router as waitlist_router

router = APIRouter()

router.include_router(waitlist_router, tags=["Public - Waitlist"])
router.include_router(landing_router, tags=["Public - Landing"])
router.include_router(demo_router, tags=["Public - Demo"])

__all__ = ["router"]
