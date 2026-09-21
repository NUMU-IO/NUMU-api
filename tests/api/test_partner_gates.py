"""The Partner program's gates stay on the routes they guard.

- every partner-portal route is hidden while the program is closed;
- theme upload and the marketplace developer routes need an approved partner
  (they accepted any logged-in user before);
- every admin decision needs the 2FA step-up.

Walks the real app, so a router mounted or rewritten without its gate fails.
"""

from fastapi.routing import APIRoute

from src.main import app


def _names(dependant) -> set[str]:
    names = set()
    for dependency in dependant.dependencies:
        names.add(getattr(dependency.call, "__qualname__", ""))
        names |= _names(dependency)
    return names


def _routes(prefix: str) -> list[APIRoute]:
    routes = [
        r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith(prefix)
    ]
    assert routes, f"no routes under {prefix}; the walk is broken"
    return routes


def test_partner_portal_is_hidden_while_the_program_is_closed():
    for route in _routes("/api/v1/partners"):
        assert "require_partner_program" in _names(route.dependant), route.path


def test_theme_developer_routes_need_an_approved_partner():
    routes = _routes("/api/v1/marketplace/developer") + _routes("/api/v1/themes/upload")
    for route in routes:
        assert "require_approved_partner" in _names(route.dependant), route.path


def test_admin_partner_decisions_need_2fa():
    writes = [
        r
        for r in _routes("/api/v1/admin/partners")
        if r.methods & {"POST", "PUT", "PATCH", "DELETE"}
    ]
    assert len(writes) == 3  # program, decision, suspension
    for route in writes:
        assert "require_admin_2fa.<locals>._check" in _names(route.dependant), (
            route.path
        )


def test_partner_app_routes_need_an_open_program_and_an_approved_partner():
    for route in _routes("/api/v1/partners/me/apps"):
        names = _names(route.dependant)
        assert "require_partner_program" in names, route.path
        assert "require_approved_partner" in names, route.path


def test_admin_app_decisions_need_2fa():
    """Review, suspension and the kill switch. Listing flags only curate the
    catalog, so they are audited but not stepped up."""
    for route in _routes("/api/v1/admin/apps"):
        names = _names(route.dependant)
        assert "require_admin" in names, route.path
        needs_2fa = route.methods & {"POST", "PUT"}
        if needs_2fa:
            assert "require_admin_2fa.<locals>._check" in names, route.path
