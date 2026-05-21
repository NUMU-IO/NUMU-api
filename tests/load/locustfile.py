"""NUMU API Load Test Suite.

Simulates realistic traffic across two personas:

  1. MerchantUser  — registers/logs in, lists stores, manages orders
  2. CustomerUser  — registers/logs in on a store, browses products, adds to cart, checks out

Before running:
  - The API must be running (default: http://localhost:8021)
  - Set LOAD_TEST_SUBDOMAIN env var to the subdomain of an existing tenant
    (required: the tenant middleware resolves tenant from Host header)
  - Set LOAD_TEST_STORE_ID env var to an existing store ID
    (required for customer flows: browse, cart, checkout)

Usage:
    LOAD_TEST_SUBDOMAIN=mystore LOAD_TEST_STORE_ID=<uuid> python scripts/load_test.py smoke

See tests/load/README.md for profiles and configuration.
"""

import os
import random
import string

from locust import HttpUser, between, tag, task

# Tenant subdomain — required so the middleware can set RLS context.
SUBDOMAIN = os.environ.get("LOAD_TEST_SUBDOMAIN", "")

# An existing store the customer flows will target.
STORE_ID = os.environ.get("LOAD_TEST_STORE_ID", "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _random_email(prefix: str = "loadtest") -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"{prefix}+{suffix}@numu-test.io"


def _random_phone() -> str:
    return f"+2010{random.randint(10000000, 99999999)}"


# ============================================================================
# Merchant persona
# ============================================================================


class MerchantUser(HttpUser):
    """Simulates a NUMU merchant: register/login -> list stores -> manage orders."""

    wait_time = between(1, 3)
    weight = 1  # relative spawn weight vs CustomerUser

    def on_start(self):
        """Authenticate once when this simulated user starts."""
        if SUBDOMAIN:
            self.client.headers.update({"Host": f"{SUBDOMAIN}.localhost"})

        self.access_token = None
        self.user_email = _random_email("merchant")
        self.user_password = "LoadTest123!"
        self.store_id = ""

        self._authenticate()

    # -- Auth helpers (called once) ------------------------------------------

    def _authenticate(self):
        """Register a new user, fall back to login if email is taken."""
        resp = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": self.user_email,
                "password": self.user_password,
                "first_name": "Load",
                "last_name": "Merchant",
            },
            name="[merchant] /auth/register",
        )

        if resp.status_code in (200, 201):
            self._extract_token(resp)
            return

        # 409 = email already exists → try login
        if resp.status_code in (400, 409, 422):
            self._login()

    def _login(self):
        """Login with existing credentials."""
        resp = self.client.post(
            "/api/v1/auth/login",
            json={
                "email": self.user_email,
                "password": self.user_password,
            },
            name="[merchant] /auth/login",
        )
        if resp.status_code == 200:
            self._extract_token(resp)

    def _extract_token(self, resp):
        """Pull access_token from standard auth response."""
        try:
            data = resp.json().get("data", {})
            tokens = data.get("tokens", {})
            self.access_token = tokens.get("access_token")
        except Exception:
            pass

    def _headers(self):
        if self.access_token:
            return {"Authorization": f"Bearer {self.access_token}"}
        return {}

    # -- Tasks ---------------------------------------------------------------

    @tag("health")
    @task(1)
    def health_check(self):
        self.client.get("/api/v1/health", name="/health")

    @tag("stores")
    @task(3)
    def list_stores(self):
        if not self.access_token:
            return

        resp = self.client.get(
            "/api/v1/stores",
            headers=self._headers(),
            name="[merchant] GET /stores",
        )
        if resp.status_code == 200:
            try:
                data = resp.json().get("data", {})
                items = data.get("items", [])
                if items:
                    self.store_id = items[0].get("id", "")
            except Exception:
                pass

    @tag("orders")
    @task(2)
    def list_orders(self):
        if not self.access_token or not self.store_id:
            return

        resp = self.client.get(
            f"/api/v1/stores/{self.store_id}/orders",
            headers=self._headers(),
            name="[merchant] GET /stores/[id]/orders",
        )
        if resp.status_code == 200:
            try:
                data = resp.json().get("data", {})
                items = data.get("items", [])
                order_ids = [o.get("id") for o in items if o.get("id")]
                if order_ids:
                    self._update_order_status(random.choice(order_ids))
            except Exception:
                pass

    def _update_order_status(self, order_id: str):
        """Attempt to update an order status (not a separate task to avoid orphan calls)."""
        new_status = random.choice(["confirmed", "processing", "shipped"])
        self.client.patch(
            f"/api/v1/stores/{self.store_id}/orders/{order_id}/status",
            json={"status": new_status},
            headers=self._headers(),
            name="[merchant] PATCH /stores/[id]/orders/[id]/status",
        )


