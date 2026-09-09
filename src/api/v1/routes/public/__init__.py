"""Public API routes (no authentication required).

URL: /api/v1/public/
- POST /waitlist             — Join beta waitlist
- GET  /beta/invite/{code}   — Look up beta invite (prefill)
- POST /beta/redeem          — Redeem invite: create user + store
- GET  /stats                — Platform statistics for landing page
- GET  /features             — Feature list for marketing
- POST /demo/start           — Try-a-Demo: provision a 7-day demo tenant
- POST /contact              — Contact form submission
- GET  /stores               — Public directory of live merchant storefronts
- GET  /r/{token}/{index}    — Marketing click redirect (records the click)
"""

from fastapi import APIRouter

from src.api.v1.routes.public.beta import router as beta_router
from src.api.v1.routes.public.contact import router as contact_router
from src.api.v1.routes.public.demo import router as demo_router
from src.api.v1.routes.public.directory import router as directory_router
from src.api.v1.routes.public.landing import router as landing_router
from src.api.v1.routes.public.marketing_click import router as marketing_click_router
from src.api.v1.routes.public.reference import router as reference_router
from src.api.v1.routes.public.waitlist import router as waitlist_router

router = APIRouter()

router.include_router(waitlist_router, tags=["Public - Waitlist"])
router.include_router(beta_router, tags=["Public - Beta"])
router.include_router(landing_router, tags=["Public - Landing"])
router.include_router(demo_router, tags=["Public - Demo"])
router.include_router(contact_router, tags=["Public - Contact"])
router.include_router(marketing_click_router, tags=["Public - Marketing"])
router.include_router(reference_router, tags=["Public - Reference"])
router.include_router(directory_router, tags=["Public - Directory"])

__all__ = ["router"]
