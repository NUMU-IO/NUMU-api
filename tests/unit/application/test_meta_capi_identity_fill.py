"""Unit tests for the identity-resolution + conversion-guard helpers added to
``meta_capi_purchase_dispatcher``.

These shipped untested. ``test_meta_capi_purchase_dispatcher.py`` even points
at a ``TestIdentityFromCustomer`` class that does not exist, and every test in
that module passes ``MagicMock()`` as the session — which makes
``fill_identity_from_customer`` bail out in its ``except Exception`` guard
before it ever reaches the query. So the function's whole body was dead in
the suite.

What matters here, in priority order:
  1. TENANT ISOLATION — the lookup must be scoped by ``store_id``, not just
     ``customer_id``. A cross-store read would leak a shopper's email into
     another merchant's Meta pixel.
  2. It must never raise. It runs inside payment webhooks; an exception here
     would fail the webhook, not just the tracking event.
  3. It must not overwrite what the buyer typed for THIS order.
  4. Placeholder emails must be refused — hashing one costs match quality
     and can never match.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.application.services.meta_capi_purchase_dispatcher import (
    fill_identity_from_customer,
    is_real_email,
    validate_conversion_value,
)

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _RecordingSession:
    """Minimal async-session stand-in that records the statement it ran.

    Deliberately not an ``AsyncMock``: an AsyncMock returns a truthy MagicMock
    from ``scalar_one_or_none()``, which would let a broken implementation
    "pass" while writing MagicMocks into ``user_data``.
    """

    def __init__(self, customer=None, raises: Exception | None = None):
        self._customer = customer
        self._raises = raises
        self.statements: list = []

    async def execute(self, statement):
        self.statements.append(statement)
        if self._raises is not None:
            raise self._raises
        return _Result(self._customer)


def _customer(**overrides):
    base = {
        "id": uuid4(),
        "email": "shopper@example.org",
        "phone": "+201001234567",
        "first_name": "Sara",
        "last_name": "Ali",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _order(*, customer_id=None, store_id=None):
    return SimpleNamespace(
        id=uuid4(),
        store_id=store_id if store_id is not None else uuid4(),
        customer_id=customer_id,
    )


# ---------------------------------------------------------------------------
# fill_identity_from_customer
# ---------------------------------------------------------------------------


class TestFillIdentityFromCustomer:
    async def test_fills_email_phone_and_name(self):
        session = _RecordingSession(customer=_customer())
        user_data: dict = {"email": None, "phone": None}

        await fill_identity_from_customer(
            session, user_data, _order(customer_id=uuid4())
        )

        assert user_data["email"] == "shopper@example.org"
        assert user_data["phone"] == "+201001234567"
        assert user_data["first_name"] == "Sara"
        assert user_data["last_name"] == "Ali"

    async def test_query_is_scoped_to_the_order_store(self):
        """Tenant isolation: both the customer id AND the store id must be
        in the WHERE clause. Filtering on customer_id alone would let a
        forged/stale customer id pull another merchant's shopper."""
        store_id = uuid4()
        customer_id = uuid4()
        session = _RecordingSession(customer=_customer())

        await fill_identity_from_customer(
            session,
            {"email": None},
            _order(customer_id=customer_id, store_id=store_id),
        )

        assert len(session.statements) == 1
        sql = str(session.statements[0].compile(compile_kwargs={"literal_binds": True}))
        # SQLAlchemy renders UUID literals dash-free.
        assert customer_id.hex in sql.replace("-", "")
        assert store_id.hex in sql.replace("-", "")
        assert "customers.store_id" in sql
        assert "customers.id" in sql

    async def test_guest_order_is_a_no_op_and_issues_no_query(self):
        session = _RecordingSession(customer=_customer())
        user_data: dict = {"email": None, "phone": None}

        await fill_identity_from_customer(session, user_data, _order(customer_id=None))

        assert session.statements == []
        assert user_data == {"email": None, "phone": None}

    async def test_short_circuits_when_email_and_phone_already_known(self):
        # The buyer already gave us both for THIS order — no reason to query.
        session = _RecordingSession(customer=_customer())
        user_data = {"email": "typed@example.org", "phone": "+201009999999"}

        await fill_identity_from_customer(
            session, user_data, _order(customer_id=uuid4())
        )

        assert session.statements == []
        assert user_data["email"] == "typed@example.org"

    async def test_never_overwrites_values_the_address_supplied(self):
        session = _RecordingSession(customer=_customer())
        user_data = {
            "email": None,
            "phone": "+201000000001",  # what the buyer typed at checkout
            "first_name": "Typed",
        }

        await fill_identity_from_customer(
            session, user_data, _order(customer_id=uuid4())
        )

        assert user_data["phone"] == "+201000000001"
        assert user_data["first_name"] == "Typed"
        assert user_data["email"] == "shopper@example.org"  # the blank was filled

    @pytest.mark.parametrize(
        "placeholder",
        [
            "guest-abc@noemail.numueg.app",
            "someone@guest.numueg.app",
            "x@placeholder.local",
            "buyer@example.com",
        ],
    )
    async def test_placeholder_emails_are_refused(self, placeholder: str):
        session = _RecordingSession(customer=_customer(email=placeholder))
        user_data: dict = {"email": None}

        await fill_identity_from_customer(
            session, user_data, _order(customer_id=uuid4())
        )

        assert user_data["email"] is None

    async def test_customer_not_found_is_a_no_op(self):
        session = _RecordingSession(customer=None)
        user_data: dict = {"email": None}

        await fill_identity_from_customer(
            session, user_data, _order(customer_id=uuid4())
        )

        assert user_data["email"] is None

    async def test_db_error_never_propagates(self):
        """CAPI must never break a payment webhook."""
        session = _RecordingSession(raises=RuntimeError("connection reset"))
        user_data: dict = {"email": None}

        await fill_identity_from_customer(
            session, user_data, _order(customer_id=uuid4())
        )

        assert user_data["email"] is None

    async def test_magicmock_session_is_survivable(self):
        # This is exactly how every existing dispatcher test calls it.
        user_data: dict = {"email": None}
        await fill_identity_from_customer(
            MagicMock(), user_data, _order(customer_id=uuid4())
        )
        assert user_data["email"] is None

    async def test_none_session_is_survivable(self):
        user_data: dict = {"email": None}
        await fill_identity_from_customer(None, user_data, _order(customer_id=uuid4()))
        assert user_data["email"] is None

    async def test_asyncmock_session_does_not_write_mock_objects(self):
        """Regression guard for the sloppy-double trap.

        An ``AsyncMock`` session returns a truthy MagicMock customer. If the
        implementation ever trusted that blindly, ``user_data`` would be
        poisoned with mock objects that later blow up inside ``hash_user_data``
        — in production code paths, only visible in tests that use AsyncMock.
        """
        session = AsyncMock()
        user_data: dict = {"email": None}

        await fill_identity_from_customer(
            session, user_data, _order(customer_id=uuid4())
        )

        for value in user_data.values():
            assert value is None or isinstance(value, str)

    @pytest.mark.parametrize("bad_id", ["not-a-uuid", 12345, object()])
    async def test_unparseable_ids_are_a_no_op(self, bad_id):
        session = _RecordingSession(customer=_customer())
        user_data: dict = {"email": None}

        await fill_identity_from_customer(
            session, user_data, _order(customer_id=bad_id)
        )

        assert session.statements == []
        assert user_data["email"] is None

    async def test_string_uuids_are_accepted(self):
        # Webhook payloads routinely hand us stringified ids.
        session = _RecordingSession(customer=_customer())
        order = _order(customer_id=str(uuid4()), store_id=str(uuid4()))
        user_data: dict = {"email": None}

        await fill_identity_from_customer(session, user_data, order)

        assert user_data["email"] == "shopper@example.org"

    async def test_blank_customer_fields_are_not_written(self):
        session = _RecordingSession(
            customer=_customer(phone=None, first_name="", last_name=None)
        )
        user_data: dict = {"email": None, "phone": None}

        await fill_identity_from_customer(
            session, user_data, _order(customer_id=uuid4())
        )

        assert user_data["phone"] is None
        assert "first_name" not in user_data or not user_data["first_name"]