# ============================================================================
# Customer persona (requires LOAD_TEST_STORE_ID)
# ============================================================================


class CustomerUser(HttpUser):
    """Simulates a storefront customer: register/login -> browse -> cart -> checkout."""

    wait_time = between(1, 3)
    weight = 2  # more customers than merchants

    def on_start(self):
        """Authenticate once when this simulated customer starts."""
        if SUBDOMAIN:
            self.client.headers.update({"Host": f"{SUBDOMAIN}.localhost"})

        self.access_token = None
        self.product_ids: list[str] = []

        if not STORE_ID:
            return  # all tasks will be skipped gracefully

        self.user_email = _random_email("customer")
        self.user_password = "Customer123!"

        self._authenticate()

    # -- Auth helpers --------------------------------------------------------

    def _authenticate(self):
        resp = self.client.post(
            f"/api/v1/storefront/store/{STORE_ID}/auth/register",
            json={
                "email": self.user_email,
                "password": self.user_password,
                "first_name": "Load",
                "last_name": "Customer",
                "phone": _random_phone(),
            },
            name="[customer] /storefront/auth/register",
        )

        if resp.status_code in (200, 201):
            self._extract_token(resp)
            return

        if resp.status_code in (400, 409, 422):
            self._login()

    def _login(self):
        resp = self.client.post(
            f"/api/v1/storefront/store/{STORE_ID}/auth/login",
            json={
                "email": self.user_email,
                "password": self.user_password,
            },
            name="[customer] /storefront/auth/login",
        )
        if resp.status_code == 200:
            self._extract_token(resp)

    def _extract_token(self, resp):
        try:
            data = resp.json().get("data", {})
            tokens = data.get("tokens", {})
            self.access_token = tokens.get("access_token")
        except Exception:
            pass

    def _headers(self):
        if self.access_token:
            return {"Authorization": f"Bearer {self.access_token}"}
        return {}

    # -- Tasks ---------------------------------------------------------------

    @tag("health")
    @task(1)
    def health_check(self):
        self.client.get("/api/v1/health", name="/health")

    @tag("browse")
    @task(5)
    def browse_products(self):
        if not STORE_ID:
            return

        resp = self.client.get(
            f"/api/v1/storefront/store/{STORE_ID}/products",
            name="[customer] GET /storefront/products",
        )
        if resp.status_code == 200:
            try:
                data = resp.json().get("data", {})
                items = data.get("items", [])
                self.product_ids = [p.get("id") for p in items if p.get("id")]
            except Exception:
                pass

    @tag("browse")
    @task(3)
    def view_product(self):
        if not STORE_ID or not self.product_ids:
            return

        pid = random.choice(self.product_ids)
        self.client.get(
            f"/api/v1/storefront/store/{STORE_ID}/products/{pid}",
            name="[customer] GET /storefront/products/[id]",
        )

    @tag("cart")
    @task(2)
    def add_to_cart(self):
        if not STORE_ID or not self.access_token or not self.product_ids:
            return

        product_id = random.choice(self.product_ids)
        self.client.post(
            "/api/v1/storefront/me/cart/items",
            json={
                "product_id": product_id,
                "quantity": random.randint(1, 3),
            },
            headers=self._headers(),
            name="[customer] POST /storefront/me/cart/items",
        )

    @tag("checkout")
    @task(1)
    def checkout(self):
        if not STORE_ID or not self.access_token or not self.product_ids:
            return

        self.client.post(
            f"/api/v1/storefront/store/{STORE_ID}/checkout",
            json={
                "line_items": [
                    {"product_id": random.choice(self.product_ids), "quantity": 1},
                ],
                "shipping_address": {
                    "first_name": "Load",
                    "last_name": "Tester",
                    "address_line1": "123 Test St",
                    "city": "Cairo",
                    "country": "EG",
                    "phone": _random_phone(),
                },
                "payment_method": "cod",
            },
            headers=self._headers(),
            name="[customer] POST /storefront/checkout",
        )
