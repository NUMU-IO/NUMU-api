# Adding a courier to the Trust Network

> Runbook for wiring a new courier (Aramex, SMSA, …) so its delivery outcomes
> feed the moat **correctly**. The wiring is documented here; the only
> courier-specific unknown is the webhook payload format (status field + values
> + auth header), which must come from the courier's webhook docs. Existing
> integrations: `api/v1/routes/webhooks/{bosta,jt,mylerz}.py`.

## The minimum requirement (the moat is courier-agnostic)

A new courier only **has** to update the `ShipmentModel.status` to a terminal
value (`delivered` / `returned`) and move the order through its lifecycle. The
**nightly reconciliation sweep** (`trust_reconciliation_tasks`) then backfills
the `delivery`/`rto` network events from terminal shipments — so even a courier
that doesn't write network events inline is covered, as long as the shipment
status is set. Wiring the network event inline is a latency optimization, not a
correctness requirement.

## Get these three things right (they bit the existing couriers)

1. **RTO is `return_to_origin`, NOT cancel.** On a carrier return for a SHIPPED
   order, call `order.return_to_origin(reason)` — the guarded `SHIPPED →
   RETURNED` transition. J&T and Mylerz originally set `status = CANCELLED` via a
   direct assignment that **bypassed the transition table** (illegal) and
   polluted the local cancellation-rate factor. Don't repeat it. Pre-ship
   returns (anomalous) may fall back to `cancel`.
2. **RTO fires for ANY payment method; `delivery` is COD-only.** A refused
   prepaid order is still a reliability signal. Mirror `bosta._record_network_
   event_from_order` (it now gates only the positive `delivery` event on COD).
3. **Idempotent network writes.** Pass `dedup_key=f"{store_id}:{order_id}:{event
   _type}"` to `write_network_event` (P1-2) so a webhook + the reconciliation
   sweep can't double-count. Same key the Bosta + manual paths use.

## Steps

1. **Add the webhook route** `api/v1/routes/webhooks/<courier>.py`, mirroring
   `mylerz.py` (the simplest). Mount it in the webhooks router.
2. **Verify the signature** — each courier signs differently (HMAC header, IP
   allowlist, shared secret). Use the per-store secret + global fallback +
   dev-permissive pattern the others use.
3. **Define the STATE_MAP** — `{<courier status string>: ShipmentStatus.<X>}`.
   ⚠️ **This is the fill-in-from-docs part.** Map the courier's terminal
   strings to `DELIVERED` / `RETURNED` / `FAILED` / `CANCELLED` and the in-flight
   ones to `PICKED_UP` / `IN_TRANSIT` / `OUT_FOR_DELIVERY`. Verify the exact
   strings against the courier's webhook payload — a wrong string silently drops
   the outcome. (Aramex e.g. uses "Delivered" / "Returned to Shipper"; SMSA
   differs — confirm both.)
4. **Handle DELIVERED** — `order.deliver()` (COD → mark paid), record the
   `delivery` network event (COD-only) with the dedup key.
5. **Handle RETURNED** — `order.return_to_origin()` (see #1 above), record the
   `rto` network event (any payment) with the dedup key.
6. **Update the shipment status** via the shipment repo so reconciliation +
   `courier_stats` see the terminal state.
7. **Tests** — mirror `tests/integration/test_courier_stats.py` for the
   normalization, and the network-event tests for the delivery/rto wiring.

## Checklist

- [ ] STATE_MAP verified against the courier's webhook docs (the only unknown)
- [ ] RTO → `return_to_origin` (guarded), never a direct `CANCELLED` set
- [ ] `delivery` COD-only, `rto` any payment method
- [ ] `dedup_key` passed to `write_network_event`
- [ ] Signature verification + dev-permissive fallback
- [ ] Shipment status updated (so reconciliation + courier_stats cover it)
- [ ] Tests for normalization + network wiring

With the STATE_MAP filled from the courier's docs, the rest is mechanical and
already correct by following the pattern.
