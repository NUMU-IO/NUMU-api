"""Regression: static-prefix /staff/* sub-routers must be registered
before the dynamic /staff/{membership_id} list router.

History: a prior registration order put `staff_list_router` (which owns
`GET/DELETE /staff/{membership_id}` and `PUT /staff/{membership_id}/roles`)
before the static-prefix staff sub-routers (`/staff/overrides`,
`/staff/sessions`, `/staff/policies`, `/staff/access-requests`). FastAPI
matches in registration order, so every request to one of those static
prefixes was captured as `{membership_id}`, failed UUID validation on
the literal path segment, and returned 422 instead of hitting the real
handler. This test keeps that from recurring.
"""

from fastapi.routing import APIRoute

from src.api.v1.routes import api_router

# `api_router` is mounted at `/api/v1`, so its registered routes carry that
# prefix in `route.path`. We match against the full prefixed path rather
# than hard-coding the version number in each assertion below.
STATIC_STAFF_PREFIXES = (
    "/api/v1/staff/overrides",
    "/api/v1/staff/sessions",
    "/api/v1/staff/policies",
    "/api/v1/staff/access-requests",
    "/api/v1/staff/invitations",
)
DYNAMIC_MEMBERSHIP_PREFIX = "/api/v1/staff/{membership_id}"
STAFF_ME_PATH = "/api/v1/staff/me"


def _route_paths() -> list[str]:
    """Ordered list of registered path templates under api_router."""
    return [r.path for r in api_router.routes if isinstance(r, APIRoute)]


def test_static_staff_prefixes_precede_dynamic_membership_id_route() -> None:
    paths = _route_paths()

    dynamic_indices = [
        i for i, p in enumerate(paths) if p.startswith(DYNAMIC_MEMBERSHIP_PREFIX)
    ]
    assert dynamic_indices, (
        f"Expected at least one {DYNAMIC_MEMBERSHIP_PREFIX} route to be "
        "registered; the list router may have been removed or renamed."
    )
    first_dynamic = min(dynamic_indices)

    for prefix in STATIC_STAFF_PREFIXES:
        static_indices = [i for i, p in enumerate(paths) if p.startswith(prefix)]
        assert static_indices, f"No routes found for static prefix {prefix!r}"
        last_static = max(static_indices)
        assert last_static < first_dynamic, (
            f"Route {prefix!r} is registered at index {last_static}, which is "
            f"AFTER the first {DYNAMIC_MEMBERSHIP_PREFIX} route at index "
            f"{first_dynamic}. FastAPI will match the dynamic route first and "
            f"return 422 (invalid UUID) for requests to {prefix}. Move the "
            f"static-prefix router's include_router() call before "
            f"staff_list_router in src/api/v1/routes/__init__.py."
        )


def test_staff_me_precedes_staff_membership_id_within_list_router() -> None:
    """`/staff/me` is declared before `/staff/{membership_id}` inside list.py
    so the literal `me` path still wins. If someone reorders the handlers,
    this catches it."""
    paths = _route_paths()

    me_indices = [i for i, p in enumerate(paths) if p == STAFF_ME_PATH]
    dyn_indices = [i for i, p in enumerate(paths) if p == DYNAMIC_MEMBERSHIP_PREFIX]

    assert me_indices, f"Expected {STAFF_ME_PATH} to be registered"
    assert dyn_indices, f"Expected {DYNAMIC_MEMBERSHIP_PREFIX} to be registered"
    assert min(me_indices) < min(dyn_indices), (
        f"{STAFF_ME_PATH} must be declared before {DYNAMIC_MEMBERSHIP_PREFIX} "
        "in src/api/v1/routes/staff/list.py, otherwise GET /staff/me is "
        "matched as a membership_id UUID and fails validation."
    )
