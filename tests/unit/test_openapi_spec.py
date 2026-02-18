"""Tests for the OpenAPI spec polish: operationIds, tags, schemas, docs auth, 204 deletes.

Run with:
    pytest tests/unit/test_openapi_spec.py -v
"""

import base64
from collections import Counter

import pytest
from fastapi.testclient import TestClient

from src.main import create_app


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def app():
    """Create a fresh app instance for spec tests."""
    return create_app()


@pytest.fixture(scope="module")
def spec(app):
    """Return the full OpenAPI JSON dict."""
    return app.openapi()


@pytest.fixture(scope="module")
def sync_client(app):
    """Synchronous test client (no DB needed — only hits docs/openapi)."""
    with TestClient(app) as c:
        yield c


# ─── 1. Every endpoint has a unique operationId ─────────────────────────────


class TestOperationIds:
    """Verify operationId presence and uniqueness."""

    def test_every_path_has_operation_id(self, spec):
        """Every method in every path MUST have an operationId."""
        missing = []
        for path, methods in spec["paths"].items():
            for method, details in methods.items():
                if method in ("parameters", "summary", "description"):
                    continue  # skip path-level metadata
                if "operationId" not in details:
                    missing.append(f"{method.upper()} {path}")
        assert missing == [], f"Endpoints missing operationId:\n" + "\n".join(missing)

    def test_operation_ids_are_unique(self, spec):
        """No two endpoints may share the same operationId."""
        ids = []
        for path, methods in spec["paths"].items():
            for method, details in methods.items():
                if "operationId" in details:
                    ids.append(details["operationId"])

        dupes = {k: v for k, v in Counter(ids).items() if v > 1}
        assert dupes == {}, f"Duplicate operationIds: {dupes}"

    def test_operation_id_format(self, spec):
        """operationIds should be snake_case (no spaces, hyphens, or uppercase)."""
        bad = []
        for path, methods in spec["paths"].items():
            for method, details in methods.items():
                oid = details.get("operationId")
                if oid and (
                    " " in oid
                    or "-" in oid
                    or oid != oid.lower()
                ):
                    bad.append(f"{oid} ({method.upper()} {path})")
        assert bad == [], f"Non-snake_case operationIds:\n" + "\n".join(bad)


# ─── 2. Tags are consistent ─────────────────────────────────────────────────


class TestTags:
    """Verify tag definitions match what routes actually use."""

    def test_all_route_tags_are_defined(self, spec):
        """Every tag used on a route must appear in the top-level tags list."""
        defined = {t["name"] for t in spec.get("tags", [])}
        used = set()
        for path, methods in spec["paths"].items():
            for method, details in methods.items():
                for tag in details.get("tags", []):
                    used.add(tag)

        undefined = used - defined
        assert undefined == set(), f"Tags used on routes but not defined: {undefined}"

    def test_no_duplicate_tags_on_endpoints(self, spec):
        """No endpoint should have the same tag listed twice."""
        bad = []
        for path, methods in spec["paths"].items():
            for method, details in methods.items():
                tags = details.get("tags", [])
                if len(tags) != len(set(tags)):
                    bad.append(f"{method.upper()} {path}: {tags}")
        assert bad == [], f"Endpoints with duplicate tags:\n" + "\n".join(bad)

    def test_no_orphan_tag_definitions(self, spec):
        """Every defined tag should be used by at least one endpoint."""
        defined = {t["name"] for t in spec.get("tags", [])}
        used = set()
        for path, methods in spec["paths"].items():
            for method, details in methods.items():
                for tag in details.get("tags", []):
                    used.add(tag)

        orphans = defined - used
        # Allow "Root" (used on "/" route in main.py) and tags for
        # tenant/configuration routers that are exported but not yet mounted.
        allowed_orphans = {"Root", "Configuration Requests", "Admin - Credentials"}
        orphans -= allowed_orphans
        assert orphans == set(), f"Defined tags with no endpoints: {orphans}"

    def test_tag_definitions_have_descriptions(self, spec):
        """Every tag definition should include a description."""
        missing = [
            t["name"] for t in spec.get("tags", []) if not t.get("description")
        ]
        assert missing == [], f"Tags without description: {missing}"


