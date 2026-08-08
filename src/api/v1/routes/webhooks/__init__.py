"""Webhook routes for external service callbacks."""

from fastapi import APIRouter

from src.api.v1.routes.webhooks.bosta import router as bosta_router
from src.api.v1.routes.webhooks.fawaterak import router as fawaterak_router
from src.api.v1.routes.webhooks.fawry import router as fawry_router
from src.api.v1.routes.webhooks.gowa import router as gowa_router
from src.api.v1.routes.webhooks.instapay import router as instapay_router
from src.api.v1.routes.webhooks.jt import router as jt_router
from src.api.v1.routes.webhooks.kashier import router as kashier_router
from src.api.v1.routes.webhooks.kashier_platform import (
    router as kashier_platform_router,
)
from src.api.v1.routes.webhooks.meta import router as meta_router
from src.api.v1.routes.webhooks.moyasar import router as moyasar_router
from src.api.v1.routes.webhooks.mylerz import router as mylerz_router
from src.api.v1.routes.webhooks.paymob import router as paymob_router
from src.api.v1.routes.webhooks.paymob_platform import (
    router as paymob_platform_router,
)
from src.api.v1.routes.webhooks.resend import router as resend_router
from src.api.v1.routes.webhooks.tiktok_shop import router as tiktok_shop_router
from src.api.v1.routes.webhooks.whatsapp import router as whatsapp_router

# Main webhooks router
router = APIRouter()

router.include_router(paymob_router, prefix="/paymob", tags=["Webhooks - Paymob"])
# Platform-directed Paymob payments (wallet top-ups) — NUMU's own account,
# hard-enforced platform HMAC. Separate handler from the merchant callback.
router.include_router(
    paymob_platform_router, prefix="/paymob", tags=["Webhooks - Paymob"]
)
router.include_router(meta_router, prefix="/meta", tags=["Webhooks - Meta"])
router.include_router(fawry_router, prefix="/fawry", tags=["Webhooks - Fawry"])
router.include_router(instapay_router, prefix="/instapay", tags=["Webhooks - InstaPay"])
router.include_router(bosta_router, prefix="/bosta", tags=["Webhooks - Bosta"])
router.include_router(mylerz_router, prefix="/mylerz", tags=["Webhooks - Mylerz"])
router.include_router(jt_router, prefix="/jt", tags=["Webhooks - J&T"])
router.include_router(whatsapp_router, prefix="/whatsapp", tags=["Webhooks - WhatsApp"])
# GOWA (unofficial WhatsApp Web transport). Separate handler from the Meta
# callback: different signature scheme, and inbound replies are numbered text
# rather than button payloads.
router.include_router(
    gowa_router, prefix="/whatsapp/gowa", tags=["Webhooks - WhatsApp"]
)
router.include_router(kashier_router, prefix="/kashier", tags=["Webhooks - Kashier"])
# Platform-directed Kashier payments (wallet card top-ups) — NUMU's own
# account, hard-enforced platform signature. Separate from the merchant route.
router.include_router(
    kashier_platform_router, prefix="/kashier", tags=["Webhooks - Kashier"]
)
router.include_router(
    fawaterak_router, prefix="/fawaterak", tags=["Webhooks - Fawaterak"]
)
router.include_router(moyasar_router, prefix="/moyasar", tags=["Webhooks - Moyasar"])
router.include_router(resend_router, prefix="/resend", tags=["Webhooks - Resend"])
router.include_router(
    tiktok_shop_router, prefix="/tiktok-shop", tags=["Webhooks - TikTok Shop"]
)

__all__ = ["router"]
