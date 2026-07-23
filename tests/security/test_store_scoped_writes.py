"""CL-1 (writes) — id-addressed merchant MUTATIONS must obey the path store.

The read leak (test_store_scoped_reads.py) had write-side siblings. Every one
took a row id from the path, fetched it WITHOUT scoping to the authenticated
store, and either mutated it or resolved permission from the row itself:

  * PUT  /stores/{s}/inventory/levels/{variant}/{location} — no scope AT ALL;
    the inventory_levels unique key omits store_id, so an upsert overwrote a
    FOREIGN store's stock and the rollup rewrote its variant total. CROSS-OWNER.
  * PATCH/DELETE /stores/{s}/products/{id}   — store resolved from the row,
    only owner_id compared → cross-STORE within one owner.
  * PATCH/DELETE /stores/{s}/categories/{id} — same.

These pin that a foreign id is refused (EntityNotFoundError), that the store
scope is a REQUIRED argument (a guard you can forget is not a guard), and that
the inventory guard rejects a foreign variant OR a foreign location.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.exceptions import EntityNotFoundError

# ── products / categories: store_id required, foreign row not-found ──────────


class _Repo:
    def __init__(self, row) -> None:
        self.row = row
        self.deleted = False

    async def get_by_id(self, entity_id):  # noqa: ANN001, ARG002
        return self.row

    async def delete(self, entity_id):  # noqa: ANN001, ARG002
        self.deleted = True
        return True


class _StoreRepo:
    def __init__(self, owner_id) -> None:
        self._owner = owner_id

    async def get_by_id(self, store_id):  # noqa: ANN001, ARG002
        class _S:
            owner_id = self._owner
            subdomain = None
            id = store_id

        return _S()


class _Row:
    def __init__(self, store_id) -> None:
        self.id = uuid4()
        self.store_id = store_id
        self.name = "Foreign"
        self.slug = "foreign"


@pytest.mark.asyncio
async def test_update_product_rejects_foreign_store():
    from src.application.use_cases.products.update_product import (
        UpdateProductUseCase,
    )

    owner = uuid4()
    uc = UpdateProductUseCase(
        product_repository=_Repo(_Row(uuid4())),  # row belongs to another store
        store_repository=_StoreRepo(owner),
    )

    class _Dto:
        def __getattr__(self, _):
            return None

    with pytest.raises(EntityNotFoundError):
        await uc.execute(
            product_id=uuid4(), dto=_Dto(), user_id=owner, store_id=uuid4()
        )


@pytest.mark.asyncio
async def test_delete_product_rejects_foreign_store_and_does_not_delete():
    from src.application.use_cases.products.delete_product import (
        DeleteProductUseCase,
    )

    owner = uuid4()
    repo = _Repo(_Row(uuid4()))
    uc = DeleteProductUseCase(
        product_repository=repo, store_repository=_StoreRepo(owner)
    )
    with pytest.raises(EntityNotFoundError):
        await uc.execute(product_id=uuid4(), user_id=owner, store_id=uuid4())
    assert repo.deleted is False, "a foreign product must never be deleted"


@pytest.mark.asyncio
async def test_delete_product_requires_store_id():
    from src.application.use_cases.products.delete_product import (
        DeleteProductUseCase,
    )

    uc = DeleteProductUseCase(
        product_repository=_Repo(_Row(uuid4())),
        store_repository=_StoreRepo(uuid4()),
    )
    with pytest.raises(TypeError):
        await uc.execute(product_id=uuid4(), user_id=uuid4())  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_update_category_rejects_foreign_store():
    from src.application.use_cases.categories.update_category import (
        UpdateCategoryUseCase,
    )

    owner = uuid4()
    uc = UpdateCategoryUseCase(
        category_repository=_Repo(_Row(uuid4())),
        store_repository=_StoreRepo(owner),
    )

    class _Dto:
        def __getattr__(self, _):
            return None

    with pytest.raises(EntityNotFoundError):
        await uc.execute(
            category_id=uuid4(), dto=_Dto(), user_id=owner, store_id=uuid4()
        )


@pytest.mark.asyncio
async def test_delete_category_rejects_foreign_store_and_does_not_delete():
    from src.application.use_cases.categories.delete_category import (
        DeleteCategoryUseCase,
    )

    owner = uuid4()
    repo = _Repo(_Row(uuid4()))
    uc = DeleteCategoryUseCase(
        category_repository=repo, store_repository=_StoreRepo(owner)
    )
    with pytest.raises(EntityNotFoundError):
        await uc.execute(category_id=uuid4(), user_id=owner, store_id=uuid4())
    assert repo.deleted is False


# ── inventory: foreign variant OR foreign location refused before any write ──


class _FakeSession:
    """Returns a fixed store_id for whichever model column is selected, so we
    can drive the guard without a database. The value comes from `store_map`
    keyed by the model class name embedded in the query."""

    def __init__(self, variant_store, location_store) -> None:
        self._variant_store = variant_store
        self._location_store = location_store
        self._n = 0

    async def execute(self, query):  # noqa: ANN001, ARG002
        # First call in _assert_owned_by_store is the variant, second the
        # location — deterministic order matches the source.
        self._n += 1
        value = self._variant_store if self._n == 1 else self._location_store

        class _R:
            def scalar_one_or_none(self_inner):
                return value

        return _R()


@pytest.mark.asyncio
async def test_inventory_set_level_rejects_foreign_variant():
    from src.application.services.inventory_service import InventoryService

    store = uuid4()
    svc = InventoryService.__new__(InventoryService)
    svc._session = _FakeSession(variant_store=uuid4(), location_store=store)
    with pytest.raises(EntityNotFoundError):
        await svc._assert_owned_by_store(store, uuid4(), uuid4())


@pytest.mark.asyncio
async def test_inventory_set_level_rejects_foreign_location():
    from src.application.services.inventory_service import InventoryService

    store = uuid4()
    svc = InventoryService.__new__(InventoryService)
    # variant is owned, but the location belongs to another store
    svc._session = _FakeSession(variant_store=store, location_store=uuid4())
    with pytest.raises(EntityNotFoundError):
        await svc._assert_owned_by_store(store, uuid4(), uuid4())


@pytest.mark.asyncio
async def test_inventory_set_level_allows_own_variant_and_location():
    from src.application.services.inventory_service import InventoryService

    store = uuid4()
    svc = InventoryService.__new__(InventoryService)
    svc._session = _FakeSession(variant_store=store, location_store=store)
    # Must NOT raise — both belong to the store.
    await svc._assert_owned_by_store(store, uuid4(), uuid4())
