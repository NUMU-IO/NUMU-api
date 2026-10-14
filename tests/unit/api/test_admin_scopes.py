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
        ("support", "POST", "admin/orders/1/mark-paid", True),
        ("support", "DELETE", "admin/orders/1", False),
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
        ("finance", "GET", "admin/merchants/m1/insights", True),
        ("finance", "GET", "admin/merchants/attention", True),
        ("finance", "GET", "admin/stores", True),
        ("finance", "GET", "admin/stores/s1/detail", True),
        ("finance", "POST", "admin/stores/s1/impersonate", False),
        ("ops", "PATCH", "admin/tenants/t1", True),
        ("ops", "GET", "admin/merchants/attention", True),
        ("ops", "GET", "admin/merchants/m1/insights", True),
        ("ops", "POST", "admin/merchants/m1/extend-trial", False),
        ("ops", "POST", "admin/merchants/m1/pause-billing", False),
        ("ops", "POST", "admin/merchants/m1/invoices/9/refund", False),
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
