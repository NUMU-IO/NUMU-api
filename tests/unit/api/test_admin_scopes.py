"""Staff scopes are a default-deny allow-list over admin paths."""

import pytest

from src.api.dependencies.auth import admin_scope_allows

API = "/api/v1/"


@pytest.mark.parametrize("scope", [None, "all"])
def test_full_scope_opens_everything(scope):
    assert admin_scope_allows(scope, "DELETE", API + "admin/users/123")
    assert admin_scope_allows(scope, "POST", API + "admin/wallets/t/adjust")


@pytest.mark.parametrize(
    ("scope", "method", "path", "allowed"),
    [
        ("support", "GET", "admin/orders", True),
        ("support", "PATCH", "admin/orders/1/status", True),
        ("support", "GET", "admin/wallets", False),
        ("support", "GET", "admin/users", False),
        ("support", "GET", "admin/audit", True),
        ("support", "POST", "admin/audit", False),
        ("support", "GET", "admin/orders-archive", False),
        ("finance", "POST", "admin/wallets/t/adjust", True),
        ("finance", "POST", "admin/merchants/m1/extend-trial", True),
        ("finance", "GET", "admin/merchants/m1/invoices/9", True),
        ("finance", "GET", "admin/merchants", False),
        ("finance", "POST", "admin/merchants/m1/suspend", False),
        ("ops", "PATCH", "admin/tenants/t1", True),
        ("ops", "GET", "marketplace/admin/themes", True),
        ("ops", "GET", "admin/dashboard/overview", True),
        ("ops", "POST", "admin/wallets/t/adjust", False),
        ("ops", "GET", "admin/unknown-section", False),
        # Every admin can see who they are and run the 2FA step-up.
        ("support", "GET", "admin/auth/me", True),
        ("finance", "POST", "admin/auth/2fa/verify", True),
        # Unknown scopes get nothing.
        ("superuser", "GET", "admin/orders", False),
        ("", "GET", "admin/orders", False),
    ],
)
def test_limited_scopes(scope, method, path, allowed):
    assert admin_scope_allows(scope, method, API + path) is allowed
