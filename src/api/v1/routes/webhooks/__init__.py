"""Webhook routes for external service callbacks."""

from fastapi import APIRouter

from src.api.v1.routes.webhooks.paymob import router as paymob_router
from src.api.v1.routes.webhooks.fawry import router as fawry_router
from src.api.v1.routes.webhooks.bosta import router as bosta_router
from src.api.v1.routes.webhooks.whatsapp import router as whatsapp_router

# Main webhooks router
router = APIRouter()

router.include_router(paymob_router, prefix="/paymob", tags=["Webhooks - Paymob"])
router.include_router(fawry_router, prefix="/fawry", tags=["Webhooks - Fawry"])
router.include_router(bosta_router, prefix="/bosta", tags=["Webhooks - Bosta"])
router.include_router(whatsapp_router, prefix="/whatsapp", tags=["Webhooks - WhatsApp"])

__all__ = ["router"]
