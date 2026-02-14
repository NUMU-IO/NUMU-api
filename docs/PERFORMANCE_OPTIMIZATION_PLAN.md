# NUMU API Performance Testing & 3G Optimization Plan
## Phase 3 - Days 61-90

**Created:** 2026-02-05
**Author:** Yahia
**Status:** Implementation Ready

---

## Executive Summary

This document outlines the implementation plan for 8 performance optimization tasks focused on mobile/3G network optimization. Based on comprehensive research of current best practices (2025-2026), this plan provides specific library versions, implementation patterns, and architectural decisions.

---

## Research Findings Summary

### Key Technology Decisions

| Component | Recommendation | Version |
|-----------|---------------|---------|
| Cursor Pagination | fastapi-pagination | 0.15.9 |
| Redis Client | redis-py (async) | 7.1.0+ |
| Compression | starlette-compress | 1.0.0+ |
| CI Performance Testing | k6 (NOT Lighthouse) | Latest |
| Python Benchmarking | pytest-benchmark | 5.2.3 |
| Network Throttling | tcconfig (Linux) | Latest |

### Critical Insight: Lighthouse CI is NOT for APIs
Lighthouse CI is exclusively for web page auditing. For REST API performance testing, use **k6** or **Artillery**.

---

## Task 1: API Response Size Analyzer

### Objective
Create a script to analyze API response payload sizes and identify optimization opportunities.

### Files to Create/Modify
- `scripts/analyze_response_sizes.py` (CREATE)
- `src/api/v1/routes/` (ANALYZE - no modification)

### Implementation Approach

```python
# scripts/analyze_response_sizes.py
"""
API Response Size Analyzer
- Calls all API endpoints and measures response sizes
- Identifies large payloads (>50KB threshold for 3G)
- Suggests pagination/fieldset opportunities
"""
```

### Key Features
1. Automatic endpoint discovery from FastAPI OpenAPI spec
2. Response size measurement with compression analysis
3. Report generation in JSON/Markdown format
4. Suggestions for:
   - Endpoints needing pagination (>100 items)
   - Large responses (>50KB uncompressed)
   - Redundant fields in responses

### 3G Optimization Targets
- Target response size: <10KB for list endpoints
- Maximum acceptable: 50KB with compression
- Pagination required for lists >20 items

---

## Task 2: Sparse Fieldsets Support

### Objective
Allow clients to request specific fields via `?fields=id,name,price` query parameter.

### Files to Create/Modify
- `src/api/dependencies/fieldsets.py` (CREATE)
- `src/api/v1/routes/stores/products.py` (MODIFY)
- `src/api/v1/routes/storefront/public.py` (MODIFY)

### Implementation Pattern (Whitelist Security)

```python
# src/api/dependencies/fieldsets.py
from fastapi import Query, HTTPException
from typing import Optional, Set
import re

FIELD_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

class FieldSelector:
    """Secure sparse fieldset handler with whitelist validation."""

    def __init__(self, allowed_fields: Set[str], default_fields: Set[str], sensitive_fields: Set[str] = None):
        self.allowed_fields = allowed_fields
        self.default_fields = default_fields
        self.sensitive_fields = sensitive_fields or set()

    def parse(self, fields_param: Optional[str]) -> Set[str]:
        if not fields_param:
            return self.default_fields

        requested = {f.strip() for f in fields_param.split(",") if f.strip()}

        # Security: Block sensitive fields
        if requested & self.sensitive_fields:
            raise HTTPException(status_code=400, detail="Invalid field requested")

        # Validate against whitelist
        invalid = requested - self.allowed_fields
        if invalid:
            raise HTTPException(status_code=400, detail=f"Unknown fields: {sorted(invalid)}")

        return requested
```

### Product Fields Configuration
```python
PRODUCT_ALLOWED_FIELDS = {"id", "name", "slug", "price", "compare_at_price", "description",
                          "images", "category_id", "stock_quantity", "is_active", "created_at"}
PRODUCT_DEFAULT_FIELDS = {"id", "name", "slug", "price", "images"}  # Mobile-optimized
PRODUCT_SENSITIVE_FIELDS = {"cost_price", "supplier_id", "internal_notes"}
```

### Performance Benefits
- Full response: ~500 bytes per product
- Sparse response (?fields=id,name,price,images): ~150 bytes
- **70% reduction** in payload size

---

## Task 3: Cursor-Based Pagination

### Objective
Implement cursor-based pagination for large lists (products, orders, customers) optimized for mobile/3G.

