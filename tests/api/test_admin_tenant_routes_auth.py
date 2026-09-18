"""Admin routes must authenticate the admin, not whoever the browser is.

The backoffice signs in through `/admin/auth/login`, which sets the ADMIN
cookie. `require_admin` reads that cookie first — deliberately, so that
impersonating a merchant in one tab cannot evict the operator's own session.

`require_roles(SUPER_ADMIN)` resolves the ordinary principal instead: the
merchant cookie, a bearer token, or a PAT. On an admin who is impersonating —
or simply has no merchant cookie — that resolves to somebody who is not a
super admin, and the route answers 403 to an operator who is signed in. That
is what happened to the tenant routes, including the API-access grant.
"""

import inspect

from src.api.dependencies.auth import require_admin
from src.api.v1.routes import tenants


def _admin_routes():
    """Every operation on the admin tenants router, with its endpoint."""
    return [(route.path, route.endpoint) for route in tenants.admin_router.routes]


def _dependency_names(endpoint) -> set[str]:
    names = set()
    for parameter in inspect.signature(endpoint).parameters.values():
        for meta in getattr(parameter.annotation, "__metadata__", ()):
            dependency = getattr(meta, "dependency", None)
            if dependency is not None:
                names.add(getattr(dependency, "__name__", ""))
    return names


def test_every_admin_tenant_route_authenticates_the_admin_cookie():
    routes = _admin_routes()
    assert routes, "the admin tenants router lost its routes"

    for path, endpoint in routes:
        assert require_admin.__name__ in _dependency_names(endpoint), (
            f"{path} does not depend on require_admin, so the backoffice "
            "will get a 403 while signed in"
        )


def test_the_api_access_grant_is_one_of_them():
    """The route the merchant page's toggle calls."""
    paths = [path for path, _ in _admin_routes()]

    assert "/{tenant_id}/api-access" in paths


def test_the_grant_answers_in_the_envelope_the_client_unwraps():
    """The admin client returns `json.data`. A bare dict arrives as undefined,
    and the page throws on a grant that actually succeeded."""
    route = next(
        r for r in tenants.admin_router.routes if r.path == "/{tenant_id}/api-access"
    )

    assert route.response_model is not None
    # Pydantic names the parametrised model, so the name is what identifies it.
    assert route.response_model.__name__.startswith("SuccessResponse")
    assert "data" in route.response_model.model_fields
