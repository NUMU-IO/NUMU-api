"""Admin 2FA step-up: the prerequisite for NUMU_FORCE_ADMIN_2FA=true.

`require_admin_2fa` gates nine admin actions (theme, app and partner
decisions, capability lifecycle, platform settings) on a TOTP or backup code
verified in the last few minutes. Before these routes existed, the only way to
verify was the merchant session's /auth/2fa/*, which an admin in the admin
panel does not have. Forcing admin 2FA would have made every gated action a
dead end.

Two defects were found and are pinned here:
  * no admin-session 2FA routes;
  * the 2FA entity stamped naive utcnow(). On a non-UTC database (the local
    one is Africa/Cairo) a step-up verified a second ago read as three hours
    old.

The full lifecycle (blocked → enrol → pass → expire → TOTP step-up → expire →
backup-code recovery) was run end to end against a local DB with
NUMU_FORCE_ADMIN_2FA=true: 14/14.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi.routing import APIRoute

from src.api.middleware.rate_limit import AUTH_ENDPOINTS
from src.core.entities.two_factor import TwoFactorAuth
from src.main import app

ROUTES = {
    ("GET", "/api/v1/admin/auth/2fa/status"),
    ("POST", "/api/v1/admin/auth/2fa/enable"),
    ("POST", "/api/v1/admin/auth/2fa/verify"),
}


def _deps(dependant) -> set[str]:
    names = set()
    for d in dependant.dependencies:
        names.add(getattr(d.call, "__name__", ""))
        names |= _deps(d)
    return names


def test_admin_2fa_routes_exist_behind_the_admin_session():
    found = {
        (m, r.path)
        for r in app.routes
        if isinstance(r, APIRoute) and "/admin/auth/2fa/" in r.path
        for m in r.methods
    }
    assert ROUTES <= found
    for r in app.routes:
        if isinstance(r, APIRoute) and "/admin/auth/2fa/" in r.path:
            assert "require_admin" in _deps(r.dependant), r.path


def test_every_2fa_code_endpoint_is_in_the_strict_auth_rate_tier():
    for path in (
        "/api/v1/admin/auth/2fa/verify",
        "/api/v1/auth/2fa/verify",
        "/api/v1/auth/2fa/complete-login",
    ):
        assert path in AUTH_ENDPOINTS, path


def _two_factor() -> TwoFactorAuth:
    return TwoFactorAuth(user_id=uuid4(), secret="JBSWY3DPEHPK3PXP")


def test_verification_timestamps_are_timezone_aware():
    tf = _two_factor()
    tf.record_use()
    assert tf.last_used_at is not None and tf.last_used_at.tzinfo is not None
    # Aware and current: the step-up compares against datetime.now(UTC).
    assert abs((datetime.now(UTC) - tf.last_used_at).total_seconds()) < 5