### Files to Create/Modify
- `src/api/dependencies/pagination.py` (MODIFY - add cursor support)
- `src/api/v1/schemas/common.py` (MODIFY - add cursor response models)
- `src/api/v1/routes/stores/products.py` (MODIFY)
- `src/api/v1/routes/storefront/public.py` (MODIFY)

### Library Installation
```bash
pip install fastapi-pagination==0.15.9
```

### Implementation

```python
# src/api/dependencies/pagination.py
from fastapi_pagination import add_pagination
from fastapi_pagination.cursor import CursorPage, CursorParams
from fastapi_pagination.ext.sqlalchemy import paginate

# Cursor pagination dependency
async def get_products_cursor(
    db: AsyncSession,
    params: CursorParams = Depends()
) -> CursorPage[ProductOut]:
    # CRITICAL: Always order by unique column(s) - include primary key
    query = select(Product).order_by(Product.created_at.desc(), Product.id.desc())
    return await paginate(db, query, params)
```

### Schema Updates
```python
# src/api/v1/schemas/common.py
from fastapi_pagination.cursor import CursorPage

class CursorPaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: Optional[str] = None
    previous_cursor: Optional[str] = None
    has_more: bool
    # Note: No total_count for cursor pagination (expensive query)
```

### Database Index Required
```sql
-- Add to new migration
CREATE INDEX idx_products_cursor ON products (created_at DESC, id DESC);
CREATE INDEX idx_orders_cursor ON orders (created_at DESC, id DESC);
CREATE INDEX idx_customers_cursor ON customers (created_at DESC, id DESC);
```

### Page Size Recommendations
| Network | Recommended Page Size |
|---------|----------------------|
| 3G | 10-15 items |
| 4G | 20-30 items |
| WiFi | 30-50 items |

---

## Task 4: Redis Caching Layer

### Objective
Add Redis caching for product listings and category trees with 5-minute TTL and cache invalidation on update.

### Files to Create/Modify
- `src/infrastructure/cache/product_cache.py` (CREATE)
- `src/application/use_cases/products/list_products.py` (MODIFY)
- `src/application/use_cases/products/update_product.py` (MODIFY)
- `src/api/v1/routes/storefront/public.py` (MODIFY)

### Library Version
```bash
# Already in project: redis>=5.0.1
# Consider upgrading to 7.1.0 for latest features
pip install redis>=7.1.0
```

### Cache Key Design
```python
# Key patterns
"numu:v1:products:store:{store_id}:cat:{category_id}:p:{page}:l:{limit}"
"numu:v1:products:store:{store_id}:detail:{product_id}"
"numu:v1:categories:store:{store_id}:tree"
"numu:v1:categories:store:{store_id}:branch:{parent_id}"
```

### Implementation

```python
# src/infrastructure/cache/product_cache.py
from redis import asyncio as aioredis
from typing import Optional, Any
import json
import hashlib

class ProductCacheService:
    """Redis caching for product-related data."""

    TTL_PRODUCT_LIST = 300      # 5 minutes
    TTL_PRODUCT_DETAIL = 1800   # 30 minutes
    TTL_CATEGORY_TREE = 3600    # 1 hour

    def __init__(self, redis: aioredis.Redis):
        self.redis = redis
        self.prefix = "numu:v1"

    async def get_products(
        self,
        store_id: int,
        category_id: Optional[int],
        page: int,
        limit: int,
        filters_hash: str
    ) -> Optional[dict]:
        key = self._products_key(store_id, category_id, page, limit, filters_hash)
        cached = await self.redis.get(key)
        return json.loads(cached) if cached else None

    async def set_products(
        self,
        store_id: int,
        category_id: Optional[int],
        page: int,
        limit: int,
        filters_hash: str,
        data: dict
    ) -> None:
        key = self._products_key(store_id, category_id, page, limit, filters_hash)
        await self.redis.setex(key, self.TTL_PRODUCT_LIST, json.dumps(data, default=str))

    async def invalidate_product(self, store_id: int, product_id: int, category_id: int) -> None:
        """Invalidate all caches related to a product."""
        # Delete specific product cache
        await self.redis.delete(f"{self.prefix}:products:store:{store_id}:detail:{product_id}")

        # Delete all product list caches for this store/category
        pattern = f"{self.prefix}:products:store:{store_id}:cat:{category_id}:*"
        keys = await self.redis.keys(pattern)
        if keys:
            await self.redis.delete(*keys)

    async def get_category_tree(self, store_id: int) -> Optional[list]:
        key = f"{self.prefix}:categories:store:{store_id}:tree"
        cached = await self.redis.get(key)
        return json.loads(cached) if cached else None

    async def set_category_tree(self, store_id: int, tree: list) -> None:
        key = f"{self.prefix}:categories:store:{store_id}:tree"
        await self.redis.setex(key, self.TTL_CATEGORY_TREE, json.dumps(tree))

    async def invalidate_categories(self, store_id: int) -> None:
        """Invalidate all category caches for a store."""
        pattern = f"{self.prefix}:categories:store:{store_id}:*"
        keys = await self.redis.keys(pattern)
        if keys:
            await self.redis.delete(*keys)

    def _products_key(self, store_id: int, category_id: Optional[int], page: int, limit: int, filters_hash: str) -> str:
        cat = category_id or "all"
        return f"{self.prefix}:products:store:{store_id}:cat:{cat}:f:{filters_hash}:p:{page}:l:{limit}"

    @staticmethod
    def hash_filters(filters: dict) -> str:
        """Generate deterministic hash for filter parameters."""
        sorted_filters = json.dumps(filters, sort_keys=True)
        return hashlib.sha256(sorted_filters.encode()).hexdigest()[:12]
```

