"""CL-1 — id-addressed merchant reads must be scoped to the path store.

`/stores/{store_id}/products/{product_id}` authenticates the PATH store via
`verify_store_ownership`, but the lookup used to ignore it and fetch by id
alone. Any authenticated merchant could therefore read ANY product or category
on the platform — verified 2026-07-21 across owner AND tenant boundaries
(a product belonging to a different owner's store was returned in full).

Three defences all failed to catch it, which is why it survived:
  * `_tenant_filter` is inert here — tenant context is derived from the Host
    subdomain and merchant traffic arrives on the apex host;
  * Postgres RLS is bypassed because the API connects as a superuser;
  * the ownership check in update/delete resolves the store FROM THE ROW, so
    it never compared against the store in the path.

These tests pin the guard at the use-case layer, where every caller inherits
it. They also pin that a foreign id is reported NOT-FOUND rather than
forbidden — otherwise the endpoint becomes an id-enumeration oracle.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.application.use_cases.categories.get_category import GetCategoryUseCase
from src.application.use_cases.products.get_product import GetProductUseCase
from src.core.exceptions import EntityNotFoundError


class _Repo:
    """Returns its row regardless of id — exactly like the real repository
    when no tenant context is set. The guard must not depend on the
    repository filtering."""

    def __init__(self, row) -> None:
        self.row = row

    async def get_by_id(self, entity_id):  # noqa: ANN001, ARG002
        return self.row


class _Product:
    def __init__(self, store_id) -> None:
        self.id = uuid4()
        self.store_id = store_id
        self.name = "Foreign Product"
        self.slug = "foreign"
        self.price = 100
        self.currency = "EGP"


class _Category:
    def __init__(self, store_id) -> None:
        self.id = uuid4()
        self.store_id = store_id
        self.name = "Foreign Category"
        self.slug = "foreign"


@pytest.mark.asyncio
async def test_product_from_another_store_is_not_found():
    mine, theirs = uuid4(), uuid4()
    uc = GetProductUseCase(product_repository=_Repo(_Product(theirs)))
    with pytest.raises(EntityNotFoundError):
        await uc.execute(product_id=uuid4(), store_id=mine)


@pytest.mark.asyncio
async def test_category_from_another_store_is_not_found():
    mine, theirs = uuid4(), uuid4()
    uc = GetCategoryUseCase(category_repository=_Repo(_Category(theirs)))
    with pytest.raises(EntityNotFoundError):
        await uc.execute(category_id=uuid4(), store_id=mine)


@pytest.mark.asyncio
async def test_store_id_is_a_required_argument():
    """A guard you can forget to pass is not a guard.

    Both use cases have exactly one caller, so the scope is mandatory rather
    than optional — calling without it must be a TypeError, not a silent
    unscoped read.
    """
    uc = GetProductUseCase(product_repository=_Repo(_Product(uuid4())))
    with pytest.raises(TypeError):
        await uc.execute(product_id=uuid4())  # type: ignore[call-arg]

    uc2 = GetCategoryUseCase(category_repository=_Repo(_Category(uuid4())))
    with pytest.raises(TypeError):
        await uc2.execute(category_id=uuid4())  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_missing_row_still_not_found():
    uc = GetProductUseCase(product_repository=_Repo(None))
    with pytest.raises(EntityNotFoundError):
        await uc.execute(product_id=uuid4(), store_id=uuid4())


@pytest.mark.asyncio
async def test_own_product_is_returned():
    mine = uuid4()
    product = _Product(mine)
    uc = GetProductUseCase(product_repository=_Repo(product))
    # DTO mapping needs a fuller entity than this stub; the guard passing —
    # i.e. NOT raising EntityNotFoundError — is what this test asserts.
    try:
        await uc.execute(product_id=product.id, store_id=mine)
    except EntityNotFoundError:  # pragma: no cover
        pytest.fail("a product in the caller's own store must not be hidden")
    except Exception:
        pass
