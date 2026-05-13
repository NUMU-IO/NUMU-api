# Load tests (Phase 5.9)

k6 scripts that simulate real shopper traffic against the storefront.
Pass criteria from the audit plan:

- **p95 < 500ms** across the read-path mix
- **error rate < 1%** (no 5xx)
- **100 concurrent VUs for 10 minutes**

## Prerequisites

- [k6 installed](https://k6.io/docs/get-started/installation/) (`brew install k6` on macOS, `choco install k6` on Windows)
- Staging environment up and reachable
- 10 test stores seeded with demo catalogs (one-time):

  ```
  for n in {1..10}; do
    curl -X POST $STAGING/api/v1/stores \
      -H "Authorization: Bearer $TOKEN" \
      -d '{"name":"load-store-'$n'","subdomain":"load-store-'$n'"}'
    # then POST /stores/{id}/seed-demo
  done
  ```

## Running

Quick smoke (5 VUs, 30s):

```bash
k6 run -e BASE_URL=https://staging.numueg.app --vus 5 --duration 30s scripts/load/storefront_load.js
```

Full pass-criteria run (100 VUs, 10 min):

```bash
k6 run -e BASE_URL=https://staging.numueg.app scripts/load/storefront_load.js
```

Override the test stores:

```bash
k6 run -e BASE_URL=… -e TEST_STORES=mystore-a,mystore-b scripts/load/storefront_load.js
```

### How the script discovers stores

Each VU resolves `subdomain → store_id` once via `GET /api/v1/storefront/store-by-subdomain/{subdomain}` and caches the UUID for the rest of its run. If you change subdomain values in `TEST_STORES`, the script still works — it just does one extra lookup per VU per store.

If a subdomain 404s, the VU **skips** that store for the rest of the run (no retry storm). A warning is printed to the run log; a missing-fixture run will pass the test but the run log makes the data drop obvious.

## CI integration

The weekly cron in `.github/workflows/load-test.yml` (Phase 5.10
follow-up) runs the full test against staging every Sunday 02:00 UTC
and posts the summary to Slack `#perf`. PR-time runs are gated to
`labels: load-test` to keep the default CI fast.

## Interpreting failures

Per-route p95 trends in the summary tell you which path regressed:

- **home_latency_ms** > 400 → typically theme-resolution slow path;
  check `fetch_active_theme` cache hit rate
- **pdp_latency_ms** > 300 → product cache miss; verify the
  `/api/v1/storefront/store/<id>/products` response is being cached
  by the API client's tag-based revalidation
- **plp_latency_ms** > 400 → ListProductsUseCase scanning more than
  needed; check that `is_active=True` is filtering before pagination
- **cart_latency_ms** > 200 → Redis evicted; check
  `RedisCartRepository.DEFAULT_TTL_SECONDS` (should be 30 days) and
  Redis maxmemory policy

## Adding new scenarios

Each scenario lives in its own file under `scripts/load/`. The
storefront-only test is the v1 baseline; future additions:

- **checkout_load.js** — exercises the payment-create path, requires
  a payment-provider sandbox token in env
- **admin_load.js** — merchant hub read paths under load
- **webhook_dispatch.js** — outbound webhook delivery throughput
  with the dispatcher under load

Keep scenario files isolated — k6 `--config scenarios.json` for
multi-scenario runs is documented but the per-file approach is easier
to debug.
