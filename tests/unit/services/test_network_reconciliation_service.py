"""Unit tests for the network-reconciliation decision function (P1-1)."""

from __future__ import annotations

import pytest

from src.application.services.network_reconciliation_service import (
    network_event_to_backfill,
)


class TestNetworkEventToBackfill:
    def test_delivered_cod_not_yet_recorded_backfills_delivery(self):
        assert (
            network_event_to_backfill(
                shipment_status="delivered",
                payment_method="cod",
                delivery_already_recorded=False,
                rto_already_recorded=False,
            )
            == "delivery"
        )

    def test_delivered_cod_already_recorded_is_skipped(self):
        assert (
            network_event_to_backfill(
                shipment_status="delivered",
                payment_method="cod",
                delivery_already_recorded=True,
                rto_already_recorded=False,
            )
            is None
        )

    def test_delivered_prepaid_does_not_backfill_delivery(self):
        """The positive ``delivery`` signal is COD-only."""
        assert (
            network_event_to_backfill(
                shipment_status="delivered",
                payment_method="paymob_card",
                delivery_already_recorded=False,
                rto_already_recorded=False,
            )
            is None
        )

    @pytest.mark.parametrize("status", ["returned", "rto", "RETURNED", " Returned "])
    def test_returned_any_payment_backfills_rto(self, status):
        """P0-4 — rto fires for any payment method, including prepaid."""
        assert (
            network_event_to_backfill(
                shipment_status=status,
                payment_method="paymob_card",
                delivery_already_recorded=False,
                rto_already_recorded=False,
            )
            == "rto"
        )

    def test_returned_already_recorded_is_skipped(self):
        assert (
            network_event_to_backfill(
                shipment_status="returned",
                payment_method="cod",
                delivery_already_recorded=False,
                rto_already_recorded=True,
            )
            is None
        )

    @pytest.mark.parametrize(
        "status", ["shipped", "in_transit", "out_for_delivery", "pending", None, ""]
    )
    def test_non_terminal_status_backfills_nothing(self, status):
        assert (
            network_event_to_backfill(
                shipment_status=status,
                payment_method="cod",
                delivery_already_recorded=False,
                rto_already_recorded=False,
            )
            is None
        )

    def test_status_and_payment_are_case_and_whitespace_insensitive(self):
        assert (
            network_event_to_backfill(
                shipment_status=" Delivered ",
                payment_method="COD",
                delivery_already_recorded=False,
                rto_already_recorded=False,
            )
            == "delivery"
        )
