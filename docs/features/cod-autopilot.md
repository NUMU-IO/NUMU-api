# COD Autopilot (004-cod-autopilot)

Automates the `shipped` and `delivered + paid` hops of the COD order
lifecycle for merchants who ship via **external couriers with no API**,
using WhatsApp taps from the two humans who hold the truth — the merchant
(knows when packages left) and the customer (knows when they arrived) —
plus timer fallbacks so 100% of orders reach a terminal state.

Spec/plan/tasks: `specs/004-cod-autopilot/`.

## The loop

| Hop | Who acts | How |
|---|---|---|
| pending → confirmed | customer | existing tap-to-confirm (backend-031), unchanged |
| confirmed → shipped | merchant, 1 tap/day | daily WhatsApp **ship digest** (`cod_ship_digest_v1`): numbered order list + **All shipped** button; partial days via a text reply `except 2, 5` (strict grammar — anything ambiguous is a no-op + help reply) |
| shipped → delivered+paid | customer | **delivery check** (`order_delivery_check_v1`) N days after shipped: Received / Not yet / Refused buttons. Received → delivered, COD auto-marked paid |
| silent orders | timers | up to 2 re-pings, then **assumed delivered** after the store window; Refused / exhausted rows land in the merchant **exception queue** (`GET /orders/autopilot-exceptions`); the nightly auto-RTO sweep (03:00) remains the negative backstop and always wins (fallback runs 03:30) |

## Trust-network integrity (non-negotiable)

- `customer_confirmed` deliveries fire the network `delivery` event at
  full weight — same as a manual merchant mark.
- `assumed_delivered` closures are **excluded** from network delivery
  events entirely (stamped `order.metadata.network_delivery_skipped`).
  A timer must never fabricate a positive reputation signal.
- Every automated transition records its `source` in the order's
  `status_history` (`customer_confirmed` / `merchant_digest` /
  `assumed_delivered`).

## Configuration

`store.settings.cod_autopilot` via `GET/PATCH /stores/{id}/settings/cod-autopilot`
(off by default):

```json
{
  "enabled": false,
  "digest_hour": 18,              // 0-23, store-local (market timezone)
  "delivery_check_delay_days": 3, // 1-7
  "delivery_check_retry_days": 2, // 1-7
  "delivery_check_max_attempts": 3,
  "assumed_delivered_days": 10    // 5-30, from shipped
}
```

The digest goes to `store.contact_phone` (response surfaces
`digest_deliverable`). Disabling stops all sends/closures immediately
without touching order state.

## Eligibility

COD orders only; orders with a carrier shipment attached (Bosta/Mylerz/
J&T) are excluded — their webhooks already automate statuses. Delivery
checks are only created for orders shipped within the last 30 days
(protects against enabling Autopilot over a stale backlog).

## Ops runbook

- **Beat tasks**: `tasks.cod_autopilot_send_digests` (hourly :05, sends
  at each store's local `digest_hour`), `tasks.cod_autopilot_delivery_checks`
  (hourly :20), `tasks.cod_autopilot_assumed_delivered` (daily 03:30 UTC —
  do NOT move before the 03:00 RTO sweep).
- **Rollout switch**: templates are seeded PENDING; the send guard blocks
  everything until Meta approval flips `whatsapp_templates` rows to
  APPROVED (`scripts/submit_platform_whatsapp_templates.py` submits;
  status webhook/poller flips). No approval → Autopilot enabled does
  nothing except fallback closures.
- **Stuck digest**: check `whatsapp_ship_digests` for the store/date —
  `processed_at` set means consumed; replies after `expires_at` (48h) are
  ignored by design.
- **Stuck delivery check**: `whatsapp_delivery_checks.outcome` state
  machine: `pending → delivered_confirmed | exception |
  response_exhausted → assumed_delivered`, `superseded` when any other
  path closed the order. `next_attempt_at NULL` + `pending` means
  awaiting the response window before exhaustion.
- **GDPR**: delivery-check rows join the customer export
  (`extras.delivery_checks`) and are phone-blanked on account deletion.
