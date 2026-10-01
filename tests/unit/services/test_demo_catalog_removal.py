"""Sample products go once real ones arrive, and only the samples (audit D3)."""

from uuid import uuid4

from sqlalchemy import select

from src.application.services.demo_seed_service import delete_demo_products
from src.infrastructure.database.models.tenant.category import CategoryModel
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


async def test_sample_collection_goes_with_its_samples(test_session):
    store = await _mk_store(test_session, subdomain="samplecoll")
    collection = CategoryModel(
        id=uuid4(),
        tenant_id=store.tenant_id,
        store_id=store.id,
        name="Starter Collection",
        slug="starter-collection",
        extra_data={"demo_seed": True},
    )
    test_session.add(collection)
    test_session.add(
        ProductModel(
            id=uuid4(),
            tenant_id=store.tenant_id,
            store_id=store.id,
            name="mug",
            slug="demo-mug",
            status="ACTIVE",
            attributes={"demo_seed": True},
            category_id=collection.id,
        )
    )
    await test_session.commit()

    await delete_demo_products(test_session, store.id)
    await test_session.commit()

    assert (await test_session.scalars(select(CategoryModel))).all() == []
