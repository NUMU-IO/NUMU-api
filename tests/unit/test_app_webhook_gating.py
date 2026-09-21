"""A Partner App gets no webhook while it may not see merchant data.

Found while writing the suspension runbook: suspending an app (or turning the
Partner-apps kill switch off) stopped its API tokens, but its webhooks kept
delivering orders and customers to it. `signing_secret` is the single point
both dispatch and retries go through, and returning None there skips the
delivery.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from src.application.services import webhook_delivery_service as wds
from src.core.entities.app import AppStatus
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppModel,
)


def _session(install, app):
    rows = {AppInstallationModel: install, AppModel: app}

    class _S:
        async def get(self, model, _id):
            return rows.get(model)

        async def scalar(self, _stmt):
            return None  # partner_apps_enabled(): no row means on

    return _S()


def _sub():
    return SimpleNamespace(app_installation_id=uuid4(), secret="")


def _run(install, app, *, kill_switch_on=True):
    async def secret(_session, _app_id):
        return "client-secret"

    async def switch(_session):
        return kill_switch_on

    with (
        patch("src.application.services.app_tokens.read_client_secret", secret),
        patch("src.application.services.partner_program.partner_apps_enabled", switch),
    ):
        return asyncio.run(wds.signing_secret(_session(install, app), _sub()))


def _install(**kw):
    return SimpleNamespace(
        app_id=uuid4(),
        is_enabled=kw.get("is_enabled", True),
        status=kw.get("status", "active"),
    )


def _app(**kw):
    return SimpleNamespace(
        status=kw.get("status", AppStatus.PUBLISHED),
        developer_id=kw.get("developer_id", uuid4()),
        manifest={},  # free: no subscription needed
    )


def test_a_live_partner_app_is_signed():
    assert _run(_install(), _app()) == "client-secret"


def test_a_suspended_app_gets_no_webhook():
    assert _run(_install(), _app(status=AppStatus.SUSPENDED)) is None


def test_a_plain_string_suspended_status_is_honoured_too():
    assert _run(_install(), _app(status="suspended")) is None


def test_the_kill_switch_stops_partner_app_webhooks():
    assert _run(_install(), _app(), kill_switch_on=False) is None


def test_the_kill_switch_does_not_touch_numu_apps():
    assert (
        _run(_install(), _app(developer_id=None), kill_switch_on=False)
        == "client-secret"
    )


def test_a_disabled_install_gets_no_webhook():
    assert _run(_install(is_enabled=False), _app()) is None


def test_a_mid_consent_install_gets_no_webhook():
    assert _run(_install(status="pending_auth"), _app()) is None


def test_a_merchant_subscription_keeps_its_own_secret():
    sub = SimpleNamespace(app_installation_id=None, secret="merchant-secret")
    assert asyncio.run(wds.signing_secret(None, sub)) == "merchant-secret"


def test_a_paid_app_the_store_has_not_paid_for_gets_no_webhook():
    """Phase 7: webhooks follow the subscription like the token does."""
    paid = _app()
    paid.manifest = {
        "pricing": {"plan": "recurring", "price_cents": 9900, "cycle": "monthly"}
    }
    install = _install()
    install.id = uuid4()

    class _NoSubscription:
        def scalar_one_or_none(self):
            return None

    session = _session(install, paid)

    async def execute(_stmt):
        return _NoSubscription()

    session.execute = execute

    async def secret(_session, _app_id):
        return "client-secret"

    async def switch(_session):
        return True

    with (
        patch("src.application.services.app_tokens.read_client_secret", secret),
        patch("src.application.services.partner_program.partner_apps_enabled", switch),
    ):
        assert asyncio.run(wds.signing_secret(session, _sub())) is None
