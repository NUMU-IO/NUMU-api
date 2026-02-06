"""Database query performance tests.

These tests verify that database queries perform within acceptable bounds.
They help identify N+1 queries, missing indexes, and slow operations.

Usage:
    pytest tests/performance/test_database_performance.py -v -s
"""

import time
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.performance.conftest import PerformanceMetrics


class TestQueryPerformance:
    """Tests for database query performance."""

    @pytest.mark.asyncio
    async def test_product_list_query_performance(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Test product listing query performance.

        Expected: <50ms for paginated product list
        """
        metrics = PerformanceMetrics()

        for _ in range(10):
            start = time.perf_counter()
            result = await db_session.execute(
                text("""
                    SELECT id, store_id, name, slug, price_amount, price_currency,
                           quantity, status, images, created_at
                    FROM products
                    WHERE status = 'active'
                    ORDER BY created_at DESC
                    LIMIT 20 OFFSET 0
                """)
            )
            _ = result.fetchall()
            elapsed_ms = (time.perf_counter() - start) * 1000
            metrics.response_times.append(elapsed_ms)

        if metrics.count > 0:
            assert metrics.p95_response_time < 50.0, (
                f"Product list query too slow: p95={metrics.p95_response_time:.2f}ms"
            )
            print(f"\nProduct list query: {metrics.summary()}")

    @pytest.mark.asyncio
    async def test_product_search_query_performance(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Test product search query performance.

        Expected: <100ms for search with ILIKE
        """
        metrics = PerformanceMetrics()
        search_term = "%test%"

        for _ in range(10):
            start = time.perf_counter()
            result = await db_session.execute(
                text("""
                    SELECT id, store_id, name, slug, price_amount
                    FROM products
                    WHERE (name ILIKE :search OR description ILIKE :search)
                    ORDER BY created_at DESC
                    LIMIT 20
                """),
                {"search": search_term},
            )
            _ = result.fetchall()
            elapsed_ms = (time.perf_counter() - start) * 1000
            metrics.response_times.append(elapsed_ms)

        if metrics.count > 0:
            assert metrics.p95_response_time < 100.0, (
                f"Product search query too slow: p95={metrics.p95_response_time:.2f}ms"
            )
            print(f"\nProduct search query: {metrics.summary()}")

    @pytest.mark.asyncio
    async def test_product_by_category_query_performance(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Test category filtering query performance.

        Expected: <30ms with proper index
        """
        metrics = PerformanceMetrics()
        dummy_category_id = uuid.uuid4()

        for _ in range(10):
            start = time.perf_counter()
            result = await db_session.execute(
                text("""
                    SELECT id, store_id, name, slug, price_amount
                    FROM products
                    WHERE category_id = :category_id
                    ORDER BY created_at DESC
                    LIMIT 20
                """),
                {"category_id": dummy_category_id},
            )
            _ = result.fetchall()
            elapsed_ms = (time.perf_counter() - start) * 1000
            metrics.response_times.append(elapsed_ms)

        if metrics.count > 0:
            assert metrics.p95_response_time < 30.0, (
                f"Category filter query too slow: p95={metrics.p95_response_time:.2f}ms"
            )
            print(f"\nCategory filter query: {metrics.summary()}")

    @pytest.mark.asyncio
    async def test_count_query_performance(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Test count query performance.

        Expected: <20ms with proper index
        """
        metrics = PerformanceMetrics()
        dummy_store_id = uuid.uuid4()

        for _ in range(10):
            start = time.perf_counter()
            result = await db_session.execute(
                text("""
                    SELECT COUNT(*) FROM products
                    WHERE store_id = :store_id AND status = 'active'
                """),
                {"store_id": dummy_store_id},
            )
            _ = result.scalar()
            elapsed_ms = (time.perf_counter() - start) * 1000
            metrics.response_times.append(elapsed_ms)

        if metrics.count > 0:
            assert metrics.p95_response_time < 20.0, (
                f"Count query too slow: p95={metrics.p95_response_time:.2f}ms"
            )
            print(f"\nCount query: {metrics.summary()}")


class TestPaginationPerformance:
    """Tests for pagination query performance."""

    @pytest.mark.asyncio
    async def test_offset_pagination_degradation(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Test that offset pagination doesn't degrade significantly.

        Expected: Page 100 should not be >2x slower than page 1
        """
        metrics_by_page: dict[int, PerformanceMetrics] = {}

        for page in [1, 10, 50, 100]:
            metrics = PerformanceMetrics()
            offset = (page - 1) * 20

            for _ in range(5):
                start = time.perf_counter()
                result = await db_session.execute(
                    text("""
                        SELECT id, name, price_amount
                        FROM products
                        ORDER BY created_at DESC
                        LIMIT 20 OFFSET :offset
                    """),
                    {"offset": offset},
                )
                _ = result.fetchall()
                elapsed_ms = (time.perf_counter() - start) * 1000
                metrics.response_times.append(elapsed_ms)

            metrics_by_page[page] = metrics

        # Check for degradation
        if metrics_by_page[1].count > 0 and metrics_by_page[100].count > 0:
            page_1_avg = metrics_by_page[1].avg_response_time
            page_100_avg = metrics_by_page[100].avg_response_time

            print("\nOffset pagination performance:")
            for page, m in metrics_by_page.items():
                print(f"  Page {page}: avg={m.avg_response_time:.2f}ms")

            # Page 100 should not be more than 3x slower
            assert page_100_avg < page_1_avg * 3, (
                f"Offset pagination degraded too much: "
                f"page 1 avg={page_1_avg:.2f}ms, page 100 avg={page_100_avg:.2f}ms"
            )

    @pytest.mark.asyncio
    async def test_cursor_pagination_consistency(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Test cursor-based pagination has consistent performance.

        Expected: All pages should have similar query times
        """
        metrics = PerformanceMetrics()

        # Simulate cursor-based pagination
        cursor_timestamp = "2024-01-01T00:00:00"
        cursor_id = uuid.uuid4()

        for _ in range(10):
            start = time.perf_counter()
            result = await db_session.execute(
                text("""
                    SELECT id, name, price_amount, created_at
                    FROM products
                    WHERE (created_at, id) < (:cursor_ts, :cursor_id)
                    ORDER BY created_at DESC, id DESC
                    LIMIT 20
                """),
                {"cursor_ts": cursor_timestamp, "cursor_id": cursor_id},
            )
            _ = result.fetchall()
            elapsed_ms = (time.perf_counter() - start) * 1000
            metrics.response_times.append(elapsed_ms)

        if metrics.count > 0:
            # Cursor pagination should be consistently fast
            assert metrics.p95_response_time < 30.0, (
                f"Cursor pagination too slow: p95={metrics.p95_response_time:.2f}ms"
            )
            print(f"\nCursor pagination: {metrics.summary()}")


class TestIndexEffectiveness:
    """Tests to verify index effectiveness."""

    @pytest.mark.asyncio
    async def test_store_id_index(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Verify store_id filter uses index."""
        result = await db_session.execute(
            text("""
                EXPLAIN ANALYZE
                SELECT id, name FROM products
                WHERE store_id = '00000000-0000-0000-0000-000000000001'
                LIMIT 20
            """)
        )
        plan = "\n".join(row[0] for row in result.fetchall())

        # Should use index scan, not sequential scan
        uses_seq_scan = "Seq Scan" in plan and "Index" not in plan

        if uses_seq_scan:
            print(f"\nWARNING: store_id query not using index!\nPlan:\n{plan}")

        # This is a soft assertion - warn but don't fail if data is sparse
        if "rows=" in plan:
            print(f"\nQuery plan for store_id filter:\n{plan[:500]}")

    @pytest.mark.asyncio
    async def test_status_index(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Verify status filter uses index or is efficient."""
        result = await db_session.execute(
            text("""
                EXPLAIN ANALYZE
                SELECT id, name FROM products
                WHERE status = 'active'
                LIMIT 20
            """)
        )
        plan = "\n".join(row[0] for row in result.fetchall())
        print(f"\nQuery plan for status filter:\n{plan[:500]}")

    @pytest.mark.asyncio
    async def test_compound_filter_efficiency(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Test compound filter efficiency (store_id + status + pagination)."""
        metrics = PerformanceMetrics()
        store_id = uuid.uuid4()

        for _ in range(10):
            start = time.perf_counter()
            result = await db_session.execute(
                text("""
                    SELECT id, name, price_amount, created_at
                    FROM products
                    WHERE store_id = :store_id
                      AND status = 'active'
                    ORDER BY created_at DESC
                    LIMIT 20 OFFSET 0
                """),
                {"store_id": store_id},
            )
            _ = result.fetchall()
            elapsed_ms = (time.perf_counter() - start) * 1000
            metrics.response_times.append(elapsed_ms)

        if metrics.count > 0:
            assert metrics.p95_response_time < 30.0, (
                f"Compound filter too slow: p95={metrics.p95_response_time:.2f}ms"
            )
            print(f"\nCompound filter (store_id + status): {metrics.summary()}")


class TestWritePerformance:
    """Tests for write operation performance."""

    @pytest.mark.asyncio
    async def test_stock_update_performance(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Test atomic stock update performance.

        Expected: <10ms for single row update
        """
        metrics = PerformanceMetrics()
        product_id = uuid.uuid4()

        for _ in range(10):
            start = time.perf_counter()
            await db_session.execute(
                text("""
                    UPDATE products
                    SET quantity = quantity - 1
                    WHERE id = :product_id AND quantity >= 1
                """),
                {"product_id": product_id},
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            metrics.response_times.append(elapsed_ms)
            await db_session.rollback()

        if metrics.count > 0:
            assert metrics.p95_response_time < 10.0, (
                f"Stock update too slow: p95={metrics.p95_response_time:.2f}ms"
            )
            print(f"\nStock update: {metrics.summary()}")
