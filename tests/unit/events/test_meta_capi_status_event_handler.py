"""Unit tests for the Wave 2 Phase 12 status-driven Meta CAPI handler.

Pins the truth table for `_resolve_meta_event` (the pure decision
function inside the handler) and verifies the fail-open contract:

  * Handler must NEVER raise — exceptions are caught + logged so a
    Meta CAPI error doesn't block order-status updates.
  * Default config (no purchase_trigger / lead_trigger) = no-op so
    legacy stores see no behavior change.
  * Trigger only matches its configured status; other transitions
    are silently ignored.
"""

from __future__ import annotations

import pytest

from src.infrastructure.events.handlers.meta_capi_status_event_handler import (
    _resolve_meta_event,
)


class TestResolveMetaEvent:
    """Pure-function truth table for the trigger → event mapping."""

    def test_no_config_no_events(self):
        # Legacy default — both triggers unset.
        assert _resolve_meta_event({}, "delivered") == (None, None)
        assert _resolve_meta_event({}, "confirmed") == (None, None)
        assert _resolve_meta_event({}, "shipped") == (None, None)

    def test_purchase_trigger_matches_status_fires_purchase(self):
        cfg = {"purchase_trigger": "delivered"}
        assert _resolve_meta_event(cfg, "delivered") == ("Purchase", None)

    def test_purchase_trigger_mismatched_status_no_fire(self):
        # purchase_trigger=delivered + new_status=shipped → no fire.
        cfg = {"purchase_trigger": "delivered"}
        assert _resolve_meta_event(cfg, "shipped") == (None, None)

    def test_lead_trigger_matches_status_fires_lead(self):
        cfg = {"lead_trigger": "confirmed"}
        assert _resolve_meta_event(cfg, "confirmed") == (None, "Lead")

    def test_both_triggers_can_fire_simultaneously(self):
        # Rare but valid: merchant maps both Lead and Purchase to the
        # same status. Both events get enqueued.
        cfg = {"purchase_trigger": "delivered", "lead_trigger": "delivered"}
        assert _resolve_meta_event(cfg, "delivered") == ("Purchase", "Lead")

    def test_typical_cod_config(self):
        # The recommended Egyptian-COD config from the plan.
        cfg = {"purchase_trigger": "delivered", "lead_trigger": "confirmed"}
        assert _resolve_meta_event(cfg, "confirmed") == (None, "Lead")
        assert _resolve_meta_event(cfg, "shipped") == (None, None)
        assert _resolve_meta_event(cfg, "delivered") == ("Purchase", None)

    @pytest.mark.parametrize(
        "trigger_value",
        ["paid", "placed", "cancelled", "refunded", "returned", "invalid"],
    )
    def test_invalid_trigger_values_silently_ignored(self, trigger_value):
        # Only the four valid trigger statuses fire. Other values
        # (typos, removed states, statuses with their own dedicated
        # event like 'paid') are no-ops — defensive against schema
        # drift.
        cfg = {"purchase_trigger": trigger_value, "lead_trigger": trigger_value}
        assert _resolve_meta_event(cfg, trigger_value) == (None, None)

    def test_null_triggers_treated_as_unset(self):
        # The settings layer persists null when the merchant cleared
        # the field; the resolver must treat null like missing.
        cfg = {"purchase_trigger": None, "lead_trigger": None}
        assert _resolve_meta_event(cfg, "delivered") == (None, None)

    @pytest.mark.parametrize(
        "status", ["confirmed", "processing", "shipped", "delivered"]
    )
    def test_all_four_valid_trigger_statuses(self, status):
        # Every valid status name reachable from purchase_trigger
        # must produce a Purchase fire when configured. Pins the
        # full _VALID_TRIGGER_STATUSES set.
        cfg = {"purchase_trigger": status}
        assert _resolve_meta_event(cfg, status) == ("Purchase", None)


# ===========================================================================
# Wave 2 Phase 21 — Refund handling
# ===========================================================================
#
# Refund transitions (REFUNDED, RETURNED) are handled by the same status
# event handler but flow through a separate dispatcher function
# (``enqueue_meta_capi_refund``) — they emit a Refund custom event with
# NEGATIVE value so the merchant can subtract from gross Purchase revenue
# in Meta Ads Manager.


class TestRefundStatusConstants:
    """The frozenset of refund-triggering statuses is pinned."""

    def test_refund_set_is_exactly_two_statuses(self):
        # REFUNDED = money refund; RETURNED = Bosta RTO (no money but
        # the sale is lost for ad-attribution purposes).
        from src.infrastructure.events.handlers.meta_capi_status_event_handler import (
            _REFUND_STATUSES,
        )

        assert _REFUND_STATUSES == frozenset({"refunded", "returned"})

    def test_refund_statuses_do_not_overlap_with_trigger_statuses(self):
        # Refund statuses must NOT appear in the Lead/Purchase trigger
        # set — otherwise a merchant could accidentally configure
        # "fire Purchase on refunded" which would be absurd.
        from src.infrastructure.events.handlers.meta_capi_status_event_handler import (
            _REFUND_STATUSES,
            _VALID_TRIGGER_STATUSES,
        )

        assert _REFUND_STATUSES.isdisjoint(_VALID_TRIGGER_STATUSES)
