# Contract — Merchant Campaign API

All endpoints below are scoped to a store and require merchant authentication. Base path: `/api/v1/stores/{store_id}/campaigns`.

The endpoints in this file extend the existing campaign surface; pre-existing endpoints (list, create, schedule, cancel) are not re-specified here.

---

## POST `/stores/{store_id}/campaigns/{campaign_id}/trackable-link`

Produce a single trackable URL plus a downloadable QR PNG for a campaign + destination combination.

### Request body

```json
{
  "destination": {
    "kind": "homepage" | "collection" | "product" | "custom",
    "collection_slug": "summer-2026",          // when kind = "collection"
    "product_id": "01HX2M...",                 // when kind = "product"
    "custom_path": "/lookbook/eid-2026"        // when kind = "custom"; must pass validate-path first
  },
  "source": "facebook" | "instagram" | "whatsapp" | "email" | "tiktok" | "sms" | "qr" | "other",
  "medium": "social",                          // optional; defaults derived from source preset (see table below)
  "term": null,
  "content": null
}
```

### Source preset table (when `medium` omitted)

| source        | medium    |
| ------------- | --------- |
| facebook      | social    |
| instagram     | social    |
| whatsapp      | messaging |
| email         | email     |
| tiktok        | social    |
| sms           | sms       |
| qr            | qr        |
| other         | (none)    |

### Response — 200

```json
{
  "data": {
    "url": "https://acme.numueg.app/product/abc-123?utm_source=facebook&utm_medium=social&utm_campaign=eid-sale-2026-AB7K&utm_content=eid-promo",
    "qr_png_base64": "iVBORw0KGgoAAAANS...",
    "short_code": "AB7K",
    "campaign_slug": "eid-sale-2026",
    "destination": {
      "kind": "product",
      "product_id": "01HX2M...",
      "resolved_path": "/product/abc-123"
    }
  }
}
```

### Response — 400 (bad destination)

```json
{ "error": "invalid_destination", "detail": "Product not found on this store" }
```

### Response — 422 (custom path failed validation)

```json
{ "error": "custom_path_invalid", "detail": "Path /lookbook/eid does not resolve on this storefront", "validate_path_result": { ... } }
```

### Behavior notes

- Server uses `canonicalOriginFor(store)` equivalent — custom domain wins over subdomain.
- `utm_campaign` is composed as `<slug(campaign.name)>-<short_code>` so it's human-readable AND stably resolvable. Slug uses kebab-case lowercase, ASCII only (Arabic campaign names get transliterated to a short hash + short_code).
- QR PNG is 512×512, error-correction level M, base64-encoded.
- The same call is idempotent — calling it twice with the same body returns the same URL (no DB write happens; the short_code is on the campaign already).

---

## POST `/stores/{store_id}/storefront/validate-path`

Pre-flight a custom destination path before producing a trackable link.

### Request body

```json
{ "path": "/lookbook/eid-2026" }
```

### Response — 200

```json
{
  "data": {
    "valid": true,
    "canonical_path": "/lookbook/eid-2026",
    "http_status": 200
  }
}
```

### Response — 200 (with redirect suggestion)

```json
{
  "data": {
    "valid": true,
    "canonical_path": "/lookbook/eid-2026",
    "suggested_canonical": "/lookbook/eid-2026/",
    "http_status": 301
  }
}
```

### Response — 422

```json
{
  "data": {
    "valid": false,
    "reason": "path_not_found" | "path_malformed" | "validation_timeout" | "external_host",
    "http_status": 404
  }
}
```

### Behavior notes

- Server issues a HEAD request to `{canonicalOriginFor(store)}{path}` with `User-Agent: NUMU-LinkValidator/1.0` and a 3-second timeout.
- Rejects any path containing a scheme (`http://`, `//`, `\\`) — those are not paths, they're URLs. Returns `external_host` reason.
- Follows a single redirect; reports the redirect target as `suggested_canonical`.
- Accepts 200, 301, 302, 308 as valid; rejects 4xx, 5xx, timeouts.

---

## GET `/stores/{store_id}/campaigns/{campaign_id}/performance`

Aggregated performance metrics for one campaign, with optional date filtering.

### Query parameters

- `date_from` (ISO-8601 date or datetime, required)
- `date_to` (ISO-8601 date or datetime, required)
- `granularity` — `day` | `week` | none (default none — only totals)

### Response — 200

```json
{
  "data": {
    "campaign_id": "01HX2M...",
    "campaign_name": "Eid Sale 2026",
    "short_code": "AB7K",
    "date_from": "2026-04-01T00:00:00Z",
    "date_to": "2026-05-21T23:59:59Z",
    "totals": {
      "sessions": 1342,
      "product_views": 894,
      "add_to_cart": 211,
      "checkout_started": 87,
      "orders": 54,
      "revenue_cents": 4382100,
      "average_order_value_cents": 81150,
      "conversion_rates": {
        "session_to_atc": 0.157,
        "atc_to_checkout": 0.412,
        "checkout_to_order": 0.621,
        "session_to_order": 0.040
      }
    },
    "top_products": [
      { "product_id": "01HX...", "name": "Linen kaftan", "orders": 12, "revenue_cents": 960000 }
    ],
    "time_series": [
      { "date": "2026-04-01", "sessions": 24, "orders": 1, "revenue_cents": 81000 }
    ]
  }
}
```

### Behavior notes

- "Sessions" is `COUNT(DISTINCT session_fingerprint)` on funnel events where `campaign_id = :campaign_id` for the window.
- "Orders" is `COUNT(*)` on `orders` where `campaign_id = :campaign_id` AND `status NOT IN ('cancelled', 'refunded')` for the window.
- `time_series` is only populated when `granularity` is set.
- `top_products` is computed by joining attributed orders to their line items, ranked by `SUM(line_total)`. Limit 10.

---

## Authentication

All three endpoints require the existing merchant-session authentication (`Depends(get_current_user)` or equivalent). Authorization: the authenticated user must have write access (for the link generator) or read access (for performance) on `store_id`.
