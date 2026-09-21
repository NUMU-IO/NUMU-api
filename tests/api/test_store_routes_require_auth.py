"""Every /stores/{store_id} route must know who is calling.

The omnichannel routers (channels, threads, messages, templates, catalog,
capi) were mounted straight on the api router with no dependency, and none of
their handlers loaded the caller. Anyone holding a store id could read the
store's customer DMs and send messages as the merchant. Store ids are not
secret: the storefront hands them out.

This walks the real app, so a router mounted anywhere without auth fails here.
"""

from fastapi.routing import APIRoute

from src.main import app

# Resolving any of these means the request carried a principal.
AUTH_DEPENDENCIES = {
    "get_current_user_id",
    "get_current_user_role",
    "require_store_owner",
    "get_current_store",
    "get_current_token_payload",
    "require_admin",
    "get_current_membership",
}

# Static content that holds no store data.
PUBLIC_BY_DESIGN = {
    "/api/v1/stores/{store_id}/products/template",
    "/api/v1/stores/{store_id}/plan/limits",
}


def _dependency_names(dependant) -> set[str]:
    names = set()
    for dependency in dependant.dependencies:
        names.add(getattr(dependency.call, "__name__", ""))
        names |= _dependency_names(dependency)
    return names


def test_every_store_route_authenticates_the_caller():
    store_routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and "/stores/{store_id}" in route.path
    ]
    assert store_routes, "no store routes found; the walk is broken"

    open_routes = sorted(
        f"{sorted(route.methods)} {route.path}"
        for route in store_routes
        if route.path not in PUBLIC_BY_DESIGN
        and not (_dependency_names(route.dependant) & AUTH_DEPENDENCIES)
    )
    assert open_routes == [], "store routes reachable without auth:\n" + "\n".join(
        open_routes
    )
