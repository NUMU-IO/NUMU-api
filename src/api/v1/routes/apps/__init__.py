"""``/api/v1/apps/<slug>/…``: the routes of apps whose backend lives in NUMU-api.

An embedded app's front calls these with the hub's session token, and nothing
else is accepted (APP-STANDARD § 4.2). Build each app's router with
``app_router(slug)`` (``api/dependencies/app_session.py``): it attaches ``require_app_session(slug)`` to every route
it holds, so no route can be added without it. Include each app's router in
``router`` below.
"""

from fastapi import APIRouter

from src.api.v1.routes.apps.back_in_stock import router as back_in_stock_router

router = APIRouter(prefix="/apps")
router.include_router(back_in_stock_router)
