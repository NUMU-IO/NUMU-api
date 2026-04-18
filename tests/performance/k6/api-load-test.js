/**
 * k6 API Load Test for NUMU API
 *
 * Tests API performance under load with 3G-optimized thresholds.
 *
 * Usage:
 *   k6 run tests/performance/k6/api-load-test.js
 *   k6 run tests/performance/k6/api-load-test.js --env API_URL=https://staging.api.numueg.app
 *
 * Thresholds based on 3G network optimization targets:
 *   - p95 < 2000ms (acceptable for 3G)
 *   - p99 < 3000ms (worst case for 3G)
 *   - Error rate < 1%
 */

import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

// Custom metrics
const errorRate = new Rate("errors");
const productListTrend = new Trend("product_list_duration");
const categoryTrend = new Trend("category_duration");
const healthTrend = new Trend("health_duration");

// Configuration
const BASE_URL = __ENV.API_URL || "http://localhost:8000";
const STORE_ID = __ENV.STORE_ID || "00000000-0000-0000-0000-000000000001";

// Test options
export const options = {
  // Ramp up pattern simulating realistic traffic
  stages: [
    { duration: "30s", target: 10 }, // Warm up
    { duration: "1m", target: 50 }, // Ramp up to 50 users
    { duration: "2m", target: 50 }, // Sustained load
    { duration: "30s", target: 100 }, // Peak load
    { duration: "1m", target: 100 }, // Sustained peak
    { duration: "30s", target: 0 }, // Ramp down
  ],

  // Performance thresholds (3G optimized)
  thresholds: {
    // Overall HTTP duration
    http_req_duration: [
      "p(95)<2000", // 95% of requests under 2s
      "p(99)<3000", // 99% of requests under 3s
    ],

    // Error rate
    http_req_failed: ["rate<0.01"], // Error rate < 1%
    errors: ["rate<0.01"],

    // Endpoint-specific thresholds
    "http_req_duration{endpoint:products}": ["p(95)<1500"],
    "http_req_duration{endpoint:categories}": ["p(95)<1000"],
    "http_req_duration{endpoint:health}": ["p(95)<200"],

    // Custom metric thresholds
    product_list_duration: ["p(95)<1500"],
    category_duration: ["p(95)<1000"],
    health_duration: ["p(95)<200"],
  },

  // Tags for result filtering
  tags: {
    testType: "load",
    environment: __ENV.ENVIRONMENT || "local",
  },
};

// Helper function to make requests with error handling
function makeRequest(url, params = {}, tags = {}) {
  const response = http.get(url, {
    tags: tags,
    timeout: "10s",
    ...params,
  });

  const success =
    check(response, {
      "status is 200": (r) => r.status === 200,
      "response time OK": (r) => r.timings.duration < 3000,
    }) && response.status === 200;

  errorRate.add(!success);

  return response;
}

// Main test function
export default function () {
  // Health check (lightweight, frequent)
  {
    const url = `${BASE_URL}/api/v1/public/health`;
    const response = makeRequest(url, {}, { endpoint: "health" });
    healthTrend.add(response.timings.duration);
  }

  sleep(0.5);

  // Product listing (main endpoint, most critical)
  {
    const url = `${BASE_URL}/api/v1/storefront/store/${STORE_ID}/products?limit=15`;
    const response = makeRequest(url, {}, { endpoint: "products" });
    productListTrend.add(response.timings.duration);

    // Verify response structure
    if (response.status === 200) {
      check(response, {
        "products has data": (r) => {
          try {
            const body = JSON.parse(r.body);
            return body.data && body.data.items !== undefined;
          } catch {
            return false;
          }
        },
        "response size < 50KB": (r) => r.body.length < 50000,
      });
    }
  }

  sleep(1);

  // Product listing with pagination
  {
    const url = `${BASE_URL}/api/v1/storefront/store/${STORE_ID}/products?limit=15&page=2`;
    const response = makeRequest(url, {}, { endpoint: "products" });
    productListTrend.add(response.timings.duration);
  }

  sleep(0.5);

  // Categories endpoint
  {
    const url = `${BASE_URL}/api/v1/storefront/store/${STORE_ID}/categories`;
    const response = makeRequest(url, {}, { endpoint: "categories" });
    categoryTrend.add(response.timings.duration);
  }

  sleep(1);
}

// Setup function - runs once before all iterations
export function setup() {
  console.log(`Testing API at: ${BASE_URL}`);
  console.log(`Store ID: ${STORE_ID}`);

  // Verify API is reachable
  const healthResponse = http.get(`${BASE_URL}/api/v1/public/health`);
  if (healthResponse.status !== 200) {
    console.error(`API health check failed: ${healthResponse.status}`);
    console.error(`Response: ${healthResponse.body}`);
  }

  return { startTime: new Date().toISOString() };
}

// Teardown function - runs once after all iterations
export function teardown(data) {
  console.log(`Test started at: ${data.startTime}`);
  console.log(`Test completed at: ${new Date().toISOString()}`);
}

// Handle test summary
export function handleSummary(data) {
  // Output JSON summary for CI processing
  return {
    "tests/performance/k6/results/summary.json": JSON.stringify(data, null, 2),
    stdout: textSummary(data, { indent: "  ", enableColors: true }),
  };
}

// Text summary helper
function textSummary(data, opts) {
  const { metrics, root_group } = data;

  let summary = "\n";
  summary += "==================== SUMMARY ====================\n\n";

  // Key metrics
  if (metrics.http_req_duration) {
    const duration = metrics.http_req_duration.values;
    summary += `HTTP Request Duration:\n`;
    summary += `  avg: ${duration.avg.toFixed(2)}ms\n`;
    summary += `  p95: ${duration["p(95)"].toFixed(2)}ms\n`;
    summary += `  p99: ${duration["p(99)"].toFixed(2)}ms\n\n`;
  }

  if (metrics.http_req_failed) {
    summary += `Error Rate: ${(metrics.http_req_failed.values.rate * 100).toFixed(2)}%\n\n`;
  }

  // Custom metrics
  if (metrics.product_list_duration) {
    const duration = metrics.product_list_duration.values;
    summary += `Product List Duration:\n`;
    summary += `  avg: ${duration.avg.toFixed(2)}ms\n`;
    summary += `  p95: ${duration["p(95)"].toFixed(2)}ms\n\n`;
  }

  // Check results
  const checks = root_group.checks;
  if (checks && checks.length > 0) {
    summary += "Checks:\n";
    checks.forEach((check) => {
      const passRate = ((check.passes / (check.passes + check.fails)) * 100).toFixed(1);
      summary += `  ${check.name}: ${passRate}% passed\n`;
    });
  }

  summary += "\n=================================================\n";

  return summary;
}
