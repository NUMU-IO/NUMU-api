"""The COD surface for apps: the ``cod`` scope, the combined settings route,
and an app reading its own settings."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.api.v1.routes.stores import cod
from src.api.v1.schemas.tenant.settings import CodDepositPolicy, UpdateCodTrustRequest
from src.application.services.app_manifest import APP_SCOPES
from src.application.services.app_tokens import required_app_scope

STORE = "/api/v1/stores/11111111-1111-1111-1111-111111111111"


@pytest.mark.parametrize(
    ("path", "method", "scope"),
    [
        (f"{STORE}/cod/settings", "GET", "cod:read"),
        (f"{STORE}/cod/settings", "PATCH", "cod:write"),
        (f"{STORE}/cod-trust/decisions", "GET", "cod:read"),
        (f"{STORE}/app-settings", "GET", "__identity__"),
        (f"{STORE}/app-settings", "PUT", None),
        (f"{STORE}/settings/cod-trust", "PATCH", None),
    ],
)
def test_app_scopes_for_the_cod_surface(path, method, scope):
    assert required_app_scope(path, method) == scope


def test_apps_may_hold_the_cod_scopes():
    assert {"cod:read", "cod:write"} <= APP_SCOPES


class _Repo:
    def __init__(self):
        self.saved = 0

    async def update(self, store):
        self.saved += 1
        return store


class _Zones:
    zones: list = []

    def __init__(self, db):
        pass

    async def list_zones_by_store(self, store_id, include_inactive=False):
        return self.zones

    async def get_zone(self, zone_id):
        return next((z for z in self.zones if z.id == zone_id), None)

    async def update_zone(self, zone):
        return zone


@pytest.fixture
def no_otp(monkeypatch):
    monkeypatch.setattr(cod, "ShippingZoneRepository", _Zones)
    _Zones.zones = []

    async def unavailable(store_id, settings, db):
        return False

    monkeypatch.setattr(
        "src.application.services.checkout_identity.otp_available", unavailable
    )


async def test_reading_a_fresh_store_gives_every_default(sample_store, no_otp):
    sample_store.settings = {}
    data = (await cod.get_cod_settings(store=sample_store, db=None)).data
    assert data.trust["enabled"] is False and data.trust["action"] == "block"
    assert data.deposit.enabled is False
    assert data.confirmation.require_order_confirmation is False
    assert data.otp.require_verification is True and data.otp.available is False


async def test_trust_and_otp_save_through_their_own_routes(sample_store, no_otp):
    sample_store.settings = {}
    repo = _Repo()
    body = cod.CodSettingsUpdate(
        trust=UpdateCodTrustRequest(
            enabled=True, action="recover", recovery_promo=" 10% off "
        ),
        otp=cod.OtpUpdate(require_verification=False),
    )
    data = (
        await cod.update_cod_settings(
            body=body,
            store=sample_store,
            db=None,
            store_repo=repo,
            onboarding_repo=None,
        )
    ).data
    assert data.trust["enabled"] is True and data.trust["action"] == "recover"
    # Dropped on save before this change.
    assert data.trust["recovery_promo"] == "10% off"
    assert data.otp.require_verification is False
    assert (
        sample_store.settings["checkout_fields"]["identity"]["require_verification"]
        is False
    )
    assert repo.saved == 2


async def test_a_deposit_on_an_unconfigured_gateway_is_refused(sample_store, no_otp):
    sample_store.settings = {}
    body = cod.CodSettingsUpdate(
        deposit=CodDepositPolicy(
            enabled=True, mode="percent", percent=10, allowed_gateways=["paymob"]
        )
    )
    with pytest.raises(HTTPException) as exc:
        await cod.update_cod_settings(
            body=body,
            store=sample_store,
            db=None,
            store_repo=_Repo(),
            onboarding_repo=None,
        )
    assert exc.value.status_code in (400, 409, 422)


async def test_only_an_app_token_reads_app_settings(sample_store):
    from types import SimpleNamespace

    from src.api.v1.routes.stores.apps import read_own_app_settings

    request = SimpleNamespace(state=SimpleNamespace(pat={"token_id": "pat-1"}))
    with pytest.raises(HTTPException) as exc:
        await read_own_app_settings(request=request, store=sample_store)
    assert exc.value.status_code == 403


async def test_rules_and_zone_fees_save(sample_store, no_otp):
    from types import SimpleNamespace
    from uuid import uuid4

    from src.application.services.cod_rules import CodRules, OrderConditions

    zone = SimpleNamespace(
        id=uuid4(),
        store_id=sample_store.id,
        name="Cairo",
        name_ar="القاهرة",
        is_active=True,
        cod_enabled=True,
        cod_fee_cents=0,
    )
    _Zones.zones = [zone]
    sample_store.settings = {}
    body = cod.CodSettingsUpdate(
        rules=CodRules(otp=OrderConditions(everyone=False, first_time=True)),
        cod_fee=[cod.ZoneFeeUpdate(zone_id=zone.id, cod_fee_cents=2_500)],
    )
    data = (
        await cod.update_cod_settings(
            body=body,
            store=sample_store,
            db=None,
            store_repo=_Repo(),
            onboarding_repo=None,
        )
    ).data
    assert data.rules.otp.first_time is True and data.rules.otp.everyone is False
    assert data.cod_fee[0].cod_fee_cents == 2_500


async def test_another_stores_zone_is_not_found(sample_store, no_otp):
    from uuid import uuid4

    with pytest.raises(HTTPException) as exc:
        await cod.update_cod_settings(
            body=cod.CodSettingsUpdate(
                cod_fee=[cod.ZoneFeeUpdate(zone_id=uuid4(), cod_fee_cents=1)]
            ),
            store=sample_store,
            db=None,
            store_repo=_Repo(),
            onboarding_repo=None,
        )
    assert exc.value.status_code == 404