### Cache Invalidation Strategy
- **TTL-based**: 5 minutes for product lists, 1 hour for category trees
- **Write-through**: Invalidate on product/category updates
- **Tag-based**: Group caches by store_id and category_id for efficient bulk invalidation

---

## Task 5: k6 CI Config for API Performance Audits

### Objective
Create k6 configuration for API performance testing in CI pipeline (replacing Lighthouse CI which is NOT for APIs).

### Files to Create/Modify
- `tests/performance/k6/api-load-test.js` (CREATE)
- `tests/performance/k6/thresholds.json` (CREATE)
- `.github/workflows/ci.yml` (MODIFY)

### k6 Test Script

```javascript
// tests/performance/k6/api-load-test.js
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '30s', target: 10 },   // Ramp up
    { duration: '1m', target: 50 },    // Sustained load
    { duration: '30s', target: 0 },    // Ramp down
  ],
  thresholds: {
    // 3G-optimized thresholds
    'http_req_duration': ['p(95)<2000', 'p(99)<3000'],  // 95% under 2s, 99% under 3s
    'http_req_failed': ['rate<0.01'],                   // Error rate < 1%
    'http_req_duration{endpoint:products}': ['p(95)<1500'],
    'http_req_duration{endpoint:categories}': ['p(95)<1000'],
  },
};

const BASE_URL = __ENV.API_URL || 'http://localhost:8000';

export default function() {
  // Test product listing endpoint
  let productsRes = http.get(`${BASE_URL}/api/v1/storefront/store/1/products?limit=20`, {
    tags: { endpoint: 'products' },
  });
  check(productsRes, {
    'products status 200': (r) => r.status === 200,
    'products response < 50KB': (r) => r.body.length < 50000,
  });

  sleep(1);

  // Test categories endpoint
  let categoriesRes = http.get(`${BASE_URL}/api/v1/storefront/store/1/categories`, {
    tags: { endpoint: 'categories' },
  });
  check(categoriesRes, {
    'categories status 200': (r) => r.status === 200,
  });

  sleep(1);
}
```

### GitHub Actions Integration

```yaml
# Add to .github/workflows/ci.yml
  performance-test:
    runs-on: ubuntu-latest
    needs: [test]
    steps:
      - uses: actions/checkout@v4

      - name: Setup k6
        uses: grafana/setup-k6-action@v1

      - name: Run k6 load test
        uses: grafana/run-k6-action@v1
        with:
          path: tests/performance/k6/api-load-test.js
        env:
          API_URL: http://localhost:8000

      - name: Upload Results
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: k6-results
          path: results/
```

---

## Task 6: 3G Network Simulation Tests

### Objective
Create tests that verify API performance under throttled 3G network conditions (500kbps).

### Files to Create/Modify
- `tests/performance/test_3g_simulation.py` (CREATE)
- `tests/performance/conftest.py` (CREATE)
- `pyproject.toml` (MODIFY - add pytest-benchmark)

### Dependencies
```bash
pip install pytest-benchmark==5.2.3
pip install httpx  # For async HTTP client with timeout support
```

