"""Idempotent seeder for k6 load-test fixtures.

Ensures every subdomain in ``--stores`` exists on the target environment
with the demo catalog seeded. Safe to re-run any number of times — every
write is gated by a prior read.

Run from CI (load-test-weekly) before the k6 step, or locally with:

    python scripts/load/seed_load_test_stores.py \
        --base-url https://staging.numueg.app \
        --admin-email "$NUMU_ADMIN_EMAIL" \
        --admin-password "$NUMU_ADMIN_PASSWORD"

Exit codes:
    0 — all stores exist and have demo data
    1 — at least one store could not be created or seeded
    2 — auth / connectivity failure (script could not run)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass

import httpx

logger = logging.getLogger("seed_load_test_stores")

DEMO_PRODUCT_SLUG = "demo-tshirt"
DEFAULT_STORES = [f"load-store-{i}" for i in range(1, 11)]
TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)


@dataclass
class StoreResult:
    subdomain: str
    id: str | None = None
    created: bool = False
    seeded_demo: bool = False
    error: str | None = None


def login(client: httpx.Client, email: str, password: str) -> None:
    """Log in with platform credentials and prime CSRF state.

    Login itself is CSRF-exempt; the csrf_token cookie is set by a
    follow-up GET /auth/csrf-token. After that we mirror the cookie
    into X-CSRF-Token on the client so every subsequent POST passes
    the double-submit check.
    """
    r = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    if r.status_code != 200:
        raise RuntimeError(f"admin login failed: {r.status_code} {r.text[:200]}")
    body = r.json()
    if body.get("data", {}).get("requires_2fa"):
        raise RuntimeError(
            "admin account has 2FA enabled — use a service account without 2FA "
            "for CI seeding"
        )

    r = client.get("/api/v1/auth/csrf-token")
    if r.status_code != 200:
        raise RuntimeError(
            f"failed to fetch CSRF token: {r.status_code} {r.text[:200]}"
        )
    csrf = client.cookies.get("csrf_token")
    if not csrf:
        raise RuntimeError("csrf_token cookie not set after /auth/csrf-token")
    client.headers["X-CSRF-Token"] = csrf


def get_store_by_subdomain(client: httpx.Client, subdomain: str) -> dict | None:
    r = client.get(f"/api/v1/storefront/store-by-subdomain/{subdomain}")
    if r.status_code == 200:
        return r.json()["data"]
    if r.status_code == 404:
        return None
    raise RuntimeError(
        f"unexpected status looking up {subdomain}: {r.status_code} {r.text[:200]}"
    )


def create_store(client: httpx.Client, subdomain: str) -> dict:
    """Create a store as the logged-in user.

    Posts to POST /api/v1/stores/ — gated by require_store_owner, which
    accepts STORE_OWNER or SUPER_ADMIN. Idempotency comes from the prior
    get_store_by_subdomain check; this function assumes the subdomain
    is free.
    """
    body = {
        "name": f"Load Test {subdomain}",
        "subdomain": subdomain,
        "default_currency": "EGP",
        "default_language": "en",
    }
    r = client.post("/api/v1/stores/", json=body)
    if r.status_code not in (200, 201):
        raise RuntimeError(
            f"failed to create {subdomain}: {r.status_code} {r.text[:200]}"
        )
    return r.json()["data"]


def has_demo_product(client: httpx.Client, store_id: str) -> bool:
    """Check whether the seed demo product is reachable on the store.

    Uses the by-slug endpoint (200/404) rather than the list endpoint
    with a slug filter — the list endpoint does not actually filter
    by slug (only category_id / search), so the filter would be
    silently ignored.
    """
    r = client.get(f"/api/v1/storefront/store/{store_id}/products/{DEMO_PRODUCT_SLUG}")
    if r.status_code == 200:
        return True
    if r.status_code == 404:
        return False
    raise RuntimeError(
        f"unexpected status checking demo product on {store_id}: "
        f"{r.status_code} {r.text[:200]}"
    )


def seed_demo(client: httpx.Client, store_id: str) -> None:
    r = client.post(f"/api/v1/stores/{store_id}/seed-demo")
    if r.status_code not in (200, 201, 204):
        raise RuntimeError(
            f"failed to seed demo for {store_id}: {r.status_code} {r.text[:200]}"
        )


def reconcile_store(client: httpx.Client, subdomain: str) -> StoreResult:
    result = StoreResult(subdomain=subdomain)
    try:
        existing = get_store_by_subdomain(client, subdomain)
        if existing:
            result.id = existing["id"]
        else:
            created = create_store(client, subdomain)
            result.id = created["id"]
            result.created = True

        if not has_demo_product(client, result.id):
            seed_demo(client, result.id)
            result.seeded_demo = True
            if not has_demo_product(client, result.id):
                raise RuntimeError(
                    f"demo seeded for {subdomain} but product still not found"
                )
    except Exception as exc:  # noqa: BLE001 — top-level summarizer
        result.error = str(exc)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--admin-password", required=True)
    parser.add_argument(
        "--stores",
        default=",".join(DEFAULT_STORES),
        help="Comma-separated subdomains",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    subdomains = [s.strip() for s in args.stores.split(",") if s.strip()]
    results: list[StoreResult] = []

    with httpx.Client(base_url=args.base_url, timeout=TIMEOUT) as client:
        try:
            login(client, args.admin_email, args.admin_password)
        except Exception as exc:  # noqa: BLE001
            logger.error("auth failure: %s", exc)
            return 2

        for sd in subdomains:
            logger.info("reconciling %s ...", sd)
            results.append(reconcile_store(client, sd))

    for r in results:
        if r.error:
            logger.error("  %s: error=%s", r.subdomain, r.error)
        else:
            logger.info(
                "  %s: id=%s created=%s seeded_demo=%s",
                r.subdomain,
                r.id,
                r.created,
                r.seeded_demo,
            )

    summary = {
        "target": args.base_url,
        "stores": [asdict(r) for r in results],
        "all_ok": all(r.error is None for r in results),
    }
    print(json.dumps(summary, indent=2))
    return 0 if summary["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
