# NUMU API — Load Tests

Locust-based load test suite simulating realistic merchant traffic across the
core API flows: auth, browse, cart, checkout, and order management.

## Quick Start

```bash
# Set required env vars (subdomain of an existing tenant + a store ID in that tenant)
set LOAD_TEST_SUBDOMAIN=mystore            # Windows
set LOAD_TEST_STORE_ID=<store-uuid>        # Windows
# export LOAD_TEST_SUBDOMAIN=mystore       # macOS / Linux
# export LOAD_TEST_STORE_ID=<store-uuid>   # macOS / Linux

# Run smoke test (10 users, 1 min)
python scripts/load_test.py smoke

# Run standard load test (100 users, 5 min)
python scripts/load_test.py load

# Run stress test (500 users, 10 min)
python scripts/load_test.py stress

# Open interactive web UI (http://localhost:8089)
python scripts/load_test.py ui

# Target a different host
python scripts/load_test.py load --host https://staging.numu.app
```

> The runner script works on **Windows, macOS, and Linux** — no `make` needed.

## Test Profiles

| Profile  | Users | Spawn Rate | Duration | Use Case                     |
| -------- | ----- | ---------- | -------- | ---------------------------- |
| **Smoke**   | 10    | 2/s        | 1 min    | CI gate / quick sanity check |
| **Load**    | 100   | 10/s       | 5 min    | Standard load validation     |
| **Stress**  | 500   | 25/s       | 10 min   | Capacity planning            |

## User Flows

Two personas simulate realistic traffic:

### MerchantUser (weight 1)
- Registers/logs in once on start via `/api/v1/auth`
- Lists stores (weight 3), manages orders (weight 2), health check (weight 1)

### CustomerUser (weight 2)
- Registers/logs in once on start via `/api/v1/storefront/store/{id}/auth`
- Browses products (weight 5), views single product (weight 3)
- Adds to cart (weight 2), checks out with COD (weight 1), health check (weight 1)

## Tag Filtering

Run a specific flow with `--tags`:

```bash
locust -f tests/load/locustfile.py --tags auth       # Auth only
locust -f tests/load/locustfile.py --tags browse      # Browse only
locust -f tests/load/locustfile.py --tags checkout    # Checkout only
locust -f tests/load/locustfile.py --tags orders      # Orders only
```

## Configuration

Default settings live in `locust.conf`.  Override via CLI flags:

```bash
locust -f tests/load/locustfile.py --host https://staging.numu.app \
       --users 200 --spawn-rate 20 --run-time 10m
```

Or via environment variables:

| Variable | Required | Description |
| --- | --- | --- |
| `LOAD_TEST_SUBDOMAIN` | **Yes** | Subdomain of the tenant (e.g. `mystore`). Sets the `Host` header so the tenant middleware resolves RLS context. |
| `LOAD_TEST_STORE_ID` | Yes (customer) | UUID of a store inside that tenant. Customer flows use this to browse, cart, and checkout. |
| `LOCUST_HOST` | No | Base URL (default `http://localhost:8021`). |

## Results

After a headless run, results are exported to `tests/load/results/`:

- `{profile}_stats.csv` — aggregate statistics per endpoint
- `{profile}_failures.csv` — failed request details
- `{profile}_stats_history.csv` — time-series data
- `{profile}.html` — self-contained HTML report

> The `results/` directory is git-ignored.

## Prerequisites

```bash
pip install locust
# or
pip install -e ".[dev]"
```
