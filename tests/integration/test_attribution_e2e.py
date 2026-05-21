"""End-to-end integration test scaffolding for feature 001 (UTM attribution).

Walks the 9-step ``specs/001-utm-campaign-attribution/quickstart.md``
flow:

  1. Create campaign → assert 201 + short_code populated
  2. Generate trackable link → assert URL + QR base64
  3. POST /track with attribution body (simulate landing)
  4. POST /track several times with no body attribution (simulate
     browsing — server should fall back to cookie parsing)
  5. POST /checkout with attribution → assert 201
  6. Assert orders row has campaign_id + attribution JSONB set
  7. Assert funnel_events rows are stamped with utm_* + campaign_id
  8. Assert customers.first_touch_attribution is populated
  9. GET /performance → assert totals + conversion rates match

Why this file exists but is skipped by default
------------------------------------------------

The full HTTP path requires three pieces that aren't available in the
default test environment:

* **The migration applied** — ``utm_campaign_attribution_20260521``
  adds the columns this test reads back; without it, every assertion
  errors on missing columns.
* **A Postgres backend** — several routes (marketing_campaigns,
  storefront/track, storefront/checkout) call
  ``AsyncSessionLocal()`` directly rather than the dep-injected
  ``get_db`` session. The default conftest swaps SQLite for the
  request-scoped session but cannot redirect the module-level
  ``AsyncSessionLocal``. SQLite also doesn't support ``JSONB``
  (the migration would also need the ``_patch_metadata_for_sqlite``
  treatment).
* **Auth dependency overrides** — ``verify_store_ownership`` and
  ``get_current_user_id`` need test-friendly stubs; the existing
  conftest doesn't include them yet.

Enabling locally
----------------

When a Postgres test env is in place, set ``NUMU_E2E_ATTRIBUTION=1``
and run::

    JWT_ALGORITHM=HS256 JWT_SECRET=... pytest tests/integration/test_attribution_e2e.py -v

The skip marker reads that env var.

Until then, this file serves as **documented contract** for what the
feature should do end-to-end, paired with the helper-level unit tests
that already pin every dangerous code path (111 unit tests across the
attribution stack).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import pytest
from httpx import AsyncClient

from src.core.entities.attribution import (
    ATTRIBUTION_SCHEMA_VERSION,
    AttributionSnapshot,
    AttributionTouch,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("NUMU_E2E_ATTRIBUTION") != "1",
    reason=(
        "E2E attribution test requires a Postgres test backend + the "
        "utm_campaign_attribution_20260521 migration applied + auth "
        "dependency overrides wired. Set NUMU_E2E_ATTRIBUTION=1 to run."
    ),
)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def fixed_now() -> datetime:
    """Stable timestamp so attribution.first_touch.ts is deterministic."""
    return datetime(2026, 5, 22, 14, 33, 0, tzinfo=UTC)


@pytest.fixture
def session_id() -> str:
    """ULID-ish session id — bound across all touches in this test run."""
    return "01HX2MTESTSESSIONIDXXXXXXX"


@pytest.fixture
def campaign_short_code() -> str:
    """Predictable short_code so we can assert the URL shape.

    In a real run the backend's short_code_generator picks this; the
    test should use whichever value comes back from the create_campaign
    response (campaign.short_code). This fixture only exists for the
    cookie / envelope construction below where we need to write a
    `utm_campaign` matching the server's choice.
    """
    return "AB7K9X"


@pytest.fixture
def utm_campaign_value(campaign_short_code: str) -> str:
    """The full utm_campaign string the link builder would emit:
    ``<slug>-<short_code>``. Tests reconstruct this so the cookie + the
    server-side resolver match without coupling tightly to the slug
    generation (which transliterates Arabic etc.)."""
    return f"eid-sale-quickstart-{campaign_short_code}"


@pytest.fixture
def attribution_envelope(
    fixed_now: datetime, session_id: str, utm_campaign_value: str
) -> AttributionSnapshot:
    """Construct an AttributionSnapshot matching what the storefront
    would write after first landing on the campaign URL."""
    touch = AttributionTouch(
        ts=fixed_now.isoformat(),
        utm_source="facebook",
        utm_medium="social",
        utm_campaign=utm_campaign_value,
        utm_term=None,
        utm_content=None,
        gclid=None,
        fbclid=None,
        referrer="https://www.facebook.com/",
        landing_path="/product/test-product",
    )
    return AttributionSnapshot(
        v=ATTRIBUTION_SCHEMA_VERSION,
        first_touch=touch,
        last_touch=touch,
        session_id=session_id,
    )


# ── Step 1: campaign creation ────────────────────────────────────


async def _create_campaign(
    client: AsyncClient,
    store_id: str,
    name: str = "Eid Sale Quickstart 2026",
    channel: str = "email",
    inline_subject: str = "Eid Sale — 20% off",
    inline_body: str = "Shop the Eid sale.",
) -> dict[str, Any]:
    """POST a campaign and return the parsed response body.

    Assertions:
    * 201 status
    * Response data contains a short_code (6-char Crockford)
    """
    res = await client.post(
        f"/api/v1/stores/{store_id}/marketing/campaigns",
        json={
            "channel": channel,
            "name": name,
            "inline_subject": inline_subject,
            "inline_body": inline_body,
        },
    )
    assert res.status_code == 201, f"expected 201, got {res.status_code}: {res.text}"
    body = res.json()
    data = body.get("data") or body
    # Short_code may be on the response only after T026 hub work — verify
    # via a follow-up GET if it's not in the initial body.
    if "short_code" not in data:
        get_res = await client.get(
            f"/api/v1/stores/{store_id}/marketing/campaigns/{data['id']}"
        )
        data = get_res.json().get("data") or get_res.json()
    assert data.get("short_code"), "campaign missing short_code"
    return data


# ── Step 2: trackable-link generation ────────────────────────────


async def _generate_trackable_link(
    client: AsyncClient,
    store_id: str,
    campaign_id: str,
    product_id: str,
    source: str = "facebook",
) -> dict[str, Any]:
    """POST /trackable-link and return the parsed response.

    Assertions:
    * 200 status
    * url contains utm_source=<source>
    * url contains the campaign short_code
    * qr_png_base64 is non-empty and decodes to PNG magic bytes
    """
    res = await client.post(
        f"/api/v1/stores/{store_id}/marketing/campaigns/{campaign_id}/trackable-link",
        json={
            "destination": {"kind": "product", "product_id": product_id},
            "source": source,
        },
    )
    assert res.status_code == 200, f"expected 200, got {res.status_code}: {res.text}"
    data = res.json().get("data") or res.json()
    assert "url" in data
    assert f"utm_source={source}" in data["url"]
    assert "utm_campaign=" in data["url"]
    assert data["qr_png_base64"]
    import base64

    raw = base64.b64decode(data["qr_png_base64"])
    assert raw.startswith(b"\x89PNG\r\n\x1a\n"), "qr_png_base64 not a valid PNG"
    return data


# ── Steps 3-4: simulated /track POSTs ────────────────────────────


async def _track(
    client: AsyncClient,
    store_id: str,
    *,
    path: str,
    fingerprint: str,
    attribution: AttributionSnapshot | None = None,
    attribution_cookie: str | None = None,
) -> None:
    """POST /track. The two attribution paths we want to exercise:

    * ``attribution`` kwarg → sent in body (modern storefront)
    * ``attribution_cookie`` kwarg → sent as Cookie: numu_attribution=...
      (legacy / cookie-only storefront)

    Server should accept either; body wins when both present.
    """
    payload: dict[str, Any] = {
        "path": path,
        "fingerprint": fingerprint,
        "referrer": "",
    }
    if attribution is not None:
        payload["attribution"] = attribution.model_dump(mode="json")

    headers: dict[str, str] = {}
    if attribution_cookie is not None:
        headers["Cookie"] = f"numu_attribution={attribution_cookie}"

    res = await client.post(
        f"/api/v1/storefront/store/{store_id}/track",
        json=payload,
        headers=headers,
    )
    assert res.status_code == 204, f"expected 204, got {res.status_code}: {res.text}"


# ── Step 5: checkout with attribution ────────────────────────────


async def _checkout(
    client: AsyncClient,
    store_id: str,
    product_id: str,
    attribution: AttributionSnapshot,
    fingerprint: str,
) -> dict[str, Any]:
    """POST /checkout with the attribution envelope. Returns the order
    creation response (order_id, order_number, etc.)."""
    res = await client.post(
        f"/api/v1/storefront/store/{store_id}/checkout",
        json={
            "line_items": [
                {"product_id": product_id, "quantity": 1},
            ],
            "shipping_address": {
                "first_name": "Alice",
                "last_name": "Shopper",
                "address_line1": "10 Tahrir Square",
                "city": "Cairo",
                "country": "EG",
                "phone": "01001234567",
            },
            "payment_method": "cod",
            "cod_requested": True,
            "guest_email": f"shopper_{uuid4().hex[:6]}@example.com",
            "attribution": attribution.model_dump(mode="json"),
            "session_fingerprint": fingerprint,
        },
    )
    assert res.status_code in (200, 201), (
        f"expected 200/201, got {res.status_code}: {res.text}"
    )
    return res.json().get("data") or res.json()


# ── Steps 6-8: direct DB assertions ──────────────────────────────


async def _assert_order_attribution(
    test_session,
    order_id: str,
    expected_campaign_id: str,
    expected_utm_source: str = "facebook",
    expected_utm_campaign_contains: str = "AB7K9X",
) -> None:
    """Query orders table for the row we just created; assert stamped."""
    from sqlalchemy import select

    from src.infrastructure.database.models.tenant.order import OrderModel

    row = (
        await test_session.execute(select(OrderModel).where(OrderModel.id == order_id))
    ).scalar_one()
    assert row.utm_source == expected_utm_source
    assert expected_utm_campaign_contains in (row.utm_campaign or "")
    assert str(row.campaign_id) == expected_campaign_id, (
        f"order.campaign_id mismatch: {row.campaign_id}"
    )
    assert row.attribution is not None
    assert (
        row.attribution.get("first_touch", {}).get("utm_source") == expected_utm_source
    )
    assert row.first_touch_at is not None


async def _assert_funnel_events_stamped(
    test_session,
    store_id: str,
    expected_campaign_id: str,
    minimum_rows: int = 3,
) -> None:
    """Every funnel_event row for this store in the window should
    carry the same campaign_id (since the cookie was set on first
    landing)."""
    from sqlalchemy import select

    from src.infrastructure.database.models.tenant.funnel_event import (
        FunnelEventModel,
    )

    rows = (
        (
            await test_session.execute(
                select(FunnelEventModel).where(
                    FunnelEventModel.store_id == store_id,
                    FunnelEventModel.campaign_id == expected_campaign_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) >= minimum_rows, (
        f"expected at least {minimum_rows} funnel_event rows, got {len(rows)}"
    )
    for r in rows:
        assert r.utm_source == "facebook"
        assert r.utm_campaign and "AB7K9X" in r.utm_campaign
        assert str(r.campaign_id) == expected_campaign_id


async def _assert_customer_first_touch(
    test_session,
    customer_id: str,
) -> None:
    """The first attributed order seeds customer.first_touch_attribution."""
    from sqlalchemy import select

    from src.infrastructure.database.models.tenant.customer import CustomerModel

    row = (
        await test_session.execute(
            select(CustomerModel).where(CustomerModel.id == customer_id)
        )
    ).scalar_one()
    assert row.first_touch_attribution is not None
    assert row.first_touch_at is not None
    assert row.first_touch_attribution.get("utm_source") == "facebook"


# ── Step 9: per-campaign performance dashboard ───────────────────


async def _assert_campaign_performance(
    client: AsyncClient,
    store_id: str,
    campaign_id: str,
    expected_orders: int = 1,
) -> None:
    """GET /performance and assert the rollup."""
    now = datetime.now(UTC)
    res = await client.get(
        f"/api/v1/stores/{store_id}/marketing/campaigns/{campaign_id}/performance",
        params={
            "date_from": (now - timedelta(days=1)).isoformat(),
            "date_to": (now + timedelta(days=1)).isoformat(),
        },
    )
    assert res.status_code == 200, f"expected 200, got {res.status_code}: {res.text}"
    data = res.json().get("data") or res.json()
    totals = data["totals"]
    assert totals["orders"] == expected_orders
    assert totals["sessions"] >= 1
    assert totals["revenue_cents"] > 0
    assert 0.0 <= totals["conversion_rates"]["session_to_order"] <= 1.0


# ── The end-to-end test ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_attribution_quickstart_end_to_end(
    client: AsyncClient,
    test_session,
    # Future work: seed_store / seed_user / seed_product / seed_customer
    # fixtures pre-populating the test DB and overriding auth deps.
    # For now, the test expects those to exist as conftest fixtures.
):
    """The 9-step quickstart flow as one end-to-end assertion chain.

    The shape of this test pins the contract every PR in feature 001
    has to honor. As long as every step's assertion passes, the MVP
    promise — "merchant generates link → customer clicks → buys →
    sees revenue attributed" — is structurally sound.
    """
    # NOTE: the following identifiers come from seed fixtures that
    # don't exist yet — adding them is the remaining work for this
    # test to run. The assertion bodies above are complete and
    # exercise the actual columns the migration added.
    store_id = str(uuid4())  # TODO: seed_store.id
    product_id = str(uuid4())  # TODO: seed_product.id
    customer_id = str(uuid4())  # TODO: seed_customer.id
    fingerprint = "fp-" + uuid4().hex[:16]

    # Step 1: campaign creation
    campaign = await _create_campaign(client, store_id)
    campaign_id = campaign["id"]
    short_code = campaign["short_code"]
    utm_campaign_value = f"eid-sale-quickstart-2026-{short_code}"

    # Step 2: trackable link
    link = await _generate_trackable_link(
        client, store_id, campaign_id, product_id, source="facebook"
    )
    assert short_code in link["url"]

    # Build an attribution envelope matching the URL the merchant just
    # produced (in production, this is what the storefront cookie
    # would contain after the visitor clicks the link).
    now = datetime.now(UTC)
    touch = AttributionTouch(
        ts=now.isoformat(),
        utm_source="facebook",
        utm_medium="social",
        utm_campaign=utm_campaign_value,
        utm_term=None,
        utm_content=None,
        gclid=None,
        fbclid=None,
        referrer="https://www.facebook.com/",
        landing_path=f"/product/{product_id}",
    )
    envelope = AttributionSnapshot(
        v=ATTRIBUTION_SCHEMA_VERSION,
        first_touch=touch,
        last_touch=touch,
        session_id="01HX2MTESTSESSIONIDXXXXXXX",
    )

    # Step 3: simulate landing with body attribution
    await _track(
        client,
        store_id,
        path=f"/product/{product_id}",
        fingerprint=fingerprint,
        attribution=envelope,
    )

    # Step 4: simulate browsing — multiple /track calls with only the
    # cookie set (no body attribution). Server should fall back to
    # cookie parsing and stamp each row.
    cookie_value = quote(envelope.model_dump_json())
    for path in ("/", "/products", f"/product/{product_id}", "/cart"):
        await _track(
            client,
            store_id,
            path=path,
            fingerprint=fingerprint,
            attribution_cookie=cookie_value,
        )

    # Step 5: checkout
    order = await _checkout(client, store_id, product_id, envelope, fingerprint)
    order_id = order["order_id"]

    # Step 6: order DB assertions
    await _assert_order_attribution(
        test_session,
        order_id=order_id,
        expected_campaign_id=campaign_id,
    )

    # Step 7: funnel-event DB assertions
    await _assert_funnel_events_stamped(
        test_session,
        store_id=store_id,
        expected_campaign_id=campaign_id,
        # We POSTed: 1 landing + 4 browse = 5 calls; backend can dedupe
        # some of them as redundant beacons, so a floor of 3 is the
        # robust lower bound.
        minimum_rows=3,
    )

    # Step 8: customer first-touch seed
    await _assert_customer_first_touch(test_session, customer_id=customer_id)

    # Step 9: performance dashboard rollup
    await _assert_campaign_performance(client, store_id, campaign_id, expected_orders=1)


# ── Negative tests (also gated; same skip marker) ────────────────


@pytest.mark.asyncio
async def test_cross_tenant_campaign_id_is_isolated(
    client: AsyncClient,
    test_session,
):
    """Attribution carrying store A's short_code, posted at checkout
    on store B, must NOT stamp store A's campaign_id on store B's
    order. SEC-006 cross-tenant safety.
    """
    store_a = str(uuid4())  # TODO: seed_store_a.id
    store_b = str(uuid4())  # TODO: seed_store_b.id
    product_b = str(uuid4())  # TODO: seed_product on store_b
    fingerprint = "fp-" + uuid4().hex[:16]

    # Create campaign on store A; capture its short_code.
    campaign_a = await _create_campaign(client, store_a, name="Store A Campaign")
    short_code_a = campaign_a["short_code"]
    utm_campaign_from_a = f"store-a-campaign-{short_code_a}"

    # Submit a checkout on store B with store A's utm_campaign.
    now = datetime.now(UTC)
    touch = AttributionTouch(
        ts=now.isoformat(),
        utm_source="facebook",
        utm_medium="social",
        utm_campaign=utm_campaign_from_a,
        utm_term=None,
        utm_content=None,
        gclid=None,
        fbclid=None,
        referrer="",
        landing_path="/",
    )
    envelope = AttributionSnapshot(
        v=ATTRIBUTION_SCHEMA_VERSION,
        first_touch=touch,
        last_touch=touch,
        session_id="01HX2MCROSSTENANTTESTXXXXX",
    )
    order = await _checkout(client, store_b, product_b, envelope, fingerprint)

    # Order on store B must NOT carry store A's campaign_id.
    from sqlalchemy import select

    from src.infrastructure.database.models.tenant.order import OrderModel

    row = (
        await test_session.execute(
            select(OrderModel).where(OrderModel.id == order["order_id"])
        )
    ).scalar_one()
    assert row.campaign_id is None, (
        f"SEC-006 violation: order on store B carries campaign_id "
        f"{row.campaign_id} from store A"
    )
    # The raw UTM strings ARE preserved (per FR-011) — they just don't
    # resolve to a campaign_id.
    assert utm_campaign_from_a in (row.utm_campaign or "")


@pytest.mark.asyncio
async def test_organic_share_does_not_create_campaign(
    client: AsyncClient,
    test_session,
):
    """An order arriving via utm_campaign=organic_share must record the
    raw UTMs but NOT create a campaign row. Per FR-011 + US4.
    """
    store_id = str(uuid4())  # TODO: seed_store.id
    product_id = str(uuid4())  # TODO: seed_product.id
    fingerprint = "fp-" + uuid4().hex[:16]

    now = datetime.now(UTC)
    touch = AttributionTouch(
        ts=now.isoformat(),
        utm_source="customer_share",
        utm_medium="whatsapp",
        utm_campaign="organic_share",
        utm_term=None,
        utm_content=None,
        gclid=None,
        fbclid=None,
        referrer="https://wa.me/",
        landing_path=f"/product/{product_id}",
    )
    envelope = AttributionSnapshot(
        v=ATTRIBUTION_SCHEMA_VERSION,
        first_touch=touch,
        last_touch=touch,
        session_id="01HX2MORGANICSHARETESTXXX",
    )
    order = await _checkout(client, store_id, product_id, envelope, fingerprint)

    from sqlalchemy import select

    from src.infrastructure.database.models.tenant.marketing_campaign import (
        MarketingCampaignModel,
    )
    from src.infrastructure.database.models.tenant.order import OrderModel

    order_row = (
        await test_session.execute(
            select(OrderModel).where(OrderModel.id == order["order_id"])
        )
    ).scalar_one()
    assert order_row.utm_source == "customer_share"
    assert order_row.utm_campaign == "organic_share"
    assert order_row.campaign_id is None, "organic_share must not stamp a campaign_id"

    # No MarketingCampaign row should have been created.
    campaigns = (
        (
            await test_session.execute(
                select(MarketingCampaignModel).where(
                    MarketingCampaignModel.store_id == store_id
                )
            )
        )
        .scalars()
        .all()
    )
    organic_campaign_names = [
        c.name for c in campaigns if "organic" in (c.name or "").lower()
    ]
    assert not organic_campaign_names, (
        f"Unexpected organic-share campaign rows: {organic_campaign_names}"
    )


# ── Performance probe (T061 — also gated) ────────────────────────


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("NUMU_E2E_ATTRIBUTION_PERF") != "1",
    reason=(
        "Performance probe (T061) seeds ~10k funnel_event rows and asserts "
        "the performance endpoint returns in ≤3s (SC-008). Set "
        "NUMU_E2E_ATTRIBUTION_PERF=1 to run."
    ),
)
async def test_campaign_performance_under_load(
    client: AsyncClient,
    test_session,
):
    """SC-008: campaign performance dashboard renders in <3s with 10k
    attributed funnel events. Seeds rows directly via the repository
    (faster than POSTing through /track 10k times)."""
    # TODO: seed 10k FunnelEventModel rows for a single (store, campaign)
    # via session.add_all + commit, then time the /performance call.
    # Skeleton:
    #
    # store_id, campaign_id = ...
    # async with AsyncSessionLocal() as s:
    #     events = [FunnelEventModel(...) for _ in range(10_000)]
    #     s.add_all(events); await s.commit()
    #
    # start = time.perf_counter()
    # res = await client.get(f"/.../performance?...")
    # elapsed = time.perf_counter() - start
    # assert elapsed < 3.0, f"performance returned in {elapsed:.2f}s"

    pytest.skip("Seed scaffolding TODO — see body comment")


# ── Notes for future maintainers ─────────────────────────────────

"""
Adding the missing pieces
-------------------------

1. **Seed fixtures** — add an autouse async fixture (or a parameterized
   one) that, before each test, runs the alembic migration on
   ``test_session`` and inserts a tenant + user + store + product +
   customer using the entity → model converters. Most repos in this
   repo accept the test session via the dep override; the ones that
   bypass it (marketing_campaigns) need either a monkeypatch on
   ``AsyncSessionLocal`` or a route refactor (see plan.md risks).

2. **Auth bypass** — in the ``client`` fixture, also set::

       from src.api.dependencies import verify_store_ownership, get_current_user_id

       app.dependency_overrides[verify_store_ownership] = lambda: seeded_store
       app.dependency_overrides[get_current_user_id] = lambda: seeded_user.id

3. **JSONB on SQLite** — the existing ``_patch_metadata_for_sqlite``
   converts JSONB → JSON. That makes ``attribution`` / ``step_data`` /
   ``first_touch_attribution`` readable, but loses the GIN indexes;
   that's fine for correctness tests, not for the perf probe.

4. **Migration parity** — the test engine should apply
   ``alembic upgrade head`` against the in-memory SQLite or, better, a
   per-test Postgres. The latter is the only way to verify the
   partial indexes (``ix_orders_store_campaign_created`` etc.) actually
   produce the right query plans.
"""
