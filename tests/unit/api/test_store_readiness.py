"""The readiness meter reads real state: a sellable product, a shipping price,
a way to pay and a number to call."""

from uuid import uuid4

from src.application.services.starter_copy import fill_hero_line, starter_copy
from src.application.services.store_readiness import store_readiness
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.database.models.tenant.shipping_rate import ShippingRateModel
from src.infrastructure.database.models.tenant.shipping_zone import ShippingZoneModel
from tests.unit.api.test_platform_indexing_gate import _mk_store


def _done(readiness):
    return {item["key"]: item["done"] for item in readiness["items"]}


async def test_new_restricted_store_needs_product_zone_and_number(test_session):
    store = await _mk_store(
        test_session,
        subdomain="ready1",
        settings={"shipping": {"restrict_to_zones": True}},
    )
    # A sample and a sold-out product don't make the store sellable.
    for slug, qty, attrs in [
        ("demo-mug", 10, {"demo_seed": True}),
        ("plate", 0, {}),
    ]:
        test_session.add(
            ProductModel(
                id=uuid4(),
                tenant_id=store.tenant_id,
                store_id=store.id,
                name=slug,
                slug=slug,
                status="ACTIVE",
                quantity=qty,
                attributes=attrs,
            )
        )
    await test_session.commit()

    got = await store_readiness(test_session, store)
    assert _done(got) == {
        "product_live": False,
        "shipping_priced": False,
        "payment_method": True,  # COD is on by default
        "contact_number": False,
    }
    assert (got["done"], got["ready"]) == (1, False)

    zone = ShippingZoneModel(
        id=uuid4(), tenant_id=store.tenant_id, store_id=store.id, name="Cairo"
    )
    test_session.add(zone)
    test_session.add(
        ShippingRateModel(
            id=uuid4(),
            tenant_id=store.tenant_id,
            zone_id=zone.id,
            rate_type="flat",
            label="Standard",
            config={"amount_cents": 5000},
        )
    )
    test_session.add(
        ProductModel(
            id=uuid4(),
            tenant_id=store.tenant_id,
            store_id=store.id,
            name="vase",
            slug="vase",
            status="ACTIVE",
            quantity=0,
            attributes={"continue_selling_when_out_of_stock": True},
        )
    )
    store.contact_phone = "+201012345678"
    await test_session.commit()

    got = await store_readiness(test_session, store)
    assert got["ready"] is True


def test_starter_copy_fills_only_an_empty_declared_hero_line():
    schemas = {"lux-hero": {"settings": [{"id": "headline"}, {"id": "subtitle"}]}}
    custom = {
        "templates": {
            "home": {"sections": {"lux-hero-1": {"type": "lux-hero", "settings": {}}}}
        }
    }
    line, about = starter_copy("books", "مكتبة النور", "ar")
    assert "مكتبة النور" in about

    assert fill_hero_line(custom, schemas, line) is True
    hero = custom["templates"]["home"]["sections"]["lux-hero-1"]["settings"]
    assert hero["subtitle"] == line

    hero["subtitle"] = "my own words"
    assert fill_hero_line(custom, schemas, "other") is False
    assert hero["subtitle"] == "my own words"

    # A hero whose schema has no line setting is left alone.
    assert fill_hero_line(custom, {"lux-hero": {"settings": []}}, line) is False
