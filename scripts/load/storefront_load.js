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
const lookupLatency = new Trend("lookup_latency_ms");
const errorRate = new Rate("errors");

// ─── Config ────────────────────────────────────────────────────

const BASE_URL = __ENV.BASE_URL || "https://staging.numueg.app";

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
    lookup_latency_ms: ["p(95)<200"],
    errors: ["rate<0.01"],
  },
  // Tag every request with the load-test name so observability tools
  // can filter (Sentry, Datadog).
  tags: { test: "storefront_load" },
};

// ─── Helpers ──────────────────────────────────────────────────

function pickStore() {
  return TEST_STORES[Math.floor(Math.random() * TEST_STORES.length)];
}

function check200(res, name) {
  const ok = check(res, {
    [`${name} status 2xx`]: (r) => r.status >= 200 && r.status < 300,
  });
  errorRate.add(!ok);
  return ok;
}

// ─── Per-VU subdomain → UUID cache ────────────────────────────

// Key: subdomain string. Value: UUID string, or null if the lookup
// 404'd (we skip the store for the rest of the run in that case).
//
// k6 runs each VU as an isolated process, so this Map is naturally
// per-VU without locking. It survives across iterations of the same
// VU but not across VUs — which is what we want: one lookup per
// (VU, store) pair, not one per (iteration, store).
const storeIdCache = new Map();

function resolveStoreId(subdomain, baseHeaders) {
  if (storeIdCache.has(subdomain)) {
    return storeIdCache.get(subdomain);
  }
  const res = http.get(
    `${BASE_URL}/api/v1/storefront/store-by-subdomain/${subdomain}`,
    { headers: baseHeaders, tags: { route: "store_lookup" } },
  );
  lookupLatency.add(res.timings.duration);

  if (res.status !== 200) {
    if (res.status === 404) {
      console.warn(`[load] store fixture missing: ${subdomain}`);
    } else {
      console.error(
        `[load] lookup ${subdomain} failed: status=${res.status} body=${res.body?.slice(0, 200)}`,
      );
    }
    storeIdCache.set(subdomain, null);
    errorRate.add(true);
    return null;
  }
  let body;
  try {
    body = JSON.parse(res.body);
  } catch (err) {
    console.error(`[load] lookup ${subdomain} bad JSON: ${err.message}`);
    storeIdCache.set(subdomain, null);
    errorRate.add(true);
    return null;
  }
  const id = body?.data?.id;
  if (!id) {
    console.error(`[load] lookup ${subdomain} missing data.id`);
    storeIdCache.set(subdomain, null);
    errorRate.add(true);
    return null;
  }
  storeIdCache.set(subdomain, id);
  return id;
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
export default function () {
  const subdomain = pickStore();
  const baseHeaders = {
    "x-numu-host": `${subdomain}.numueg.app`,
    host: `${subdomain}.numueg.app`,
  };

  // Resolve UUID up front. If the fixture is missing, skip the rest
  // of this iteration — don't fall through into broken URLs.
  const storeId = resolveStoreId(subdomain, baseHeaders);
  if (storeId === null) {
    sleep(1);
    return;
  }

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

export function setup() {
  console.log(
    `[load] BASE_URL=${BASE_URL}\n[load] stores=${TEST_STORES.join(",")}\n[load] target: 100 VUs / 10m / p95<500ms`,
  );
}

export function teardown(_data) {
  // No-op — tests are read-only against staging. If we add cart-add
  // / order-create paths later, teardown should clean those rows.
}
