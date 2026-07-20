# Quickstart: COD Autopilot (004-cod-autopilot)

Local end-to-end verification without waiting for Meta template approval (mock the send layer; only step 7 needs real approved templates).

## 1. Migrate + seed

```bash
alembic upgrade head          # creates whatsapp_delivery_checks, whatsapp_ship_digests (+RLS), seeds the two templates as PENDING
```

For local sends without Meta approval, force-approve the seeded rows in your dev DB:

```sql
UPDATE whatsapp_templates SET status='APPROVED'
WHERE name IN ('cod_ship_digest_v1','order_delivery_check_v1');
```

## 2. Enable Autopilot on a test store

```bash
curl -X PATCH localhost:8000/api/v1/stores/$STORE_ID/settings/cod-autopilot \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"enabled": true, "digest_hour": <current hour in Africa/Cairo>, "delivery_check_delay_days": 1}'
```

Store must have `contact_phone` set (digest recipient) — response shows `digest_deliverable: true`.

## 3. Create eligible orders

Create 2–3 **COD** orders and move them to `confirmed` (dashboard or existing status endpoint). Ensure no Bosta shipment is attached (that excludes them, FR-001).

## 4. Digest flow

```bash
celery -A src.infrastructure.messaging.celery_app call tasks.cod_autopilot_send_digests
```

Expect: one `whatsapp_ship_digests` row (UNIQUE store+date), outbound `cod_ship_digest_v1` in `message_logs` with `event_tag=digest:<date>`. Re-running the task sends nothing (idempotent).

Simulate the merchant's **All shipped** tap (Meta inbound shape):

```bash
curl -X POST localhost:8000/api/v1/webhooks/whatsapp/callback -H "Content-Type: application/json" \
  -d '{"entry":[{"changes":[{"field":"messages","value":{"messages":[{"type":"button","from":"<store contact_phone digits>","button":{"payload":"shipall:<subdomain>/<digest_id>","text":"All shipped"}}]}}]}]}'
```

(Disable/stub HMAC verification locally, as existing webhook tests do.) Expect: all listed orders → `shipped`, timeline entries with `source: merchant_digest`, digest row `processed_at` set. Replay the same POST → ack only, no re-transition.

Partial day: reset with fresh orders next local day, reply as free text `{"type":"text","text":{"body":"except 2"},"from":"<merchant digits>"}` → all but item 2 ship. Send garbage text → help reply, zero status changes.

## 5. Delivery-check flow

```bash
celery -A ... call tasks.cod_autopilot_delivery_checks
```

With `delivery_check_delay_days: 1`, back-date `orders.shipped_at` by 1 day to make checks due:

```sql
UPDATE orders SET shipped_at = shipped_at - interval '1 day' WHERE id = '<order_id>';
```

Expect: `whatsapp_delivery_checks` row, attempts=1, outbound `order_delivery_check_v1` logged with `event_tag=dlvcheck:<order_id>:1`.

Tap **Received** (`"payload":"dlvyes:<sub>/<order_id>"`, from = customer phone digits): order → `delivered`, `payment_status=paid` (COD auto-mark), check outcome `delivered_confirmed`, timeline `source: customer_confirmed`, `network_reputation` delivery counter incremented for the phone hash.

Tap **Refused** (`dlvref:`): no status change; check outcome `exception`; order appears in:

```bash
curl localhost:8000/api/v1/stores/$STORE_ID/orders/autopilot-exceptions -H "Authorization: Bearer $TOKEN"
```

## 6. Assumed-delivered fallback

Exhaust attempts (run the check task 3× with back-dated `next_attempt_at`), then back-date `assumed_delivered_due_at`, then:

```bash
celery -A ... call tasks.cod_autopilot_assumed_delivered
```

Expect: order → `delivered` with timeline `source: assumed_delivered`, `metadata.autopilot_assumed_delivered_at` set, `metadata.network_delivery_skipped = "assumed_delivered"`, and **no** new `network_reputation` delivery increment (R-06). An order manually moved to `returned` before the sweep is untouched (`superseded`).

## 7. Real-channel smoke test (staging)

Run `python scripts/submit_platform_whatsapp_templates.py` → wait for Meta APPROVED (webhook `message_template_status_update` flips the DB rows). Then repeat steps 2–6 against the test env with a real merchant + customer phone. Verify the no-emoji bodies rendered correctly in both `en` and `ar`.

## 8. Test suite anchors

- `tests/unit/tasks/test_cod_autopilot_tasks.py` (clone style of `test_cod_auto_rto_task.py`)
- `tests/unit/services/test_autopilot_reply_parsing.py` (exceptions grammar, R-04 — including Arabic-Indic digits and ambiguity no-ops)
- `tests/unit/core/whatsapp/test_send_guard.py` additions (new pref keys)
- `tests/integration/whatsapp/test_autopilot_webhook_actions.py` (button payloads, idempotency, phone matching)
- `tests/unit/use_cases/test_update_order_status_network.py` additions (source-gated delivery event, R-06)