# ─── 3. Delete endpoints return 204 ─────────────────────────────────────────


class TestDeleteStatusCodes:
    """Verify DELETE endpoints return 204 No Content (except carts)."""

    # Cart endpoints return 200 because they return the updated cart.
    # 2FA disable returns 200 because it returns the TwoFactorStatusResponse.
    KEEP_200_EXCEPTIONS = {
        "/api/v1/storefront/me/cart/items/{item_id}",
        "/api/v1/storefront/me/cart",
        "/api/v1/auth/2fa/disable",
    }

    def test_delete_endpoints_return_204(self, spec):
        """DELETE endpoints (except allowed exceptions) should have 204."""
        bad = []
        for path, methods in spec["paths"].items():
            if "delete" not in methods:
                continue
            if path in self.KEEP_200_EXCEPTIONS:
                continue

            responses = methods["delete"].get("responses", {})
            has_204 = "204" in responses
            has_200 = "200" in responses
            if not has_204 or has_200:
                bad.append(f"DELETE {path}: responses={list(responses.keys())}")

        assert bad == [], (
            "DELETE endpoints should return 204 (not 200):\n" + "\n".join(bad)
        )

    def test_exception_deletes_return_200(self, spec):
        """Exceptions (cart, 2FA disable) should keep 200 (return response body)."""
        for path in self.KEEP_200_EXCEPTIONS:
            if path in spec.get("paths", {}):
                responses = spec["paths"][path].get("delete", {}).get("responses", {})
                assert "200" in responses, f"{path} should return 200"


# ─── 4. Schema quality ──────────────────────────────────────────────────────


class TestSchemas:
    """Verify Pydantic schema enrichment appears in the spec."""

    def test_no_model_name_collisions(self, spec):
        """Schema names should not contain module paths (e.g. src__api__...)."""
        schemas = spec.get("components", {}).get("schemas", {})
        collisions = [name for name in schemas if name.startswith("src__")]
        assert collisions == [], (
            f"Schema name collisions (module-qualified names):\n"
            + "\n".join(collisions)
        )

    def test_key_schemas_have_examples(self, spec):
        """Important request/response schemas should have json_schema_extra examples."""
        schemas = spec.get("components", {}).get("schemas", {})
        should_have_examples = [
            "LoginRequest",
            "RegisterRequest",
            "ProductCreate",
            "OrderCreate",
            "CouponCreate",
        ]
        missing = []
        for name in should_have_examples:
            schema = schemas.get(name, {})
            has_example = "example" in schema or "examples" in schema
            if not has_example:
                missing.append(name)

        # Only warn — some schemas may not be directly in components
        if missing:
            # Check if they exist at all first
            existing_missing = [m for m in missing if m in schemas]
            assert existing_missing == [], (
                f"Schemas missing examples: {existing_missing}"
            )

    def test_auth_schema_has_field_descriptions(self, spec):
        """Auth schemas should have descriptions on their fields."""
        schemas = spec.get("components", {}).get("schemas", {})
        auth_schema = schemas.get("LoginRequest", {})
        if not auth_schema:
            pytest.skip("LoginRequest schema not found in components")
        props = auth_schema.get("properties", {})
        for field_name, field_def in props.items():
            assert "description" in field_def, (
                f"LoginRequest.{field_name} missing description"
            )


# ─── 5. Docs auth middleware ─────────────────────────────────────────────────


class TestDocsAuth:
    """Verify /docs and /redoc are accessible (dev mode) and the middleware works."""

    def test_openapi_json_accessible(self, sync_client):
        """GET /openapi.json should return 200 in dev/debug mode."""
        resp = sync_client.get("/openapi.json")
        assert resp.status_code == 200
        data = resp.json()
        assert "openapi" in data
        assert "paths" in data

    def test_docs_page_accessible(self, sync_client):
        """GET /docs should return 200 in dev/debug mode."""
        resp = sync_client.get("/docs")
        assert resp.status_code == 200

    def test_redoc_page_accessible(self, sync_client):
        """GET /redoc should return 200 in dev/debug mode."""
        resp = sync_client.get("/redoc")
        assert resp.status_code == 200


