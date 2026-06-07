"""Cross-merchant network-reputation reconciliation (P1-1).

Courier webhooks and the manual order-status path each try to record a
``delivery`` / ``rto`` event into ``network_reputation`` when a shipment
reaches a terminal outcome. Any one of those writes can be missed:

  * a dropped / lost courier webhook;
  * a courier integration that never wired network recording at all — J&T
    and Mylerz only update the shipment + order, they do NOT call
    ``write_network_event``;
  * a transient DB/Redis error on the original write.

A single missed write permanently desyncs the moat for that phone — the
customer's RTO never lowers their network score, the opposite of the
product's promise. This module backfills those misses: a nightly sweep
scans recent terminal shipments and, for any whose order is still missing
the idempotency flag, replays the event.

The decision of *what* (if anything) to backfill is a pure function so it
can be unit-tested without a database; the Celery task in
``trust_reconciliation_tasks`` is a thin DB wrapper around it.
"""

from __future__ import annotations

# Shipment.status values that represent a terminal delivery outcome.
# Mirrors ``courier_stats_service`` so the two stay aligned.
DELIVERED_STATUSES = frozenset({"delivered"})
RETURNED_STATUSES = frozenset({"returned", "rto"})


def network_event_to_backfill(
    *,
    shipment_status: str | None,
    payment_method: str | None,
    delivery_already_recorded: bool,
    rto_already_recorded: bool,
) -> str | None:
    """Return the network ``event_type`` missing for a terminal shipment.

    Returns ``"delivery"``, ``"rto"``, or ``None`` (nothing to do).

    Rules — kept identical to the live write paths (P0-4) so a backfilled
    event is indistinguishable from one the webhook should have written:

      * a delivered shipment contributes the positive ``delivery`` signal
        ONLY for COD orders (cash-collected is the meaningful event);
      * a returned shipment contributes the negative ``rto`` signal for
        ANY payment method (a refused delivery is a reliability signal
        regardless of how it was paid);
      * anything already stamped with its idempotency flag is skipped, so
        the sweep only ever fills genuine gaps.
    """
    status = (shipment_status or "").strip().lower()

    if status in DELIVERED_STATUSES:
        if delivery_already_recorded:
            return None
        if (payment_method or "").strip().lower() != "cod":
            return None
        return "delivery"

    if status in RETURNED_STATUSES:
        if rto_already_recorded:
            return None
        return "rto"

    return None
