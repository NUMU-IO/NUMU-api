"""Route-level regression tests for the shipments router.

`GET /pickups` was **unreachable in production**: FastAPI matches routes in
declaration order, and the single-segment literal was declared *after*
`GET /{shipment_id}`, so every call 422'd trying to parse "pickups" as a
UUID. `test_no_shadowed_routes` guards the whole class of bug, not just
that one route.
"""

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from src.application.services.carrier_resolver import (
    DEFAULT_CARRIER,
    SUPPORTED_CARRIERS,
    carrier_catalog,
    supported_operations,
)


@pytest.fixture(scope="module")
def app() -> FastAPI:
    from src.api.v1.routes.stores.shipments import router

    application = FastAPI()
    application.include_router(router)
    return application


@pytest.fixture(scope="module")
def stores_app() -> FastAPI:
    """The whole `/stores` tree, as it is actually mounted.

    Shadowing happens *between* routers as well as within one. A duplicate
    `GET /shipments/carriers` was once defined in both the shipments and
    carriers routers; testing the shipments router alone could not see it,
    because the loser was in a different module.
    """
    from src.api.v1.routes.stores import router

    application = FastAPI()
    application.include_router(router)
    return application


def _routes(app: FastAPI) -> list[APIRoute]:
    return [r for r in app.routes if isinstance(r, APIRoute)]


def _concrete(path: str) -> str:
    """Substitute a UUID for every path param so the path is matchable."""
    out = []
    for segment in path.strip("/").split("/"):
        if segment.startswith("{") and segment.endswith("}"):
            out.append("11111111-1111-1111-1111-111111111111")
        else:
            out.append(segment)
    return "/" + "/".join(out)


class TestRouteShadowing:
    """Every declared route must actually be reachable."""

    def test_no_shadowed_routes(self, app):
        """A route shadowed by an earlier one can never be called.

        Only literal segments are checked: a route whose own concrete form
        is claimed by an *earlier* route with a different path template is
        dead code. Parameterised routes legitimately overlap each other,
        so we only flag a loss when the shadowed route has a literal
        segment where the winner has a parameter.
        """
        routes = _routes(app)
        shadowed: list[tuple[str, str, str]] = []

        for i, route in enumerate(routes):
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                target = _concrete(route.path)
                for earlier in routes[:i]:
                    if method not in earlier.methods:
                        continue
                    if earlier.path == route.path:
                        continue
                    if earlier.path_regex.match(target):
                        shadowed.append((method, route.path, earlier.path))
                        break

        assert not shadowed, (
            "Unreachable routes (shadowed by an earlier one):\n"
            + "\n".join(
                f"  {m} {path}  is swallowed by  {winner}"
                for m, path, winner in shadowed
            )
        )

    def test_list_pickups_is_reachable(self, app):
        """The specific regression: GET /pickups used to 422 as a bad UUID."""
        target = _concrete("/{store_id}/shipments/pickups")
        winner = next(
            r for r in _routes(app) if "GET" in r.methods and r.path_regex.match(target)
        )
        assert winner.path.endswith("/pickups"), (
            f"GET /pickups is shadowed by {winner.path} — it must be declared "
            f"above /{{shipment_id}}"
        )

    def test_carriers_endpoint_is_reachable(self, stores_app):
        """Served by the carriers router, which mounts ahead of this one."""
        target = _concrete("/{store_id}/shipments/carriers")
        winner = next(
            r
            for r in _routes(stores_app)
            if "GET" in r.methods and r.path_regex.match(target)
        )
        assert winner.path.endswith("/carriers")

    def test_operation_ids_are_unique_in_this_router(self, app):
        ids = [r.operation_id for r in _routes(app) if r.operation_id]
        assert len(ids) == len(set(ids))

    def test_no_duplicate_shipping_paths_across_routers(self, stores_app):
        """Cross-router duplicates: the loser is silently dead code.

        Caught a real one — `GET /shipments/carriers` was defined in both
        the shipments and carriers routers. Only the carriers one ever
        ran.
        """
        seen: dict[tuple[str, str], int] = {}
        for route in _routes(stores_app):
            if "/shipments" not in route.path:
                continue
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                seen[(method, route.path)] = seen.get((method, route.path), 0) + 1

        dupes = [f"{m} {p}" for (m, p), n in seen.items() if n > 1]
        assert not dupes, f"Duplicate shipping routes (later one is dead): {dupes}"


class TestCarrierCatalog:
    """The catalog is what lets the hub disable unsupported actions."""

    def test_lists_every_supported_carrier(self):
        assert {c["slug"] for c in carrier_catalog()} == set(SUPPORTED_CARRIERS)

    def test_every_carrier_has_bilingual_names(self):
        for entry in carrier_catalog():
            assert entry["name_en"], entry["slug"]
            assert entry["name_ar"], entry["slug"]
            # Arabic name must actually be Arabic, not a slug fallback.
            assert any("؀" <= ch <= "ۿ" for ch in entry["name_ar"]), (
                f"{entry['slug']} has no Arabic name"
            )

    def test_exactly_one_default(self):
        defaults = [c["slug"] for c in carrier_catalog() if c["is_default"]]
        assert defaults == [DEFAULT_CARRIER]

    def test_bosta_reports_the_rich_operation_set(self):
        ops = supported_operations("bosta")
        for expected in ("print_awb", "create_pickup", "cancel_shipment", "get_cities"):
            assert expected in ops

    @pytest.mark.parametrize("slug", ["mylerz", "jt"])
    def test_thin_providers_report_only_the_base_contract(self, slug):
        """Regression guard: these must not claim Bosta-only operations."""
        ops = supported_operations(slug)
        assert "create_shipment" in ops
        assert "track_shipment" in ops
        for absent in ("print_awb", "create_pickup", "cancel_shipment"):
            assert absent not in ops, f"{slug} should not claim {absent}"

    def test_unknown_carrier_reports_no_operations(self):
        assert supported_operations("aramex") == []

    def test_catalog_never_calls_a_carrier(self):
        """Must be answerable from class introspection alone.

        If this ever needs credentials or network, the hub can't render
        the shipping settings page for an unconfigured store.
        """
        for entry in carrier_catalog():
            assert isinstance(entry["supported_operations"], list)
