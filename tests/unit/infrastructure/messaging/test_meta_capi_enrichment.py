"""Unit tests for the identity-enrichment RESEND path in ``_send_event``.

This branch is the riskiest thing in the signal-quality change: it is the
only place where NUMU deliberately POSTs the *same* ``event_id`` to Meta a
second time. Get it wrong in one direction and the feature stays inert (the
bug it was written to fix); get it wrong in the other and every resend
outside Meta's 48-hour dedup window is counted as a **new conversion**,
inflating the merchant's reported revenue.

It shipped with zero coverage — the six existing pipeline tests all take the
happy INSERT path and never reach the ``IntegrityError`` handler.

Covered here:
  * resend fires only when the new payload ADDS a match key
  * resend is capped (no outbound amplifier against Meta's API)
  * resend is refused once the ORIGINAL event is outside the 48h window
  * the adopted row is looked up per-PIXEL (the new UNIQUE key)
  * the failed-row retry path still works and is not confused with enrichment
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from src.infrastructure.messaging.tasks.meta_capi import (
    _MAX_ENRICHMENT_RESENDS,
    _META_DEDUP_WINDOW_SECONDS,
    _adds_match_keys,
    _sweep_order_filter,
)

PIXEL_A = "1552896226251388"
PIXEL_B = "9999999999999999"
TENANT_ID = uuid4()
STORE_ID = uuid4()


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


class _ExistingRow:
    """Stand-in for the already-logged ``meta_event_log`` row."""

    def __init__(
        self,
        *,
        response_status: int | None = 200,
        attempt_count: int = 1,
        user_data: dict | None = None,
        event_time: datetime | None = None,
        pixel_id: str = PIXEL_A,
    ):
        self.id = uuid4()
        self.pixel_id = pixel_id
        self.response_status = response_status
        self.attempt_count = attempt_count
        self.request_payload = {"user_data": user_data or {}}
        self.event_time = event_time or datetime.now(UTC)
        self.last_error = "prior"


@pytest.fixture
def capi_harness(monkeypatch):
    """Drive ``_send_event`` down the IntegrityError branch.

    Returns a mutable dict: set ``harness["existing"]`` to the row the
    adopt-lookup should find, then read ``harness["posted"]`` to see whether
    anything actually went to Meta.
    """
    harness: dict = {"existing": None, "posted": None}

    response_mock = MagicMock()
    response_mock.status_code = 200
    response_mock.json.return_value = {"events_received": 1, "fbtrace_id": "t-1"}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, *, params, json):
            harness["posted"] = json
            return response_mock

    import src.infrastructure.messaging.tasks.meta_capi as meta_capi_module

    monkeypatch.setattr(meta_capi_module.httpx, "Client", FakeClient)

    fake_session = MagicMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=None)
    fake_session.commit = AsyncMock()
    fake_session.rollback = AsyncMock()

    fake_store = SimpleNamespace(
        id=STORE_ID,
        tenant_id=TENANT_ID,
        settings={"tracking": {"meta": {"capi_enabled": True, "pixel_id": PIXEL_A}}},
    )
    fake_store_repo = MagicMock()
    fake_store_repo.return_value.get_by_id = AsyncMock(return_value=fake_store)

    # create() ALWAYS collides — that is the branch under test.
    fake_log_repo = MagicMock()
    fake_log_repo.return_value.create = AsyncMock(
        side_effect=IntegrityError("stmt", {}, Exception("dup"))
    )
    fake_log_repo.return_value.update_response = AsyncMock()
    fake_log_repo.return_value.update_error = AsyncMock()

    fake_credential = SimpleNamespace(
        credentials_encrypted=b"blob", encryption_key_id="key-1", is_active=True
    )

    import src.infrastructure.database.connection as conn_module
    import src.infrastructure.external_services.secrets as secrets_module
    import src.infrastructure.repositories.meta_event_log_repository as log_repo_module
    import src.infrastructure.repositories.store_repository as store_repo_module
    import src.infrastructure.tenancy.rls as rls_module

    class FakeSessionFactory:
        def __call__(self):
            return fake_session

    monkeypatch.setattr(conn_module, "AsyncSessionLocal", FakeSessionFactory())
    monkeypatch.setattr(store_repo_module, "StoreRepository", fake_store_repo)
    monkeypatch.setattr(log_repo_module, "MetaEventLogRepository", fake_log_repo)
    monkeypatch.setattr(rls_module, "enable_rls_bypass", AsyncMock())
    monkeypatch.setattr(rls_module, "narrow_to_tenant", AsyncMock())

    fake_secrets = MagicMock()
    fake_secrets.decrypt = AsyncMock(return_value={"access_token": "tok"})
    monkeypatch.setattr(secrets_module, "get_secrets_manager", lambda: fake_secrets)

    executed: list = []

    async def _execute(statement):
        executed.append(statement)
        sql = str(statement).lower()
        result = MagicMock()
        if "meta_event_log" in sql:
            result.scalar_one_or_none.return_value = harness["existing"]
        else:
            result.scalar_one_or_none.return_value = fake_credential
        return result

    fake_session.execute = _execute
    harness["executed"] = executed
    harness["row"] = None
    return harness


async def _send(**overrides):
    from src.infrastructure.messaging.tasks.meta_capi import _send_event

    kwargs = {
        "task": MagicMock(request=MagicMock(retries=0)),
        "store_id": str(STORE_ID),
        "pixel_id": PIXEL_A,
        "event_name": "InitiateCheckout",
        "event_id": "evt-ic-1",
        "event_time": int(datetime.now(UTC).timestamp()),
        "event_source_url": None,
        "user_data": {},
        "custom_data": {},
        "test_event_code": None,
        "action_source": "website",
    }
    kwargs.update(overrides)
    return await _send_event(**kwargs)


# ---------------------------------------------------------------------------
# _adds_match_keys — the gate
# ---------------------------------------------------------------------------


class TestAddsMatchKeys:
    def test_new_key_appearing_qualifies(self):
        assert _adds_match_keys({"em": ["h"]}, {"em": None}) is True
        assert _adds_match_keys({"st": ["h"]}, {}) is True

    def test_changed_value_does_not_qualify(self):
        # Strictly additive: churn must not trigger a resend.
        assert _adds_match_keys({"em": ["new"]}, {"em": ["old"]}) is False

    def test_key_disappearing_does_not_qualify(self):
        assert _adds_match_keys({"em": None}, {"em": ["old"]}) is False

    def test_non_match_keys_are_ignored(self):
        # fbp/fbc/ip/ua are present from the first fire; a change is churn.
        assert (
            _adds_match_keys({"fbp": "fb.1.2.3", "client_ip_address": "1.2.3.4"}, {})
            is False
        )

    @pytest.mark.parametrize("stored", [None, "not-a-dict", 42])
    def test_malformed_stored_payload_is_refused(self, stored):
        # A row written by an older schema must not be able to trigger sends.
        assert _adds_match_keys({"em": ["h"]}, stored) is False

    def test_every_hashed_field_is_covered(self):
        """The gate must know about every match key ``hash_user_data`` emits.

        A new key added to the hasher and forgotten here would silently never
        trigger enrichment — exactly the failure mode this whole change exists
        to fix.
        """
        from src.infrastructure.external_services.meta.hashing import hash_user_data
        from src.infrastructure.messaging.tasks.meta_capi import _MATCH_KEY_FIELDS

        hashed = hash_user_data({
            "email": "a@b.co",
            "phone": "01001234567",
            "first_name": "Sara",
            "last_name": "Ali",
            "city": "Cairo",
            "state": "Giza",
            "zip": "11511",
            "country_code": "EG",
            "external_id": "sess-1",
        })
        emitted_pii_keys = {
            k
            for k, v in hashed.items()
            if v is not None
            and k not in {"fbp", "fbc", "client_ip_address", "client_user_agent"}
        }
        assert emitted_pii_keys <= set(_MATCH_KEY_FIELDS), (
            f"hash_user_data emits {emitted_pii_keys - set(_MATCH_KEY_FIELDS)} "
            "which _MATCH_KEY_FIELDS does not know about"
        )


# ---------------------------------------------------------------------------
# The resend branch end-to-end
# ---------------------------------------------------------------------------


class TestEnrichmentResend:
    async def test_resends_when_identity_is_newly_known(self, capi_harness):
        capi_harness["existing"] = _ExistingRow(user_data={"em": None, "ph": None})

        result = await _send(user_data={"email": "shopper@example.org"})

        assert result["status"] == "sent"
        assert capi_harness["posted"] is not None
        ud = capi_harness["posted"]["data"][0]["user_data"]
        assert ud["em"] == [_sha256("shopper@example.org")]

    async def test_no_resend_when_nothing_new(self, capi_harness):
        capi_harness["existing"] = _ExistingRow(
            user_data={"em": [_sha256("shopper@example.org")]}
        )

        result = await _send(user_data={"email": "shopper@example.org"})

        assert result["status"] == "duplicate"
        assert capi_harness["posted"] is None

    async def test_in_flight_row_is_never_resent(self, capi_harness):
        # response_status NULL = another worker owns it right now.
        capi_harness["existing"] = _ExistingRow(response_status=None, user_data={})

        result = await _send(user_data={"email": "shopper@example.org"})

        assert result["status"] == "duplicate"
        assert capi_harness["posted"] is None

    async def test_resend_cap_bounds_the_amplifier(self, capi_harness):
        capi_harness["existing"] = _ExistingRow(
            user_data={}, attempt_count=_MAX_ENRICHMENT_RESENDS
        )

        result = await _send(user_data={"email": "shopper@example.org"})

        assert result["status"] == "duplicate"
        assert capi_harness["posted"] is None

    async def test_one_below_the_cap_still_resends(self, capi_harness):
        capi_harness["existing"] = _ExistingRow(
            user_data={}, attempt_count=_MAX_ENRICHMENT_RESENDS - 1
        )

        result = await _send(user_data={"email": "shopper@example.org"})

        assert result["status"] == "sent"

    async def test_attempt_count_is_incremented_so_the_cap_can_bite(self, capi_harness):
        row = _ExistingRow(user_data={}, attempt_count=1)
        capi_harness["existing"] = row

        await _send(user_data={"email": "shopper@example.org"})

        assert row.attempt_count == 2

    async def test_failed_row_is_retried_regardless_of_match_keys(self, capi_harness):
        # The pre-existing retry-a-4xx behaviour must survive the new branch.
        capi_harness["existing"] = _ExistingRow(
            response_status=500, user_data={"em": [_sha256("shopper@example.org")]}
        )

        result = await _send(user_data={"email": "shopper@example.org"})

        assert result["status"] == "sent"
        assert capi_harness["posted"] is not None

    async def test_lookup_is_scoped_to_this_pixel(self, capi_harness):
        """The adopt-lookup must match the UNIQUE key exactly.

        With ``UNIQUE(store_id, pixel_id, event_id)`` a store's pixels each own
        a row. A lookup without ``pixel_id`` would adopt whichever pixel logged
        first and then overwrite ITS payload — corrupting pixel A's log while
        pixel B silently skipped.
        """
        capi_harness["existing"] = _ExistingRow(user_data={})

        await _send(pixel_id=PIXEL_B, user_data={"email": "shopper@example.org"})

        log_stmts = [
            s for s in capi_harness["executed"] if "meta_event_log" in str(s).lower()
        ]
        assert log_stmts, "no meta_event_log lookup was issued"
        sql = str(log_stmts[0].compile(compile_kwargs={"literal_binds": True}))
        assert "pixel_id" in sql
        assert PIXEL_B in sql


class TestEnrichmentDedupWindow:
    """Meta merges a repeated ``event_id`` only for 48 hours.

    Outside that window the resend is a NEW event — it double-counts. The
    window must therefore be measured from the ORIGINAL event, which is the
    one Meta already holds.
    """

    async def test_stale_event_is_not_resent(self, capi_harness):
        original = datetime.now(UTC) - timedelta(
            seconds=_META_DEDUP_WINDOW_SECONDS + 3600
        )
        capi_harness["existing"] = _ExistingRow(user_data={}, event_time=original)

        # The storefront's `refireFunnelWithIdentity` re-POSTs with the
        # ORIGINAL event_id but NO event_time, so `/track` stamps it with
        # `datetime.now()`. The incoming timestamp therefore says "0 seconds
        # old" no matter how stale the event Meta holds actually is.
        result = await _send(
            event_time=int(datetime.now(UTC).timestamp()),
            user_data={"email": "shopper@example.org"},
        )

        assert result["status"] == "duplicate", (
            "resent an event Meta will no longer deduplicate — this inflates "
            "the merchant's conversion count"
        )
        assert capi_harness["posted"] is None

    async def test_fresh_event_is_resent(self, capi_harness):
        original = datetime.now(UTC) - timedelta(hours=1)
        capi_harness["existing"] = _ExistingRow(user_data={}, event_time=original)

        result = await _send(
            event_time=int(datetime.now(UTC).timestamp()),
            user_data={"email": "shopper@example.org"},
        )

        assert result["status"] == "sent"

    async def test_window_boundary(self, capi_harness):
        just_inside = datetime.now(UTC) - timedelta(
            seconds=_META_DEDUP_WINDOW_SECONDS - 60
        )
        capi_harness["existing"] = _ExistingRow(user_data={}, event_time=just_inside)
        assert (await _send(user_data={"email": "shopper@example.org"}))[
            "status"
        ] == "sent"


# ---------------------------------------------------------------------------
# Funnel-step map additions + COD sweep filter
# ---------------------------------------------------------------------------


class TestFunnelMapAdditions:
    def test_collection_view_maps_to_pageview(self):
        from src.infrastructure.messaging.tasks.meta_capi import (
            FUNNEL_STEP_TO_META_EVENT,
        )

        assert FUNNEL_STEP_TO_META_EVENT["collection_view"] == "PageView"

    def test_add_shipping_info_is_mapped(self):
        from src.infrastructure.messaging.tasks.meta_capi import (
            FUNNEL_STEP_TO_META_EVENT,
        )

        assert FUNNEL_STEP_TO_META_EVENT["add_shipping_info"] == "AddShippingInfo"

    def test_every_mapped_step_is_a_real_funnel_step(self):
        """A typo'd key here is invisible: ``.get(step)`` just returns None and
        the CAPI enqueue silently no-ops — which is precisely how
        ``collection_view`` went missing in the first place."""
        from src.api.v1.routes.storefront.tracking import _VALID_FUNNEL_STEPS
        from src.infrastructure.messaging.tasks.meta_capi import (
            FUNNEL_STEP_TO_META_EVENT,
        )

        unknown = set(FUNNEL_STEP_TO_META_EVENT) - set(_VALID_FUNNEL_STEPS)
        assert not unknown, f"mapped steps that /track can never emit: {unknown}"


class TestSweepOrderFilter:
    def test_includes_paid_and_cod_orders(self):
        cutoff = datetime.now(UTC) - timedelta(hours=6)
        sql = str(
            _sweep_order_filter(cutoff).compile(compile_kwargs={"literal_binds": True})
        )
        assert "paid_at IS NOT NULL" in sql
        assert "paid_at IS NULL" in sql
        assert "payment_method = 'cod'" in sql
        assert "payment_method IS NULL" in sql
        assert "created_at" in sql

    def test_cod_branch_is_time_bounded(self):
        """Without a created_at bound the COD branch would scan every unpaid
        order the store has ever taken."""
        cutoff = datetime.now(UTC) - timedelta(hours=6)
        sql = str(
            _sweep_order_filter(cutoff).compile(compile_kwargs={"literal_binds": True})
        )
        # Both branches carry their own >= cutoff comparison.
        assert sql.count(">=") == 2
