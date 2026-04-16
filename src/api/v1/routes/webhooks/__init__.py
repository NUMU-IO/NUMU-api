"""Webhook routes for external service callbacks."""

from fastapi import APIRouter

from src.api.v1.routes.webhooks.bosta import router as bosta_router
from src.api.v1.routes.webhooks.fawry import router as fawry_router
from src.api.v1.routes.webhooks.jt import router as jt_router
from src.api.v1.routes.webhooks.kashier import router as kashier_router
from src.api.v1.routes.webhooks.meta import router as meta_router
from src.api.v1.routes.webhooks.mylerz import router as mylerz_router
from src.api.v1.routes.webhooks.paymob import router as paymob_router
from src.api.v1.routes.webhooks.whatsapp import router as whatsapp_router

# Main webhooks router
router = APIRouter()

router.include_router(paymob_router, prefix="/paymob", tags=["Webhooks - Paymob"])
router.include_router(meta_router, prefix="/meta", tags=["Webhooks - Meta"])
router.include_router(fawry_router, prefix="/fawry", tags=["Webhooks - Fawry"])
router.include_router(bosta_router, prefix="/bosta", tags=["Webhooks - Bosta"])
router.include_router(mylerz_router, prefix="/mylerz", tags=["Webhooks - Mylerz"])
router.include_router(jt_router, prefix="/jt", tags=["Webhooks - J&T"])
router.include_router(whatsapp_router, prefix="/whatsapp", tags=["Webhooks - WhatsApp"])
router.include_router(kashier_router, prefix="/kashier", tags=["Webhooks - Kashier"])

__all__ = ["router"]
