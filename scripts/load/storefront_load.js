/**
 * Storefront load test (Phase 5.9).
 *
 * Simulates 100 concurrent shoppers across 10 stores hitting the
 * core read paths (home, product, collection, cart get, cart add)
 * for 10 minutes. Pass criteria:
 *
 *   p95 < 500ms across all checks
 *   error rate < 1% (no 5xx)
 *
 * Run with:
 *
 *   k6 run -e BASE_URL=https://staging.numueg.app scripts/load/storefront_load.js
 *
 * For one-shot perf checks before a release, run with --vus 100 --duration 10m
 * (overriding the stages below). For weekly cron runs, the stages
 * model traffic ramp + sustain + ramp-down which reflects real load
 * patterns more accurately than a step function.
 */

import http from "k6/http";
import { check, group, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

// ─── Custom metrics ────────────────────────────────────────────

// Per-route p95 lets us pinpoint which path regressed when the
// summary p95 fails.
const homeLatency = new Trend("home_latency_ms");
const pdpLatency = new Trend("pdp_latency_ms");
const plpLatency = new Trend("plp_latency_ms");
const cartLatency = new Trend("cart_latency_ms");
const errorRate = new Rate("errors");

// ─── Config ────────────────────────────────────────────────────

const BASE_URL = __ENV.BASE_URL || "https://staging.numueg.app";

// Load-test bypass token — when set on the server side
// (settings.load_test_bypass_token) AND sent with the same value here,
// the rate limiter skips the general + tracking tiers for requests
// carrying this header. Without it, 100 VUs from one runner IP get
// 429'd after the first ~60 requests and the test measures the rate
// limiter, not the backend. Auth / checkout still rate-limited even
// with the token — that's intentional. Set LOAD_TEST_TOKEN as a CI
// secret; do NOT hardcode it.
const LOAD_TEST_TOKEN = __ENV.LOAD_TEST_TOKEN || "";

// Test stores. Seed these on staging via:
//   for s in load-store-{1..10}; do POST /stores { "name": $s, "subdomain": $s }; done
//   POST /stores/{id}/seed-demo for each
const TEST_STORES = (
  __ENV.TEST_STORES ||
  "load-store-1,load-store-2,load-store-3,load-store-4,load-store-5,load-store-6,load-store-7,load-store-8,load-store-9,load-store-10"
).split(",");

// Demo product slug created by /seed-demo. Stable across stores.
const DEMO_PRODUCT_SLUG = "demo-tshirt";
const DEMO_COLLECTION_SLUG = "starter-collection";

// ─── Test profile ─────────────────────────────────────────────

export const options = {
  // Realistic ramp: 0→100 over 2 min, hold 6 min, ramp down 2 min.
  // Step functions don't surface the issues you actually see in
  // production (e.g. DB pool warm-up, cache cold-start).
  stages: [
    { duration: "2m", target: 100 },
    { duration: "6m", target: 100 },
    { duration: "2m", target: 0 },
  ],
  thresholds: {
    // Audit-plan pass criteria.
    http_req_duration: ["p(95)<500"],
    http_req_failed: ["rate<0.01"],
    // Per-route ceilings — tighter than the global to catch
    // route-specific regressions.
    home_latency_ms: ["p(95)<400"],
    pdp_latency_ms: ["p(95)<300"],
    plp_latency_ms: ["p(95)<400"],
    cart_latency_ms: ["p(95)<200"],
    errors: ["rate<0.01"],
  },
  // Tag every request with the load-test name so observability tools
  // can filter (Sentry, Datadog).
  tags: { test: "storefront_load" },
};

// ─── Helpers ──────────────────────────────────────────────────

function pickStore(stores) {
  return stores[Math.floor(Math.random() * stores.length)];
}

function check200(res, name) {
  const ok = check(res, {
    [`${name} status 2xx`]: (r) => r.status >= 200 && r.status < 300,
  });
  errorRate.add(!ok);
  return ok;
}

// ─── Default scenario ────────────────────────────────────────

/**
 * One virtual user's session: open the storefront, browse a product
 * + collection, fetch the cart, add an item. Mirrors the Shopify
 * funnel-event sequence (page_view → product_view → add_to_cart).
 *
 * Random sleep between actions to model human pacing without
 * batch-flooding any single endpoint.
 */
export default function (data) {
  // `data` comes from setup() and is k6's standard way to share
  // immutable, JSON-serialisable values between the setup phase and
  // the per-VU iterations. We pre-resolved subdomain → UUID once for
  // all VUs in setup(), so iterations have zero lookup overhead.
  const { subdomain, storeId } = pickStore(data.stores);
  const baseHeaders = {
    "x-numu-host": `${subdomain}.numueg.app`,
    host: `${subdomain}.numueg.app`,
    // Bypass rate limiting for general/tracking tiers only when the
    // server-side token is configured; on a normal deploy this header
    // is ignored and we'd be back to 60/min anon throttling.
    ...(LOAD_TEST_TOKEN ? { "x-load-test-token": LOAD_TEST_TOKEN } : {}),
  };

  group("home", () => {
    const res = http.get(`${BASE_URL}/`, {
      headers: baseHeaders,
      tags: { route: "home" },
    });
    homeLatency.add(res.timings.duration);
    check200(res, "home");
  });
  sleep(1 + Math.random() * 2);

  group("collection", () => {
    const res = http.get(
      `${BASE_URL}/api/v1/storefront/store/${storeId}/products?page=1&limit=20`,
      { headers: baseHeaders, tags: { route: "plp" } },
    );
    plpLatency.add(res.timings.duration);
    check200(res, "plp");
  });
  sleep(0.5 + Math.random() * 1.5);

  group("product", () => {
    const res = http.get(
      `${BASE_URL}/api/v1/storefront/store/${storeId}/products/${DEMO_PRODUCT_SLUG}`,
      { headers: baseHeaders, tags: { route: "pdp" } },
    );
    pdpLatency.add(res.timings.duration);
    check200(res, "pdp");
  });
  sleep(0.5 + Math.random() * 2);

  group("cart get", () => {
    const res = http.get(`${BASE_URL}/api/v1/storefront/me/cart`, {
      headers: baseHeaders,
      tags: { route: "cart_get" },
    });
    cartLatency.add(res.timings.duration);
    // Cart endpoint may legitimately 401 for anonymous + un-cookied;
    // we just track latency, not status.
    check(res, {
      "cart latency under 200ms": (r) => r.timings.duration < 200,
    });
  });
  sleep(0.5);

  group("search", () => {
    const res = http.get(
      `${BASE_URL}/api/v1/storefront/store/${storeId}/search/predictive?q=hood&limit=5`,
      { headers: baseHeaders, tags: { route: "search" } },
    );
    check200(res, "search");
  });

  // Realistic between-session pause — virtual users don't hammer
  // the storefront in a tight loop.
  sleep(2 + Math.random() * 3);
}

// ─── Lifecycle hooks ─────────────────────────────────────────

/**
 * Resolve every configured subdomain to its store UUID exactly once,
 * before any VU starts. The returned object is passed to every VU's
 * default() call by k6, so iterations don't do any lookup network I/O.
 *
 * Failure modes:
 *   - 404 for a subdomain → drop that store from the pool and log a
 *     warning. The test can still run on the remaining stores.
 *   - Zero stores resolved → throw, aborting the test before VUs spin
 *     up. This is what we want — running 100 VUs against missing
 *     fixtures produces a misleading "everything failed" report. Better
 *     to fail fast with a clear "fixture seeding didn't happen" message.
 */
export function setup() {
  console.log(
    `[load] BASE_URL=${BASE_URL}\n[load] subdomains=${TEST_STORES.join(",")}\n[load] target: 100 VUs / 10m / p95<500ms`,
  );

  const setupHeaders = LOAD_TEST_TOKEN
    ? { "x-load-test-token": LOAD_TEST_TOKEN }
    : {};
  const resolved = [];
  for (const subdomain of TEST_STORES) {
    const res = http.get(
      `${BASE_URL}/api/v1/storefront/store-by-subdomain/${subdomain}`,
      { headers: setupHeaders, tags: { route: "store_lookup", phase: "setup" } },
    );
    if (res.status !== 200) {
      console.warn(
        `[load] fixture missing for "${subdomain}" (status=${res.status}) — skipping`,
      );
      continue;
    }
    let id;
    try {
      id = JSON.parse(res.body)?.data?.id;
    } catch (err) {
      console.warn(`[load] bad JSON resolving "${subdomain}": ${err.message}`);
      continue;
    }
    if (!id) {
      console.warn(`[load] response for "${subdomain}" missing data.id`);
      continue;
    }
    resolved.push({ subdomain, storeId: id });
  }

  if (resolved.length === 0) {
    throw new Error(
      `[load] no stores resolved — run scripts/load/seed_load_test_stores.py against ${BASE_URL} first`,
    );
  }

  console.log(
    `[load] resolved ${resolved.length}/${TEST_STORES.length} stores:`,
  );
  for (const { subdomain, storeId } of resolved) {
    console.log(`[load]   ${subdomain} → ${storeId}`);
  }

  return { stores: resolved };
}

export function teardown(_data) {
  // No-op — tests are read-only against staging. If we add cart-add
  // / order-create paths later, teardown should clean those rows.
}
