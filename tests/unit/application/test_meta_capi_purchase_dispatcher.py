"""Unit tests for ``enqueue_meta_capi_purchase`` — the shared CAPI
Purchase dispatcher used by every payment + courier webhook.

Covers:
  * Activation gate (``capi_enabled`` + ``pixel_id`` truth table)
  * Store-not-found short-circuits gracefully
  * Payload shape — what every webhook ships to Meta is identical
  * event_id = order.id (the dedup contract with the storefront fire)
  * Edge cases: missing shipping_address, empty line_items, missing
    paid_at fallback to now()

These are pure-Python unit tests — no DB, no Celery broker. The lazy
imports inside the dispatcher (StoreRepository, meta_capi_send_event)
are monkeypatched at their source modules.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from src.application.services.meta_capi_purchase_dispatcher import (
    enqueue_meta_capi_purchase,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PIXEL_ID = "1552896226251388"


def _make_store(*, capi_enabled: bool = True, pixel_id: str | None = PIXEL_ID):
    """A minimal store record matching what StoreRepository.get_by_id returns.

    Only ``settings`` is read by the dispatcher — everything else is
    irrelevant. We mirror the namespaced settings shape the merchant-hub
    panel writes (``settings.tracking.meta``).
    """
    return SimpleNamespace(
        id=uuid4(),
        settings={
            "tracking": {
                "meta": {
                    "pixel_id": pixel_id,
                    "pixel_enabled": True,
                    "capi_enabled": capi_enabled,
                }
            }
        },
    )


def _make_order(
    *,
    line_items: list | None = None,
    shipping_address: dict | None = None,
    total: int = 25_000,
    currency: str = "EGP",
    paid_at: datetime | None = None,
    customer_id=None,
    metadata: dict | None = None,
):
    """A minimal order record carrying just what the dispatcher reads.

    Uses SimpleNamespace so attribute access matches both ORM models
    and entity dataclasses without forcing a real DB row.
    """
    order_id = uuid4()
    return SimpleNamespace(
        id=order_id,
        store_id=uuid4(),
        line_items=line_items
        if line_items is not None
        else [
            {
                "product_id": "prod-1",
                "product_name": "Modal scarf",
                "quantity": 2,
                "unit_price": 12_500,  # cents
            },
            {
                "product_id": "prod-2",
                "product_name": "Plain hijab",
                "quantity": 1,
                "unit_price": 8_000,
            },
        ],
        shipping_address=shipping_address
        if shipping_address is not None
        else {
            "email": "buyer@example.com",
            "phone": "+201234567890",
            "first_name": "Sara",
            "last_name": "Ali",
            "city": "Cairo",
            "country": "EG",
            "postal_code": "11511",
        },
        total=total,
        currency=currency,
        paid_at=paid_at
        if paid_at is not None
        else datetime(2026, 5, 16, 10, 0, tzinfo=UTC),
        customer_id=customer_id,
        metadata=metadata
        if metadata is not None
        else {
            "ip_address": "192.0.2.42",
            "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X)",
        },
    )


@pytest.fixture
def patched_collaborators(monkeypatch):
    """Patch ``StoreRepository`` + ``meta_capi_send_event`` at their
    source modules so the lazy imports inside the dispatcher pick up
    our mocks. Returns ``(store_repo_cls, send_event_task)`` so each
    test can configure return values + assert call args.
    """
    store_repo_cls = MagicMock()
    store_repo_cls.return_value.get_by_id = AsyncMock(return_value=None)

    send_event_task = MagicMock()
    send_event_task.delay = MagicMock()

    import src.infrastructure.messaging.tasks.meta_capi as meta_capi_module
    import src.infrastructure.repositories.store_repository as store_repo_module

    monkeypatch.setattr(store_repo_module, "StoreRepository", store_repo_cls)
    monkeypatch.setattr(meta_capi_module, "meta_capi_send_event", send_event_task)

    return store_repo_cls, send_event_task


# ---------------------------------------------------------------------------
# Activation gate
# ---------------------------------------------------------------------------


class TestActivationGate:
    """Truth table covering when the dispatcher fires vs no-ops."""

    async def test_no_op_when_store_not_found(self, patched_collaborators):
        store_repo_cls, send_event_task = patched_collaborators
        store_repo_cls.return_value.get_by_id = AsyncMock(return_value=None)

        await enqueue_meta_capi_purchase(MagicMock(), _make_order())

        send_event_task.delay.assert_not_called()

    async def test_no_op_when_capi_disabled(self, patched_collaborators):
        store_repo_cls, send_event_task = patched_collaborators
        store_repo_cls.return_value.get_by_id = AsyncMock(
            return_value=_make_store(capi_enabled=False)
        )

        await enqueue_meta_capi_purchase(MagicMock(), _make_order())

        send_event_task.delay.assert_not_called()

    async def test_no_op_when_pixel_id_missing(self, patched_collaborators):
        store_repo_cls, send_event_task = patched_collaborators
        store_repo_cls.return_value.get_by_id = AsyncMock(
            return_value=_make_store(pixel_id=None)
        )

        await enqueue_meta_capi_purchase(MagicMock(), _make_order())

        send_event_task.delay.assert_not_called()

    async def test_no_op_when_settings_completely_missing(self, patched_collaborators):
        store_repo_cls, send_event_task = patched_collaborators
        store_repo_cls.return_value.get_by_id = AsyncMock(
            return_value=SimpleNamespace(id=uuid4(), settings=None)
        )

        await enqueue_meta_capi_purchase(MagicMock(), _make_order())

        send_event_task.delay.assert_not_called()

    async def test_no_op_when_tracking_namespace_missing(self, patched_collaborators):
        store_repo_cls, send_event_task = patched_collaborators
        # Legacy store with only the flat meta_pixel_id field — namespaced
        # tracking config absent. Dispatcher must NOT try to fall back to
        # the legacy field for CAPI (CAPI requires explicit opt-in via
        # capi_enabled).
        store_repo_cls.return_value.get_by_id = AsyncMock(
            return_value=SimpleNamespace(
                id=uuid4(),
                settings={"meta_pixel_id": "999999999999999"},
            )
        )

        await enqueue_meta_capi_purchase(MagicMock(), _make_order())

        send_event_task.delay.assert_not_called()

    async def test_fires_when_capi_enabled_and_pixel_present(
        self, patched_collaborators
    ):
        store_repo_cls, send_event_task = patched_collaborators
        store_repo_cls.return_value.get_by_id = AsyncMock(
            return_value=_make_store(capi_enabled=True)
        )

        await enqueue_meta_capi_purchase(MagicMock(), _make_order())

        send_event_task.delay.assert_called_once()


# ---------------------------------------------------------------------------
# Payload shape
# ---------------------------------------------------------------------------


class TestPayloadShape:
    """The contract every payment-method handler relies on. event_id =
    order.id is the dedup key against the storefront's Pixel-side fire."""

    async def test_event_id_is_order_id(self, patched_collaborators):
        store_repo_cls, send_event_task = patched_collaborators
        store_repo_cls.return_value.get_by_id = AsyncMock(return_value=_make_store())
        order = _make_order()

        await enqueue_meta_capi_purchase(MagicMock(), order)

        kwargs = send_event_task.delay.call_args.kwargs
        assert kwargs["event_id"] == str(order.id)
        assert kwargs["event_name"] == "Purchase"
        assert kwargs["pixel_id"] == PIXEL_ID
        assert kwargs["action_source"] == "website"

    async def test_user_data_carries_match_quality_pii(self, patched_collaborators):
        store_repo_cls, send_event_task = patched_collaborators
        store_repo_cls.return_value.get_by_id = AsyncMock(return_value=_make_store())

        await enqueue_meta_capi_purchase(MagicMock(), _make_order())

        ud = send_event_task.delay.call_args.kwargs["user_data"]
        assert ud["email"] == "buyer@example.com"
        assert ud["phone"] == "+201234567890"
        assert ud["first_name"] == "Sara"
        assert ud["last_name"] == "Ali"
        assert ud["city"] == "Cairo"
        assert ud["country_code"] == "EG"
        assert ud["zip"] == "11511"

    async def test_user_data_includes_ip_and_user_agent_from_metadata(
        self, patched_collaborators
    ):
        # Snapshot captured at checkout-time (see storefront/checkout.py)
        # is the customer's real IP+UA, not the webhook caller's. The
        # dispatcher reads it from order.metadata and forwards to CAPI
        # so Meta can use the two highest-signal match keys (per plan
        # §1.1) even when PII isn't available.
        store_repo_cls, send_event_task = patched_collaborators
        store_repo_cls.return_value.get_by_id = AsyncMock(return_value=_make_store())

        await enqueue_meta_capi_purchase(MagicMock(), _make_order())

        ud = send_event_task.delay.call_args.kwargs["user_data"]
        assert ud["ip"] == "192.0.2.42"
        assert ud["user_agent"].startswith("Mozilla/5.0 (iPhone")

    async def test_user_data_ip_user_agent_none_when_metadata_missing(
        self, patched_collaborators
    ):
        # Legacy orders (created before this PR) won't have ip_address
        # or user_agent in metadata. Dispatcher must degrade gracefully
        # — Meta drops None fields, no exception.
        store_repo_cls, send_event_task = patched_collaborators
        store_repo_cls.return_value.get_by_id = AsyncMock(return_value=_make_store())

        await enqueue_meta_capi_purchase(MagicMock(), _make_order(metadata={}))

        ud = send_event_task.delay.call_args.kwargs["user_data"]
        assert ud["ip"] is None
        assert ud["user_agent"] is None

    async def test_user_data_ip_user_agent_none_when_metadata_attr_missing(
        self, patched_collaborators
    ):
        # Some entity flavors don't carry a `metadata` attribute at all
        # (older domain dataclasses). getattr(order, "metadata", None)
        # must not raise.
        store_repo_cls, send_event_task = patched_collaborators
        store_repo_cls.return_value.get_by_id = AsyncMock(return_value=_make_store())
        order = _make_order()
        del order.metadata  # simulate missing attribute

        await enqueue_meta_capi_purchase(MagicMock(), order)

        ud = send_event_task.delay.call_args.kwargs["user_data"]
        assert ud["ip"] is None
        assert ud["user_agent"] is None

    async def test_country_code_falls_back_to_country_code_key(
        self, patched_collaborators
    ):
        # Some shipping providers store the field as `country_code`
        # already. Both keys must work for the same outcome.
        store_repo_cls, send_event_task = patched_collaborators
        store_repo_cls.return_value.get_by_id = AsyncMock(return_value=_make_store())
        order = _make_order(
            shipping_address={"country_code": "EG"},
        )

        await enqueue_meta_capi_purchase(MagicMock(), order)

        ud = send_event_task.delay.call_args.kwargs["user_data"]
        assert ud["country_code"] == "EG"

    async def test_custom_data_uses_display_units_not_cents(
        self, patched_collaborators
    ):
        # Meta expects `value` in display units (EGP, not piasters).
        # Backend stores cents — dispatcher must divide by 100.
        store_repo_cls, send_event_task = patched_collaborators
        store_repo_cls.return_value.get_by_id = AsyncMock(return_value=_make_store())
        order = _make_order(total=33_000)  # 330.00 EGP

        await enqueue_meta_capi_purchase(MagicMock(), order)

        cd = send_event_task.delay.call_args.kwargs["custom_data"]
        assert cd["value"] == 330.0
        assert cd["currency"] == "EGP"

    async def test_contents_array_carries_line_items(self, patched_collaborators):
        store_repo_cls, send_event_task = patched_collaborators
        store_repo_cls.return_value.get_by_id = AsyncMock(return_value=_make_store())

        await enqueue_meta_capi_purchase(MagicMock(), _make_order())

        cd = send_event_task.delay.call_args.kwargs["custom_data"]
        assert cd["content_ids"] == ["prod-1", "prod-2"]
        assert cd["content_type"] == "product"
        assert cd["num_items"] == 3  # 2 + 1
        assert len(cd["contents"]) == 2
        assert cd["contents"][0] == {
            "id": "prod-1",
            "quantity": 2,
            "item_price": 125.0,  # 12500 cents → 125.00 EGP
        }

    async def test_line_items_without_product_id_are_filtered(
        self, patched_collaborators
    ):
        # A malformed line item (gift wrap, manual fee, etc.) without a
        # product_id must not poison the `contents` array — Meta requires
        # every entry to have a non-empty id.
        store_repo_cls, send_event_task = patched_collaborators
        store_repo_cls.return_value.get_by_id = AsyncMock(return_value=_make_store())
        order = _make_order(
            line_items=[
                {"product_id": "prod-1", "quantity": 1, "unit_price": 10_000},
                {"product_id": "", "quantity": 1, "unit_price": 500},  # filtered
                {"quantity": 1, "unit_price": 200},  # filtered
            ],
        )

        await enqueue_meta_capi_purchase(MagicMock(), order)

        cd = send_event_task.delay.call_args.kwargs["custom_data"]
        assert cd["content_ids"] == ["prod-1"]
        assert len(cd["contents"]) == 1