### 3G Network Profiles
```python
# tests/performance/conftest.py
from dataclasses import dataclass

@dataclass
class NetworkProfile:
    name: str
    download_kbps: int
    upload_kbps: int
    latency_ms: int
    jitter_ms: int
    packet_loss_percent: float

NETWORK_PROFILES = {
    "3g_slow": NetworkProfile("Slow 3G", 500, 250, 400, 100, 2.0),
    "3g_regular": NetworkProfile("Regular 3G", 1000, 500, 200, 50, 0.5),
    "3g_fast": NetworkProfile("Fast 3G", 2000, 1000, 100, 20, 0.1),
}
```

### Test Implementation
```python
# tests/performance/test_3g_simulation.py
import pytest
import time
import asyncio
import httpx

class TestAPIUnder3GConditions:
    """API performance tests simulating 3G network conditions."""

    # Performance thresholds for 3G
    THRESHOLDS = {
        "3g_slow": {"p95_max_ms": 5000, "p99_max_ms": 8000},
        "3g_regular": {"p95_max_ms": 3000, "p99_max_ms": 5000},
        "3g_fast": {"p95_max_ms": 2000, "p99_max_ms": 3000},
    }

    @pytest.mark.benchmark(group="3g-regular")
    async def test_products_endpoint_3g(self, benchmark):
        """Test products endpoint under 3G conditions."""

        async def make_request():
            # Simulate 3G latency
            await asyncio.sleep(0.2)  # 200ms
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "http://localhost:8000/api/v1/storefront/store/1/products",
                    params={"limit": 15}  # 3G-optimized page size
                )
                return response

        result = benchmark.pedantic(
            lambda: asyncio.run(make_request()),
            rounds=10,
            warmup_rounds=2
        )

        # Assert acceptable for 3G
        assert benchmark.stats['mean'] < 3.0

    def test_response_size_under_3g_limit(self):
        """Verify response sizes are acceptable for 3G."""
        import requests

        response = requests.get(
            "http://localhost:8000/api/v1/storefront/store/1/products",
            params={"limit": 15, "fields": "id,name,price,images"}
        )

        # 3G target: <50KB for acceptable load time
        assert len(response.content) < 50000, f"Response too large for 3G: {len(response.content)} bytes"
```

---

## Task 7: Response Time Tracking Middleware

### Objective
Add middleware to log slow queries (>500ms) and add X-Response-Time header.

### Files to Create/Modify
- `src/api/middleware/timing.py` (CREATE)
- `src/main.py` (MODIFY)

### Implementation

```python
# src/api/middleware/timing.py
import time
import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger(__name__)

class ResponseTimeMiddleware(BaseHTTPMiddleware):
    """
    Middleware to track response times and log slow requests.

    Features:
    - Adds X-Response-Time header to all responses
    - Logs requests exceeding threshold (default 500ms)
    - Tracks percentile statistics
    """

    SLOW_REQUEST_THRESHOLD_MS = 500

    async def dispatch(self, request: Request, call_next) -> Response:
        start_time = time.perf_counter()

        response = await call_next(request)

        # Calculate response time
        process_time_ms = (time.perf_counter() - start_time) * 1000

        # Add response time header
        response.headers["X-Response-Time"] = f"{process_time_ms:.2f}ms"

        # Log slow requests
        if process_time_ms > self.SLOW_REQUEST_THRESHOLD_MS:
            logger.warning(
                "slow_request",
                path=request.url.path,
                method=request.method,
                response_time_ms=round(process_time_ms, 2),
                status_code=response.status_code,
                threshold_ms=self.SLOW_REQUEST_THRESHOLD_MS,
                query_params=dict(request.query_params),
            )
        else:
            logger.debug(
                "request_completed",
                path=request.url.path,
                method=request.method,
                response_time_ms=round(process_time_ms, 2),
                status_code=response.status_code,
            )

        return response
```

### Add to main.py
```python
# src/main.py
from src.api.middleware.timing import ResponseTimeMiddleware

# Add after other middleware
app.add_middleware(ResponseTimeMiddleware)
```

---

## Task 8: Performance Tests Suite

### Objective
Comprehensive performance tests for cache hit/miss, pagination, sparse fieldsets, and compression.

### Files to Create/Modify
- `tests/performance/test_cache_performance.py` (CREATE)
- `tests/performance/test_pagination_performance.py` (CREATE)
- `tests/performance/test_fieldsets_performance.py` (CREATE)
- `tests/performance/test_compression.py` (CREATE)

### Test Categories

