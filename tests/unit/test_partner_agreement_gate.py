"""Building Partner Apps needs the CURRENT Partner Agreement.

Theme developers were backfilled as approved partners with the placeholder
agreement ``legacy-theme-developer`` so theme upload keeps working while the
program is dark. Nothing then made them accept the real Agreement before they
created apps or development stores (PARTNER-ONBOARDING.md § 7 gap).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.dependencies import partners as deps
from src.application.services.partner_program import AGREEMENT_VERSION


def _gate(account, monkeypatch, role="merchant"):
    async def lookup(_db, _user_id):
        return account

    monkeypatch.setattr(deps, "partner_for_user", lookup)
    user_id = uuid4()
    return user_id, asyncio.run(
        deps.require_agreed_partner(user_id, user=(user_id, role), db=None)
    )


def test_a_backfilled_theme_developer_must_accept_first(monkeypatch):
    legacy = SimpleNamespace(
        status="approved", agreement_version="legacy-theme-developer"
    )
    with pytest.raises(HTTPException) as exc:
        _gate(legacy, monkeypatch)
    assert exc.value.status_code == 403
    assert "Partner Agreement" in exc.value.detail


def test_a_partner_on_an_older_agreement_must_accept_again(monkeypatch):
    old = SimpleNamespace(status="approved", agreement_version="2020-01")
    with pytest.raises(HTTPException):
        _gate(old, monkeypatch)


def test_a_partner_on_the_current_agreement_passes(monkeypatch):
    current = SimpleNamespace(status="approved", agreement_version=AGREEMENT_VERSION)
    user_id, result = _gate(current, monkeypatch)
    assert result == user_id


def test_a_super_admin_without_a_partner_account_passes(monkeypatch):
    # require_approved_partner lets super admins through with no account.
    user_id, result = _gate(None, monkeypatch, role="super_admin")
    assert result == user_id


def test_a_super_admin_with_a_stale_partner_account_passes(monkeypatch):
    """Sentry on api#646: a super admin who once built themes has a legacy
    partner row, and must not be locked out by it."""
    legacy = SimpleNamespace(
        status="approved", agreement_version="legacy-theme-developer"
    )
    user_id, result = _gate(legacy, monkeypatch, role="SUPER_ADMIN")
    assert result == user_id


def test_the_app_and_dev_store_routes_use_the_gate():
    from src.api.v1.routes import partner_apps, partners

    def gates(router, op_ids=None):
        out = {}
        for route in router.routes:
            if op_ids and route.operation_id not in op_ids:
                continue
            names = {d.call.__name__ for d in route.dependant.dependencies}
            out[route.operation_id] = names
        return out

    for op, names in gates(partner_apps.router).items():
        assert "require_agreed_partner" in names, op
    dev = gates(partners.router, {"create_partner_dev_store", "seed_partner_dev_store"})
    assert len(dev) == 2
    for op, names in dev.items():
        assert "require_agreed_partner" in names, op
