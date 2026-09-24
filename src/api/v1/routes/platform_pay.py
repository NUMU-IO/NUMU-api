"""NUMU platform card page, framed by the merchant hub.

``GET /api/v1/platform-pay/card`` (+ ``card.js``): a static page where a
merchant types a card for a wallet top-up or plan payment. The page posts the
card straight to Kashier's Direct API with the signed order the hub got from
``POST /wallet/topups`` or ``POST /billing/card-intents``; nothing about the
card reaches this API.

It lives on the API origin, not the hub's, so no hub script (analytics,
chat, a compromised dependency) can read the card fields. Its strict CSP is
set in ``SecurityHeadersMiddleware.PAY_CSP``.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter(prefix="/platform-pay", include_in_schema=False)

_ASSETS = Path(__file__).parent / "platform_pay_page"
_HTML = (_ASSETS / "card.html").read_bytes()
_JS = (_ASSETS / "card.js").read_bytes()
_HTML_CACHE = {"Cache-Control": "no-store"}
_ASSET_CACHE = {"Cache-Control": "public, max-age=300"}


@router.get("/card")
async def card_page() -> Response:
    return Response(_HTML, media_type="text/html; charset=utf-8", headers=_HTML_CACHE)


@router.get("/card.js")
async def card_script() -> Response:
    return Response(
        _JS, media_type="application/javascript; charset=utf-8", headers=_ASSET_CACHE
    )
