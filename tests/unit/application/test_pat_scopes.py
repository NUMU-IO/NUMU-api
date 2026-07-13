"""Unit tests for PAT scope vocabulary and central enforcement mapping.

These guard the security-relevant invariants of scoped personal access tokens:
default-deny outside the mapped store surface, read/write split by HTTP method,
PATs never managing PATs, and the identity endpoints staying reachable.
"""

from src.application.services.personal_access_token_service import (
    SCOPE_DOMAINS,
    VALID_SCOPES,
    required_scope_for,
    scope_allows,
)


class TestScopeVocabulary:
    def test_every_domain_has_read_and_write(self):
        for domain in SCOPE_DOMAINS:
            assert f"{domain}:read" in VALID_SCOPES
            assert f"{domain}:write" in VALID_SCOPES

    def test_wildcard_is_valid(self):
        assert "*" in VALID_SCOPES


class TestRequiredScopeFor:
    def test_store_reads_map_to_read_scopes(self):
        assert (
            required_scope_for("/api/v1/stores/abc/products", "GET") == "catalog:read"
        )
        assert required_scope_for("/api/v1/stores/abc/orders/", "GET") == "orders:read"
        assert (
            required_scope_for("/api/v1/stores/abc/analytics/overview", "GET")
            == "analytics:read"
        )

    def test_store_mutations_map_to_write_scopes(self):
        assert (
            required_scope_for("/api/v1/stores/abc/products/", "POST")
            == "catalog:write"
        )
        assert (
            required_scope_for("/api/v1/stores/abc/products/x", "DELETE")
            == "catalog:write"
        )
        assert (
            required_scope_for("/api/v1/stores/abc/orders/x/status", "PATCH")
            == "orders:write"
        )

    def test_pat_management_is_always_denied(self):
        for method in ("GET", "POST", "DELETE"):
            assert (
                required_scope_for("/api/v1/stores/abc/access-tokens/", method) is None
            )

    def test_unmapped_store_segment_is_denied(self):
        assert (
            required_scope_for("/api/v1/stores/abc/definitely-not-a-route", "GET")
            is None
        )

    def test_non_store_surfaces_are_denied(self):
        assert required_scope_for("/api/v1/admin/tenants", "GET") is None
        assert required_scope_for("/api/v1/auth/login", "POST") is None
        assert required_scope_for("/api/v1/tenants", "GET") is None

    def test_identity_endpoints_are_allowed_read_only(self):
        assert required_scope_for("/api/v1/auth/me", "GET") == "__identity__"
        assert required_scope_for("/api/v1/auth/api-key/me", "GET") == "__identity__"
        assert required_scope_for("/api/v1/auth/me", "PATCH") is None


class TestScopeAllows:
    def test_exact_scope_matches(self):
        assert scope_allows(["catalog:read"], "catalog:read")
        assert not scope_allows(["catalog:read"], "catalog:write")
        assert not scope_allows(["catalog:read"], "orders:read")

    def test_wildcard_and_legacy_null_allow_everything(self):
        assert scope_allows(["*"], "themes:write")
        assert scope_allows(None, "orders:write")

    def test_identity_pseudo_scope_always_allowed(self):
        assert scope_allows(["analytics:read"], "__identity__")
        assert scope_allows([], "__identity__")
