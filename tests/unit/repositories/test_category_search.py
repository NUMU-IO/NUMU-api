"""Storefront category search runs in SQL and never loads a catalog."""

from uuid import uuid4

from src.infrastructure.database.models.tenant.category import CategoryModel
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.repositories.category_repository import CategoryRepository


async def test_search_by_name_matches_in_sql_with_a_limit(test_session):
    store = StoreModel(
        id=uuid4(),
        tenant_id=uuid4(),
        owner_id=uuid4(),
        name="Pixel Print",
        slug=f"s-{uuid4().hex[:8]}",
        subdomain=f"s-{uuid4().hex[:8]}",
    )
    test_session.add(store)
    for name, active in (
        ("Arabic Novels", True),
        ("English Novels", True),
        ("Novels 100% Off", True),
        ("Old Novels", False),
        ("Poetry", True),
    ):
        test_session.add(
            CategoryModel(
                store_id=store.id,
                tenant_id=store.tenant_id,
                name=name,
                slug=name.lower().replace(" ", "-"),
                is_active=active,
            )
        )
    await test_session.commit()
    repo = CategoryRepository(test_session)

    names = [c.name for c in await repo.search_by_name(store.id, "novels", 10)]
    assert sorted(names) == ["Arabic Novels", "English Novels", "Novels 100% Off"]
    assert len(await repo.search_by_name(store.id, "novels", 2)) == 2
    # A % in the query is a literal, not a wildcard.
    assert [c.name for c in await repo.search_by_name(store.id, "0%", 10)] == [
        "Novels 100% Off"
    ]
