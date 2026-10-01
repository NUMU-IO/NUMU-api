"""Onboarding steps finish on real state, not on wizard answers (audit D1/D2)."""

from types import SimpleNamespace
from uuid import uuid4

from src.api.v1.routes.stores.shipping import apply_egypt_4_zone_preset
from src.api.v1.schemas.tenant.shipping import PresetRequest
from src.core.entities.onboarding import OnboardingStepKey, StoreOnboarding


class FakeZoneRepo:
    def __init__(self):
        self.rates = []

    async def list_zones_by_store(self, store_id, include_inactive=False):
        return []

    async def create_zone(self, zone, codes):
        return zone

    async def create_rate(self, rate):
        self.rates.append(rate)
        return rate


class FakeOnboardingRepo:
    def __init__(self, store_id):
        self.onboarding = StoreOnboarding(store_id=store_id)

    async def get_by_store_id(self, store_id):
        return self.onboarding

    async def update(self, onboarding):
        self.onboarding = onboarding


async def test_preset_uses_merchant_rates_and_completes_shipping_step():
    store = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    zones, onboarding = FakeZoneRepo(), FakeOnboardingRepo(store.id)

    await apply_egypt_4_zone_preset(
        store, zones, onboarding, PresetRequest(rates_cents=[4000, 5500, 6500, 0])
    )

    assert [r.config["amount_cents"] for r in zones.rates] == [4000, 5500, 6500, 0]
    step = onboarding.onboarding.steps[OnboardingStepKey.ADD_SHIPPING.value]
    assert step["status"] == "completed"


async def test_preset_without_body_keeps_defaults():
    store = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    zones = FakeZoneRepo()

    await apply_egypt_4_zone_preset(store, zones, FakeOnboardingRepo(store.id), None)

    assert [r.config["amount_cents"] for r in zones.rates] == [5000, 6000, 7000, 9000]