class TestDocsAuthMiddleware:
    """Test the DocsAuthMiddleware in isolation.

    Uses monkeypatch to swap settings values instead of reloading modules,
    because Pydantic Settings instances can't be ``importlib.reload``-ed.
    The patch must stay active during both app creation AND request handling,
    since the middleware reads settings at request time.
    """

    @staticmethod
    def _staging_patches():
        """Return a contextmanager that fakes staging settings."""
        from unittest.mock import patch

        from src.config import settings as _settings

        # Stack multiple patches — must stay open during requests
        return (
            patch.object(_settings, "environment", "staging"),
            patch.object(_settings, "debug", False),
            patch.object(_settings, "docs_username", "admin"),
            patch.object(_settings, "docs_password", "secret"),
        )

    def test_middleware_rejects_without_credentials(self):
        """Staging mode + no auth header → 401."""
        patches = self._staging_patches()
        for p in patches:
            p.start()
        try:
            staging_app = create_app()
            with TestClient(staging_app, raise_server_exceptions=False) as c:
                resp = c.get("/openapi.json")
                assert resp.status_code == 401
                assert "WWW-Authenticate" in resp.headers
        finally:
            for p in reversed(patches):
                p.stop()

    def test_middleware_accepts_valid_credentials(self):
        """Staging mode + correct Basic auth → 200."""
        patches = self._staging_patches()
        for p in patches:
            p.start()
        try:
            staging_app = create_app()
            creds = base64.b64encode(b"admin:secret").decode()
            with TestClient(staging_app, raise_server_exceptions=False) as c:
                resp = c.get(
                    "/openapi.json",
                    headers={"Authorization": f"Basic {creds}"},
                )
                assert resp.status_code == 200
        finally:
            for p in reversed(patches):
                p.stop()

    def test_middleware_rejects_wrong_password(self):
        """Staging mode + wrong password → 401."""
        patches = self._staging_patches()
        for p in patches:
            p.start()
        try:
            staging_app = create_app()
            creds = base64.b64encode(b"admin:wrong").decode()
            with TestClient(staging_app, raise_server_exceptions=False) as c:
                resp = c.get(
                    "/openapi.json",
                    headers={"Authorization": f"Basic {creds}"},
                )
                assert resp.status_code == 401
        finally:
            for p in reversed(patches):
                p.stop()


# ─── 6. OpenAPI metadata ────────────────────────────────────────────────────


class TestOpenAPIMetadata:
    """Verify top-level OpenAPI metadata is populated."""

    def test_title(self, spec):
        assert spec["info"]["title"]

    def test_version(self, spec):
        assert spec["info"]["version"]

    def test_description(self, spec):
        assert len(spec["info"].get("description", "")) > 20

    def test_contact(self, spec):
        contact = spec["info"].get("contact", {})
        assert "email" in contact

    def test_servers(self, spec):
        servers = spec.get("servers", [])
        assert len(servers) >= 2
        urls = [s["url"] for s in servers]
        assert any("localhost" in u for u in urls), "Should have local dev server"

    def test_license(self, spec):
        license_info = spec["info"].get("license", {})
        assert "name" in license_info


# ─── 7. Spec completeness snapshot ──────────────────────────────────────────


class TestSpecCompleteness:
    """Guard against regressions in spec completeness."""

    def test_minimum_path_count(self, spec):
        """We should have at least 60 paths (currently ~80+)."""
        assert len(spec["paths"]) >= 60

    def test_minimum_operation_count(self, spec):
        """We should have at least 130 operations (currently 136)."""
        count = sum(
            1
            for methods in spec["paths"].values()
            for m in methods
            if m in ("get", "post", "put", "patch", "delete")
        )
        assert count >= 130, f"Only {count} operations found"

    def test_minimum_schema_count(self, spec):
        """We should have a healthy number of component schemas."""
        schemas = spec.get("components", {}).get("schemas", {})
        assert len(schemas) >= 30, f"Only {len(schemas)} schemas found"
