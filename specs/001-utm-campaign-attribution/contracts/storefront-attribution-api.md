# Contract — Storefront Attribution Wire

Two existing storefront endpoints are extended to carry attribution. Plus one client-side cookie shape that everything keys off.

---

## Client cookie — `numu_attribution`

Set by the storefront, read by the storefront on every page load, also read server-side by the funnel-event endpoint via the `Cookie:` header.

### Attributes

- **Name**: `numu_attribution`
- **Value**: URL-encoded JSON (see schema below)
- **Domain**: defaults to current host (no `Domain=` directive). A merchant on a custom domain gets the cookie scoped to that domain; subdomain stores get it scoped per-subdomain. This matches the merchant's mental model — each store is its own attribution silo.
- **Path**: `/`
- **Max-Age**: 7776000 (90 days)
- **SameSite**: `Lax`
- **Secure**: `true` when served over HTTPS (i.e. always in prod/staging/test; not in localhost dev)
- **HttpOnly**: `false` — the storefront's React app reads it to attach to the checkout body

### Payload schema (v1)

```json
{
  "v": 1,
  "first_touch": {
    "ts": "2026-05-21T14:33:00Z",
    "utm_source": "facebook",
    "utm_medium": "social",
    "utm_campaign": "eid-sale-2026-AB7K",
    "utm_term": null,
    "utm_content": null,
    "gclid": null,
    "fbclid": "PAQ...",
    "referrer": "https://www.facebook.com/",
    "landing_path": "/product/abc-123"
  },
  "last_touch": { /* same shape */ },
  "session_id": "01HX2M..."
}
```

### Update rules

On every page load, the storefront hook (`useAttribution()`):

1. Reads `window.location.search` for any of: `utm_source`, `utm_medium`, `utm_campaign`, `utm_term`, `utm_content`, `gclid`, `fbclid`.
2. If at least one is present, treat this as a new touch. Compose a touch object: stamp `ts` to now (UTC ISO-8601), `referrer` to `document.referrer || null`, `landing_path` to `window.location.pathname`.
3. Read the existing cookie (decode JSON; on parse error, treat as absent).
4. If cookie is absent: write `{ v: 1, first_touch: <new>, last_touch: <new>, session_id: ulid() }`.
5. If cookie exists: overwrite `last_touch` with `<new>`. Leave `first_touch` and `session_id` unchanged.
6. If no UTM params in URL: do not write. Existing cookie (if any) is preserved.

The hook runs on the client only (no SSR-side cookie write — it would conflict with ISR).

---

## Extended: `POST /storefront/store/{store_id}/track`

Existing endpoint (`tracking.py:177`). Schema extension only — backwards compatible.

### Request body — new fields

```json
{
  // ...existing fields (path, fingerprint, referrer, step, step_data, event_id, etc.)...

  "attribution": {            // NEW — optional. When omitted, server falls back to Cookie: header parse.
    "v": 1,
    "first_touch": { ... },
    "last_touch": { ... },
    "session_id": "01HX..."
  }
}
```

### Server behavior

1. If `attribution` is in the body, use it.
2. Else, attempt to parse `Cookie: numu_attribution=...` from the request headers.
3. On either path, validate via Pydantic; on schema mismatch, log + ignore (do not 400).
4. Pass parsed `last_touch` fields into `_emit_funnel_event` (via new kwargs).
5. Resolve `campaign_id` from `attribution.last_touch.utm_campaign` (split off the trailing `-<short_code>` and look up in `marketing_campaigns`).

### Response

Unchanged: `204 No Content`.

---

## Extended: `POST /storefront/store/{store_id}/track-event`

Same extension as `/track`. The generic event tracker also accepts and persists attribution.

---

## Extended: `POST /storefront/store/{store_id}/checkout`

Existing endpoint. Schema extension on `CheckoutRequest`.

### Request body — new fields

```json
{
  // ...all existing fields...

  "utm_term": "linen-collection",        // NEW (mirrors existing utm_source/medium/campaign)
  "utm_content": "eid-banner-v2",        // NEW

  "attribution": {                       // NEW — preferred over flat utm_* when both present.
    "v": 1,
    "first_touch": { ... },
    "last_touch": { ... },
    "session_id": "01HX..."
  }
}
```

### Server behavior

1. Accept either the flat `utm_*` fields (legacy clients) OR the structured `attribution` object (preferred). When both present, `attribution.last_touch` wins for the raw UTM columns.
2. Resolve `campaign_id` from `attribution.last_touch.utm_campaign` (or fallback `utm_campaign` if no attribution payload).
3. Stamp `orders.utm_source`, `utm_medium`, `utm_campaign`, `utm_term`, `utm_content`, `campaign_id`, `attribution`, `first_touch_at` per the resolution flow in data-model.md.
4. If this is a new customer (or `customers.first_touch_attribution IS NULL`), write `first_touch` leaf to the customer row.

### Validation

- All UTM strings ≤ 200 chars (existing constraint, mirrored to new fields).
- `attribution.first_touch.ts` and `last_touch.ts` parsed as ISO-8601; invalid → drop the field (don't 400 the whole request).
- Strip control characters from all string fields. Strip `<`, `>`, `"` after that.
- If `attribution.v != 1`, ignore the whole `attribution` block and fall back to flat `utm_*`. (Forwards-compatibility hatch — a v2 client talking to a v1 server degrades to flat UTMs rather than 400ing.)

---

## Share-link generation (storefront, client-side)

Not an HTTP contract — but it is a wire shape merchants will see. PDP share buttons construct outgoing URLs with these UTM params:

| Share channel | utm_source        | utm_medium |
| ------------- | ----------------- | ---------- |
| WhatsApp      | customer_share    | whatsapp   |
| Facebook      | customer_share    | facebook   |
| Instagram     | customer_share    | instagram  |
| Twitter/X     | customer_share    | twitter    |
| Telegram      | customer_share    | telegram   |
| Copy link     | customer_share    | copy_link  |

`utm_campaign` for customer shares is the string `"organic_share"` (no campaign resolution; appears in traffic-sources but never as a campaign with a `campaign_id`).

The product URL is constructed in the client via `${canonicalOriginFor(store)}/product/${product.slug || product.id}?utm_source=...&utm_medium=...&utm_campaign=organic_share` (the storefront already has access to the resolved origin via its own context).
