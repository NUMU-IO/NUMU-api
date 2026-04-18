"""WebSocket routes for realtime features."""

from fastapi import APIRouter

from src.api.v1.routes.ws.inbox import router as inbox_router

router = APIRouter(tags=["WebSocket"])
router.include_router(inbox_router, prefix="/inbox")

__all__ = ["router"]