# ---------------------------------------------------------------------------
# Edge cases / defensive behavior
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Real-world malformed orders shouldn't blow up the webhook."""

    async def test_empty_line_items(self, patched_collaborators):
        # Possible for orders that are pure shipping refunds / gift cards —
        # dispatcher should still fire (just with empty contents).
        store_repo_cls, send_event_task = patched_collaborators
        store_repo_cls.return_value.get_by_id = AsyncMock(return_value=_make_store())

        await enqueue_meta_capi_purchase(MagicMock(), _make_order(line_items=[]))

        send_event_task.delay.assert_called_once()
        cd = send_event_task.delay.call_args.kwargs["custom_data"]
        assert cd["contents"] == []
        assert cd["num_items"] == 0

    async def test_missing_shipping_address(self, patched_collaborators):
        # COD orders sometimes land here before the address is finalized —
        # we still want the Purchase event to fire (Meta will downgrade
        # match quality but the conversion is real).
        store_repo_cls, send_event_task = patched_collaborators
        store_repo_cls.return_value.get_by_id = AsyncMock(return_value=_make_store())

        await enqueue_meta_capi_purchase(MagicMock(), _make_order(shipping_address={}))

        send_event_task.delay.assert_called_once()
        ud = send_event_task.delay.call_args.kwargs["user_data"]
        assert ud["email"] is None
        assert ud["phone"] is None

    async def test_paid_at_falls_back_to_now_when_missing(
        self, patched_collaborators, monkeypatch
    ):
        # If paid_at is None (legacy row, retroactive sweep), dispatcher
        # uses datetime.now(UTC) so the event still has a timestamp.
        store_repo_cls, send_event_task = patched_collaborators
        store_repo_cls.return_value.get_by_id = AsyncMock(return_value=_make_store())
        # Build the order directly — the _make_order helper's
        # "None means use default" sentinel logic shadows our explicit None.
        order = _make_order()
        order.paid_at = None  # type: ignore[assignment]

        # Freeze the clock so the assertion is deterministic.
        frozen = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen

        import src.application.services.meta_capi_purchase_dispatcher as mod

        monkeypatch.setattr(mod, "datetime", FrozenDatetime)

        await enqueue_meta_capi_purchase(MagicMock(), order)

        kwargs = send_event_task.delay.call_args.kwargs
        assert kwargs["event_time"] == int(frozen.timestamp())

    async def test_currency_falls_back_to_egp(self, patched_collaborators):
        # Egyptian merchants who never set a currency get EGP — never None.
        store_repo_cls, send_event_task = patched_collaborators
        store_repo_cls.return_value.get_by_id = AsyncMock(return_value=_make_store())

        await enqueue_meta_capi_purchase(MagicMock(), _make_order(currency=None))

        cd = send_event_task.delay.call_args.kwargs["custom_data"]
        assert cd["currency"] == "EGP"

    async def test_string_store_id_normalizes_to_uuid(self, patched_collaborators):
        # ORM models hand us UUID; entity dataclasses sometimes hand us
        # str. The dispatcher must normalize either to UUID before
        # querying the repository (or get_by_id will throw a type error).
        store_repo_cls, _ = patched_collaborators
        store = _make_store()
        store_repo_cls.return_value.get_by_id = AsyncMock(return_value=store)
        order = _make_order()
        order.store_id = str(order.store_id)  # type: ignore[assignment]

        await enqueue_meta_capi_purchase(MagicMock(), order)

        # Verify the repository was called with a UUID, not a str.
        called_arg = store_repo_cls.return_value.get_by_id.call_args.args[0]
        assert isinstance(called_arg, UUID)

    async def test_customer_id_propagates_when_present(self, patched_collaborators):
        store_repo_cls, send_event_task = patched_collaborators
        store_repo_cls.return_value.get_by_id = AsyncMock(return_value=_make_store())
        cust_id = uuid4()

        await enqueue_meta_capi_purchase(MagicMock(), _make_order(customer_id=cust_id))

        ud = send_event_task.delay.call_args.kwargs["user_data"]
        assert ud["customer_id"] == str(cust_id)

    async def test_customer_id_is_none_for_guest_orders(self, patched_collaborators):
        store_repo_cls, send_event_task = patched_collaborators
        store_repo_cls.return_value.get_by_id = AsyncMock(return_value=_make_store())

        await enqueue_meta_capi_purchase(MagicMock(), _make_order(customer_id=None))

        ud = send_event_task.delay.call_args.kwargs["user_data"]
        assert ud["customer_id"] is None
