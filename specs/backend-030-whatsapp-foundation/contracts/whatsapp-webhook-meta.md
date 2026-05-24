# Meta WhatsApp Webhook — Payload Contract (incoming)

Existing route: `POST /webhooks/whatsapp/callback` (kept unchanged per FR-040).

The route already handles `messages` and `statuses` fields. Phase 1 adds **two** behaviours within the existing route:

## 1. STOP keyword detection on `messages` field (inbound from customer)

When a customer message arrives, the existing handler logs it to `message_log` and creates/updates `whatsapp_conversations`. **New step**: after that, run the STOP-keyword detector on the message text. If detected, flip the active opt-in row to opted-out and send an acknowledgement reply.

### Detection rules
- Apply only to `messages[].type == "text"`.
- Extract `messages[].text.body`, normalize: Unicode NFKC, strip Arabic tashkeel (`ً-ْ`), strip leading/trailing whitespace.
- Take the first whitespace-delimited token (or the whole string if no whitespace).
- Lowercase it (Latin) — Arabic case is already canonical.
- Match against the set `{"stop", "unsubscribe", "إلغاء", "الغاء"}`.
- If matched: opt-out flow runs (FR-009, FR-010).

### Acknowledgement reply
- A short text message: "You have been unsubscribed from WhatsApp messages from {store_name}. Reply START to resubscribe." (English + Arabic variants seeded as system templates `optout_confirmation_en` and `optout_confirmation_ar`).
- Sent via the same `WhatsAppMessagingService.send_text_message` path, **bypassing the opt-in guard for this specific message only** (the customer just messaged in, the 24h window is open, and they explicitly asked to opt out — the ack confirms it).

## 2. `message_template_status_update` field handler (new)

Meta delivers template approval-status changes to the same webhook URL under a different `field` value. Existing route must inspect `entry[].changes[].field` and route accordingly.

### Routing logic
```
if change.field == "messages": → existing handler (inbound message + statuses)
elif change.field == "message_template_status_update": → new handler
else: log unhandled-field warning, return 200
```

### Payload (relevant fields)
```json
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "<waba_id>",
    "time": 1234567890,
    "changes": [{
      "field": "message_template_status_update",
      "value": {
        "event": "APPROVED|REJECTED|FLAGGED|PAUSED|DISABLED",
        "message_template_id": "<meta_template_id>",
        "message_template_name": "string",
        "message_template_language": "en|ar|...",
        "reason": "ABUSIVE_CONTENT|INVALID_FORMAT|null",
        "disable_info": { "disable_date": "string" }
      }
    }]
  }]
}
```

### Handler behaviour
1. Resolve `store_id` from `waba_id` via `service_credentials` (BYO) or platform mapping. If no match, log warning and return 200.
2. Find the local `whatsapp_templates` row by `(store_id, meta_template_id)` OR `(store_id, name, language)`.
3. Update `status`, `rejection_reason`, and the appropriate timestamp (`approved_at` on APPROVED, etc.).
4. Return 200.

## Signature verification

Both new behaviours use the existing `app_secret` verification (HMAC-SHA256 of raw body). The handler resolves the **single correct** secret deterministically before verifying — no fallback chain (which would create an oracle and weaken the auth guarantee).

### Resolution algorithm

1. Read `entry[].id` from the payload (this is the unsigned `waba_id` Meta uses to route to a NUMU-managed app).
2. Resolve the matching store:
   - If `entry[].id == settings.whatsapp_platform_waba_id` → this is a platform-managed event. Use `settings.whatsapp_app_secret` as the verification key. The handler later resolves which platform-managed store(s) the event applies to from the payload (e.g., `messages[].from` for inbound; `value.message_template_name` + WABA for template-status).
   - Else look up `service_credentials` rows where `service_name = 'WHATSAPP_BUSINESS'`, `is_active = true`, and the encrypted credentials' decrypted `waba_id` equals `entry[].id`. If exactly one match → use that store's BYO `app_secret`. If multiple matches → log a structured error (data inconsistency) and reject with 401. If zero matches → return 200 with no-op and log a structured warning (avoids Meta retry storms for stale subscriptions); do NOT try any other secret.
3. Compute HMAC-SHA256 of the raw request body using the resolved secret. Compare to `X-Hub-Signature-256` in constant time.
4. If verification fails → return 401. Do NOT try any other secret. Do NOT proceed to any handler.

### Why no fallback

Trying multiple secrets sequentially and accepting whichever verifies turns the webhook into an oracle (an attacker can probe with crafted payloads and observe response variance to enumerate which WABAs have registered BYO credentials). It also weakens the auth guarantee: any of several keys can authenticate the payload, increasing the blast radius of a single compromised key. The lookup-then-verify pattern above keeps a 1:1 correspondence between payload origin and verification key.

### Edge cases

- `entry[].id` missing or malformed → return 200 with no-op + log; do not attempt verification.
- Verification fails after resolution → return 401 (Meta will retry, then eventually drop). This is preferable to silent acceptance.
- BYO credentials rotated mid-flight (decrypted `waba_id` no longer matches any stored secret because the merchant changed apps) → resolves to zero matches → no-op return 200, log a structured `whatsapp_webhook_no_credential_match` event for operator alerting.

## Backward compatibility

- Route path: `/webhooks/whatsapp/callback` (unchanged, FR-040).
- Existing message + status handling: unchanged.
- New behaviours: additive only.
