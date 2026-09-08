"""`create_product` previews and validates; it must never create.

The write itself goes through the same CreateProductUseCase the API route
runs, only after the merchant confirms.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.agent.entities import RiskTier
from src.infrastructure.agent.tools.create_product import SPEC, create_product


class _Store:
    default_currency = "EGP"


class _Category:
    def __init__(self, store_id):
        self.store_id = store_id


def _ctx(store_id=None, *, allowed=True, category=None, session=object()):
    from src.application.agent.tools import ToolContext

    async def perm(_code):
        return allowed

    return ToolContext(
        tenant_id=uuid4(),
        store_id=store_id or uuid4(),
        staff_id=uuid4(),
        session=session,
        locale="en",
        has_permission=perm,
    )


@pytest.fixture(autouse=True)
def _repos(monkeypatch):
    """Stub the two lookups the tool makes, so the tests stay about the tool."""

    class _StoreRepo:
        def __init__(self, s):
            pass

        async def get_by_id(self, _id):
            return _Store()

    monkeypatch.setattr(
        "src.infrastructure.repositories.store_repository.StoreRepository", _StoreRepo
    )
    return _StoreRepo


class TestItPreviewsAndNeverCreates:
    @pytest.mark.asyncio
    async def test_a_valid_product_returns_a_confirmable_proposal(self):
        res = await create_product(_ctx(), {"name": "Linen Scarf", "price": 450})

        assert res.ok
        assert res.proposal is not None
        assert res.proposal["tool_name"] == "create_product"
        assert res.data["diff"]["action"] == "create_product"
        assert res.data["diff"]["product"]["name"] == "Linen Scarf"

    @pytest.mark.asyncio
    async def test_new_products_are_drafts(self):
        """A misheard price must not become a live, buyable product."""
        res = await create_product(_ctx(), {"name": "Tee", "price": 200})
        assert res.proposal["params"]["status"] == "draft"

    @pytest.mark.asyncio
    async def test_the_summary_is_written_in_the_merchants_language(self):
        ctx = _ctx()
        ctx.locale = "ar"
        res = await create_product(ctx, {"name": "كوفية", "price": 250})
        assert "إضافة منتج" in res.data["summary"]
        assert "مسودة" in res.data["summary"]

    @pytest.mark.asyncio
    async def test_the_tool_is_confirm_tier_and_asks_for_product_create(self):
        assert SPEC["risk_tier"] == RiskTier.CONFIRM
        assert SPEC["required_permission"] == "product.create"


class TestValidation:
    @pytest.mark.asyncio
    async def test_permission_is_checked_before_anything_else(self):
        res = await create_product(_ctx(allowed=False), {"name": "x", "price": 1})
        assert res.ok is False
        assert res.error_code == "forbidden"
        assert res.proposal is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "args",
        [
            {"price": 100},  # no name
            {"name": "  ", "price": 100},  # blank name
            {"name": "x"},  # no price
            {"name": "x", "price": 0},
            {"name": "x", "price": -5},
            {"name": "x", "price": "free"},
            {"name": "x", "price": 100, "quantity": -1},
            {"name": "x", "price": 100, "compare_at_price": 50},  # below price
        ],
    )
    async def test_bad_input_is_refused_without_a_proposal(self, args):
        res = await create_product(_ctx(), args)
        assert res.ok is False, args
        assert res.proposal is None

    @pytest.mark.asyncio
    async def test_image_urls_must_be_https(self):
        """An http image is mixed content on the storefront."""
        res = await create_product(
            _ctx(), {"name": "x", "price": 10, "images": ["http://x/a.jpg"]}
        )
        assert res.ok is False
        assert "https" in res.error_message

    @pytest.mark.asyncio
    async def test_a_category_from_another_store_is_refused(self, monkeypatch):
        class _CatRepo:
            def __init__(self, s):
                pass

            async def get_by_id(self, _id):
                return _Category(store_id=uuid4())  # some other store

        monkeypatch.setattr(
            "src.infrastructure.repositories.category_repository.CategoryRepository",
            _CatRepo,
        )
        res = await create_product(
            _ctx(), {"name": "x", "price": 10, "category_id": str(uuid4())}
        )
        assert res.ok is False
        assert res.error_code == "not_found"
