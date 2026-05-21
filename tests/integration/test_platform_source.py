"""Tests for the platform-source discriminator (backend-026 / spec 017).

Pure-enum + DB-backed tests. The DB tests verify:
  - Default value is `shopify` when not specified
  - Non-default values (`salla`, `woocommerce`, etc.) persist + read back
  - Rows from different sources coexist in the same table
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.platform_source import (
    DEFAULT_ORDER_SOURCE,
    ORDER_SOURCE_VALUES,
    OrderSource,
)
from src.infrastructure.database.models.tenant.customer import CustomerModel

# ---------------------------------------------------------------------------
# Pure enum tests
# ---------------------------------------------------------------------------


class TestOrderSourceEnum:
    """Spec 017 / backend-026 — enum members + values + default."""

    def test_six_supported_sources(self):
        # If a new platform is added, the migration needs amending too.
        assert set(ORDER_SOURCE_VALUES) == {
            "shopify",
            "woocommerce",
            "salla",
            "zid",
            "numu_native",
            "tiktok_shop",
        }

    def test_default_is_shopify(self):
        assert DEFAULT_ORDER_SOURCE == OrderSource.SHOPIFY
        assert DEFAULT_ORDER_SOURCE.value == "shopify"

    def test_values_are_lowercase_strings(self):
        for v in ORDER_SOURCE_VALUES:
            assert v.islower()
            assert " " not in v

    def test_str_enum_str_returns_value(self):
        # Python StrEnum: instance acts as its string value.
        assert str(OrderSource.SHOPIFY) == "shopify"
        assert str(OrderSource.WOOCOMMERCE) == "woocommerce"


# ---------------------------------------------------------------------------
# DB-backed tests on CustomerModel (chosen because it has the simplest
# required-fields surface — Order would require an OrderLineItem etc.)
# ---------------------------------------------------------------------------


class TestSourceColumnDB:
    """Schema-level acceptance: column persists, defaults, reads back."""

    @pytest.mark.asyncio
    async def test_default_source_is_shopify_when_not_set(
        self, test_session: AsyncSession
    ):
        c = CustomerModel(
            id=uuid4(),
            tenant_id=uuid4(),
            store_id=uuid4(),
            email="default@test.local",
            first_name="Test",
            last_name="Customer",
        )
        test_session.add(c)
        await test_session.flush()
        # Refresh to pick up server_default value.
        await test_session.refresh(c)
        assert c.source == OrderSource.SHOPIFY

    @pytest.mark.asyncio
    async def test_non_default_source_persists(self, test_session: AsyncSession):
        c = CustomerModel(
            id=uuid4(),
            tenant_id=uuid4(),
            store_id=uuid4(),
            email="salla@test.local",
            first_name="Test",
            last_name="Customer",
            source=OrderSource.SALLA,
        )
        test_session.add(c)
        await test_session.flush()
        await test_session.refresh(c)
        assert c.source == OrderSource.SALLA

    @pytest.mark.asyncio
    async def test_multiple_sources_coexist(self, test_session: AsyncSession):
        """Future-multi-platform readiness — different-source rows in the same table."""
        store_id = uuid4()
        tenant_id = uuid4()
        sources = [
            OrderSource.SHOPIFY,
            OrderSource.WOOCOMMERCE,
            OrderSource.SALLA,
            OrderSource.ZID,
            OrderSource.NUMU_NATIVE,
            OrderSource.TIKTOK_SHOP,
        ]
        for i, src in enumerate(sources):
            test_session.add(
                CustomerModel(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    store_id=store_id,
                    email=f"c{i}-{src.value}@test.local",
                    first_name="Test",
                    last_name=f"User{i}",
                    source=src,
                )
            )
        await test_session.flush()

        # All six rows persist with their declared source.
        rows = await test_session.execute(
            select(CustomerModel).where(CustomerModel.store_id == store_id)
        )
        persisted_sources = {r.source for r in rows.scalars().all()}
        assert persisted_sources == set(sources)

    @pytest.mark.asyncio
    async def test_filter_by_source_returns_subset(self, test_session: AsyncSession):
        """The column is queryable as a normal enum filter."""
        store_id = uuid4()
        tenant_id = uuid4()
        # 3 Shopify, 2 Salla.
        for i in range(3):
            test_session.add(
                CustomerModel(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    store_id=store_id,
                    email=f"shop-{i}@test.local",
                    first_name="Shop",
                    last_name=f"User{i}",
                    source=OrderSource.SHOPIFY,
                )
            )
        for i in range(2):
            test_session.add(
                CustomerModel(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    store_id=store_id,
                    email=f"salla-{i}@test.local",
                    first_name="Salla",
                    last_name=f"User{i}",
                    source=OrderSource.SALLA,
                )
            )
        await test_session.flush()

        salla_rows = await test_session.execute(
            select(CustomerModel).where(
                CustomerModel.store_id == store_id,
                CustomerModel.source == OrderSource.SALLA,
            )
        )
        assert len(list(salla_rows.scalars().all())) == 2

        shopify_rows = await test_session.execute(
            select(CustomerModel).where(
                CustomerModel.store_id == store_id,
                CustomerModel.source == OrderSource.SHOPIFY,
            )
        )
        assert len(list(shopify_rows.scalars().all())) == 3