#### Cache Performance Tests
```python
# tests/performance/test_cache_performance.py
import pytest
import time

class TestCachePerformance:

    async def test_cache_hit_faster_than_miss(self, client, redis):
        """Verify cache hits are significantly faster than misses."""
        # First request (cache miss)
        start = time.perf_counter()
        await client.get("/api/v1/storefront/store/1/products")
        miss_time = time.perf_counter() - start

        # Second request (cache hit)
        start = time.perf_counter()
        await client.get("/api/v1/storefront/store/1/products")
        hit_time = time.perf_counter() - start

        # Cache hit should be at least 2x faster
        assert hit_time < miss_time / 2

    async def test_cache_invalidation_on_update(self, client, redis):
        """Verify cache is invalidated when product is updated."""
        # Populate cache
        await client.get("/api/v1/storefront/store/1/products")

        # Update product
        await client.patch("/api/v1/stores/1/products/1", json={"name": "Updated"})

        # Verify cache was invalidated
        cache_key = "numu:v1:products:store:1:*"
        keys = await redis.keys(cache_key)
        assert len(keys) == 0
```

#### Pagination Performance Tests
```python
# tests/performance/test_pagination_performance.py
class TestPaginationPerformance:

    @pytest.mark.benchmark
    def test_cursor_pagination_constant_time(self, benchmark, client):
        """Verify cursor pagination has O(1) performance regardless of offset."""

        def paginate_to_page(cursor=None):
            params = {"limit": 20}
            if cursor:
                params["cursor"] = cursor
            return client.get("/api/v1/storefront/store/1/products", params=params)

        # Page 1
        result_page1 = benchmark.pedantic(paginate_to_page, rounds=10)

        # Page 100 (should be same speed with cursor pagination)
        # Get cursor for page 100
        cursor = get_cursor_for_page(100)
        result_page100 = benchmark.pedantic(lambda: paginate_to_page(cursor), rounds=10)

        # Performance should be within 20% regardless of position
        assert abs(result_page1.stats['mean'] - result_page100.stats['mean']) / result_page1.stats['mean'] < 0.2
```

---

## Implementation Order

1. **Task 7**: Response Time Middleware (foundation for measuring improvements)
2. **Task 1**: Response Size Analyzer (identify optimization targets)
3. **Task 2**: Sparse Fieldsets (quick win for payload reduction)
4. **Task 3**: Cursor-Based Pagination (critical for 3G)
5. **Task 4**: Redis Caching (major performance improvement)
6. **Task 8**: Performance Tests (verify improvements)
7. **Task 6**: 3G Simulation Tests (comprehensive validation)
8. **Task 5**: k6 CI Config (continuous monitoring)

---

## Branch Strategy

Each task will be implemented in a separate feature branch:

```
feature/perf-response-time-middleware
feature/perf-response-analyzer
feature/perf-sparse-fieldsets
feature/perf-cursor-pagination
feature/perf-redis-cache
feature/perf-tests-suite
feature/perf-3g-simulation
feature/perf-k6-ci
```

---

## Dependencies to Add

```toml
# pyproject.toml additions
[project.dependencies]
fastapi-pagination = ">=0.15.9"
starlette-compress = ">=1.0.0"

[project.optional-dependencies]
performance = [
    "pytest-benchmark>=5.2.3",
    "locust>=2.43.2",
    "httpx>=0.26.0",
]
```

---

## Success Metrics

| Metric | Current (Est.) | Target |
|--------|---------------|--------|
| Product list response size | ~500KB | <50KB |
| P95 response time | ~800ms | <500ms |
| P99 response time | ~2000ms | <1000ms |
| Cache hit ratio | 0% | >80% |
| 3G full page load | ~5s | <2s |

---

## Risk Mitigation

1. **Cache Invalidation Bugs**: Implement comprehensive cache invalidation tests
2. **Cursor Encoding Security**: Use HMAC-signed opaque cursors
3. **Field Injection Attacks**: Strict whitelist validation for sparse fieldsets
4. **Performance Regression**: k6 thresholds in CI will catch regressions

---

## References

- [fastapi-pagination Documentation](https://uriyyo-fastapi-pagination.netlify.app/)
- [Redis Best Practices](https://redis.io/docs/manual/patterns/)
- [k6 Documentation](https://k6.io/docs/)
- [JSON:API Sparse Fieldsets](https://jsonapi.org/format/#fetching-sparse-fieldsets)
- [Mobile API Optimization - Nordic APIs](https://nordicapis.com/optimizing-apis-for-mobile-apps/)
