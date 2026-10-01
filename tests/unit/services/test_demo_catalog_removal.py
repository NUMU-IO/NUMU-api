"""Sample products go once real ones arrive, and only the samples (audit D3)."""

from uuid import uuid4

from sqlalchemy import select

from src.application.services.demo_seed_service import delete_demo_products
from src.infrastructure.database.models.tenant.product import ProductModel
from tests.unit.api.test_platform_indexing_gate import _mk_store


async def test_only_seeded_samples_are_deleted(test_session):
    store = await _mk_store(test_session, subdomain="samples")
    for slug, attrs in [
        ("demo-mug", {"demo_seed": True}),
        ("demo-day-dress", {}),
        ("my-plate", {}),
    ]:
        test_session.add(
            ProductModel(
                id=uuid4(),
                tenant_id=store.tenant_id,
                store_id=store.id,
                name=slug,
                slug=slug,
                status="ACTIVE",
                attributes=attrs,
            )
        )
    await test_session.commit()

    assert await delete_demo_products(test_session, store.id) == 1
    await test_session.commit()

    left = (await test_session.scalars(select(ProductModel.slug))).all()
    assert sorted(left) == ["demo-day-dress", "my-plate"]