# ---------------------------------------------------------------------------
# is_real_email
# ---------------------------------------------------------------------------


class TestIsRealEmail:
    @pytest.mark.parametrize(
        "value",
        ["a@b.co", "Yousef.Ali@Example.ORG", "  shopper@vionne.com.eg  "],
    )
    def test_real_addresses(self, value: str):
        assert is_real_email(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "   ",
            "no-at-sign",
            "@leading.com",
            "trailing@",
            "guest@noemail.numueg.app",
            "x@guest.local",
            "x@placeholder.io",
            "test@example.com",
            "TEST@EXAMPLE.COM",  # case-insensitive marker match
        ],
    )
    def test_rejected(self, value):
        assert is_real_email(value) is False


# ---------------------------------------------------------------------------
# validate_conversion_value — boundary analysis
# ---------------------------------------------------------------------------


class TestValidateConversionValue:
    def test_normal_purchase_passes(self):
        assert (
            validate_conversion_value({"value": 250.0, "currency": "EGP"}, "Purchase")
            is None
        )

    def test_zero_is_allowed(self):
        # 100%-discounted / fully gift-carded order is a real conversion.
        assert (
            validate_conversion_value({"value": 0, "currency": "EGP"}, "Purchase")
            is None
        )
        assert (
            validate_conversion_value({"value": 0.0, "currency": "EGP"}, "Purchase")
            is None
        )

    def test_negative_purchase_refused(self):
        assert validate_conversion_value(
            {"value": -0.01, "currency": "EGP"}, "Purchase"
        )

    def test_refund_must_be_negative_or_zero(self):
        assert (
            validate_conversion_value({"value": -250.0, "currency": "EGP"}, "Refund")
            is None
        )
        assert (
            validate_conversion_value({"value": 0, "currency": "EGP"}, "Refund") is None
        )
        assert validate_conversion_value({"value": 250.0, "currency": "EGP"}, "Refund")

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "abc",
            float("nan"),
            float("inf"),
            float("-inf"),
            True,
            False,
            [],
            {},
        ],
    )
    def test_unusable_values_refused(self, value):
        assert validate_conversion_value(
            {"value": value, "currency": "EGP"}, "Purchase"
        )

    def test_numeric_string_is_accepted(self):
        # Meta accepts a numeric string; refusing one would drop a real sale.
        assert (
            validate_conversion_value(
                {"value": "250.00", "currency": "EGP"}, "Purchase"
            )
            is None
        )

    @pytest.mark.parametrize(
        "currency", [None, "", "E", "EG", "EGPP", "EGP 250", "123", 250, ["EGP"]]
    )
    def test_bad_currency_refused(self, currency):
        assert validate_conversion_value(
            {"value": 250.0, "currency": currency}, "Purchase"
        )

    @pytest.mark.parametrize("currency", ["EGP", "egp", " EGP ", "SaR"])
    def test_three_letter_currency_accepted_any_case_or_padding(self, currency):
        assert (
            validate_conversion_value(
                {"value": 250.0, "currency": currency}, "Purchase"
            )
            is None
        )

    def test_reason_string_is_machine_taggable(self):
        # `_guard_conversion_payload` splits on ":" to build the Sentry tag.
        reason = validate_conversion_value({"value": -5, "currency": "EGP"}, "Purchase")
        assert reason.split(":", 1)[0] == "negative_value"
