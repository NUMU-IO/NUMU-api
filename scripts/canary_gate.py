"""Production canary gate: health, latency, login, orders, and checkout.

The smoke test creates one COD order in the dedicated load-test store, then
cancels and permanently deletes only that exact order.  Run with the canary
service account credentials supplied through the environment.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class GateError(RuntimeError):
    pass


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise GateError("cannot calculate a percentile without samples")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


class ApiClient:
    def __init__(self, base_url: str, variant: str):
        self.base_url = base_url.rstrip("/")
        self.variant = variant
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar)
        )
        self.cookies = jar
        self.csrf_token: str | None = None

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        extra_headers: dict[str, str] | None = None,
        expected: tuple[int, ...] = (200,),
        verify_variant: bool = True,
    ) -> dict:
        headers = {
            "Accept": "application/json",
            "User-Agent": "NUMU-Canary-Gate/1.0",
            "X-NUMU-Variant": self.variant,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.csrf_token and method not in ("GET", "HEAD"):
            headers["X-CSRF-Token"] = self.csrf_token
        headers.update(extra_headers or {})
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
                response_status = response.status
                selected = response.headers.get("X-NUMU-Variant")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise GateError(f"{method} {path} returned {exc.code}: {detail}") from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            raise GateError(f"{method} {path} failed: {exc}") from exc

        if response_status not in expected:
            raise GateError(f"{method} {path} returned unexpected {response_status}")
        if verify_variant and selected != self.variant:
            raise GateError(
                f"{method} {path} requested {self.variant}, router selected {selected!r}"
            )
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GateError(f"{method} {path} returned invalid JSON") from exc

    def login(self, email: str, password: str) -> None:
        body = self.request(
            "POST", "/api/v1/auth/login", body={"email": email, "password": password}
        )
        if body.get("data", {}).get("requires_2fa"):
            raise GateError("canary service account must not require 2FA")
        self.request("GET", "/api/v1/auth/csrf-token")
        self.csrf_token = next(
            (cookie.value for cookie in self.cookies if cookie.name == "csrf_token"),
            None,
        )
        if not self.csrf_token:
            raise GateError("login succeeded but no CSRF cookie was issued")


def check_health(base_url: str, samples: int, max_ratio: float) -> tuple[float, float]:
    timings: dict[str, list[float]] = {"stable": [], "candidate": []}
    for _ in range(samples):
        for variant in ("stable", "candidate"):
            started = time.perf_counter()
            body = ApiClient(base_url, variant).request("GET", "/api/v1/health")
            elapsed = time.perf_counter() - started
            if body.get("success") is not True:
                raise GateError(f"{variant} health response was not successful")
            timings[variant].append(elapsed)

    stable_p95 = percentile(timings["stable"], 0.95)
    candidate_p95 = percentile(timings["candidate"], 0.95)
    allowed = stable_p95 * max_ratio
    print(
        f"latency p95: stable={stable_p95 * 1000:.0f}ms "
        f"candidate={candidate_p95 * 1000:.0f}ms allowed={allowed * 1000:.0f}ms"
    )
    if candidate_p95 > allowed:
        raise GateError(
            f"candidate p95 is {candidate_p95 / stable_p95:.2f}x stable; "
            f"maximum is {max_ratio:.2f}x"
        )
    return stable_p95, candidate_p95


def functional_smoke(base_url: str, email: str, password: str, subdomain: str) -> None:
    candidate = ApiClient(base_url, "candidate")
    cleanup = ApiClient(base_url, "stable")
    candidate.login(email, password)
    cleanup.login(email, password)

    store_body = candidate.request(
        "GET", f"/api/v1/storefront/store-by-subdomain/{subdomain}"
    )
    store_id = store_body.get("data", {}).get("id")
    if not store_id:
        raise GateError(f"canary store {subdomain!r} was not found")

    # Keep this dedicated fixture able to exercise guest COD checkout even
    # when platform defaults change. These writes are idempotent and scoped
    # only to the load-test store.
    fields_path = f"/api/v1/stores/{store_id}/settings/checkout-fields"
    fields = candidate.request("GET", fields_path).get("data", {})
    identity = fields.setdefault("identity", {})
    identity["require_verification"] = False
    candidate.request("PUT", fields_path, body=fields)
    candidate.request(
        "PATCH",
        f"/api/v1/stores/{store_id}/settings/payment",
        body={"cod_enabled": True},
    )

    product_body = candidate.request(
        "GET", f"/api/v1/storefront/store/{store_id}/products/demo-tshirt"
    )
    product_id = product_body.get("data", {}).get("id")
    if not product_id:
        raise GateError("canary demo product was not found")

    # Prove the authenticated merchant order path before creating test data.
    candidate.request("GET", f"/api/v1/stores/{store_id}/orders/?limit=1")

    order_id: str | None = None
    cancelled = False
    try:
        checkout_body = candidate.request(
            "POST",
            f"/api/v1/storefront/store/{store_id}/checkout",
            body={
                "line_items": [{"product_id": product_id, "quantity": 1}],
                "shipping_address": {
                    "first_name": "NUMU",
                    "last_name": "Canary",
                    "address_line1": "Automated production smoke test",
                    "city": "Cairo",
                    "state": "Cairo",
                    "country": "EG",
                    "phone": "+201000000091",
                },
                "payment_method": "cod",
                "cod_requested": True,
                "guest_email": "canary-checkout@numu-test.io",
                "customer_notes": "AUTOMATED CANARY - safe to cancel and delete",
            },
            extra_headers={"Idempotency-Key": str(uuid.uuid4())},
            expected=(201,),
        )
        order = checkout_body.get("data", {})
        order_id = order.get("order_id")
        order_number = order.get("order_number")
        if not order_id or not order_number:
            raise GateError("checkout succeeded without an order id and number")

        orders = candidate.request(
            "GET",
            f"/api/v1/stores/{store_id}/orders/?limit=10&search="
            f"{urllib.parse.quote(str(order_number))}",
        )
        ids = {item.get("id") for item in orders.get("data", {}).get("items", [])}
        if order_id not in ids:
            raise GateError(
                f"created order {order_number} did not appear in order list"
            )
        print(f"functional smoke passed with temporary order {order_number}")
    finally:
        if order_id:
            try:
                cleanup.request(
                    "DELETE",
                    f"/api/v1/stores/{store_id}/orders/{order_id}?reason="
                    "automated%20canary%20cleanup",
                    expected=(204,),
                )
                cancelled = True
            finally:
                if cancelled:
                    cleanup.request(
                        "DELETE", f"/api/v1/admin/orders/{order_id}", expected=(204,)
                    )


def self_test() -> None:
    assert percentile([5, 1, 3, 4, 2], 0.95) == 5
    assert percentile([0.1] * 19 + [0.124], 0.95) == 0.1
    print("canary gate self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://numueg.app")
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--max-latency-ratio", type=float, default=1.25)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0
    if args.samples < 1 or args.max_latency_ratio < 1:
        raise GateError("samples must be positive and latency ratio must be at least 1")

    check_health(args.base_url, args.samples, args.max_latency_ratio)
    if args.smoke:
        email = os.environ.get("NUMU_ADMIN_EMAIL")
        password = os.environ.get("NUMU_ADMIN_PASSWORD")
        if not email or not password:
            raise GateError("NUMU_ADMIN_EMAIL and NUMU_ADMIN_PASSWORD are required")
        functional_smoke(
            args.base_url,
            email,
            password,
            os.environ.get("CANARY_STORE_SUBDOMAIN", "load-store-1"),
        )
    print("canary gate passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GateError as exc:
        print(f"CANARY GATE FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
