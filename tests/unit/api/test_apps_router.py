"""BIS-K-02: every route under ``/api/v1/apps/<slug>`` depends on
``require_app_session(<slug>)``, so an app route can never be reached with a
merchant JWT, an app token or no token at all."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

from src.api.dependencies.app_session import app_router

PREFIX = "/api/v1/apps/"


def _session_slugs(dependant) -> set[str]:
    found = set()
    for dep in dependant.dependencies:
        slug = getattr(dep.call, "app_session_slug", None)
        if slug:
            found.add(slug)
        found |= _session_slugs(dep)
    return found


def _unguarded(app: FastAPI) -> list[str]:
    bad = []
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith(PREFIX):
            slug = route.path[len(PREFIX) :].split("/", 1)[0]
            if slug not in _session_slugs(route.dependant):
                bad.append(route.path)
    return bad


def test_every_app_route_in_the_api_needs_its_own_session():
    from src.main import app

    assert _unguarded(app) == []


def test_the_check_catches_a_route_without_the_session():
    guarded = app_router("dummy-app")

    @guarded.get("/ping")
    async def ping():
        return {}

    loose = APIRouter(prefix="/dummy-app")

    @loose.get("/open")
    async def open_route():
        return {}

    app = FastAPI()
    app.include_router(guarded, prefix="/api/v1/apps")
    app.include_router(loose, prefix="/api/v1/apps")

    assert _unguarded(app) == ["/api/v1/apps/dummy-app/open"]


def test_a_route_guarded_for_another_app_is_caught():
    other = app_router("other-app")

    @other.get("/x")
    async def x():
        return {}

    app = FastAPI()
    # Mounted under the wrong slug: other-app's session is not dummy-app's.
    app.include_router(other, prefix="/api/v1/apps/dummy-app")

    assert _unguarded(app) == ["/api/v1/apps/dummy-app/other-app/x"]
