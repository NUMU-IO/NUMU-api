# Contracts — Analytics breakdown endpoints

Five new GET endpoints that feed the 8-panel chart grid on the campaign detail page (US3). Three panels reuse existing endpoints: KPI cards reuse `GET /performance` (no change), top products reuses the same, multi-touch (header pill) reuses the existing `/analytics/multi-touch`.

All endpoints under `/api/v1/stores/{store_id}/marketing/campaigns/{campaign_id}/breakdown/...`. All accept `?date_from` and `?date_to` ISO timestamps + (where applicable) `?attribution_model` (one of `last_touch`/`first_touch`/`linear`/`time_decay`/`position_based`, defaults to `last_touch`).

All responses wrapped in `SuccessResponse<T>` envelope (matches existing repo pattern). All requests authenticated + tenant-scoped via existing `verify_store_ownership` dependency.

---

## `GET /breakdown/channel`

Drives both "Sessions by channel" and "Sales by channel" panels in one query (saves a round trip; UI splits the rows into two bar charts).

**Query**:
- `date_from` (ISO 8601, required)
- `date_to` (ISO 8601, required)
- `attribution_model` (enum, optional, default `last_touch`)

**Response**:
```json
{
  "data": {
    "campaign_id": "uuid",
    "date_from": "2026-05-01T00:00:00Z",
    "date_to": "2026-05-24T23:59:59Z",
    "attribution_model": "last_touch",
    "channels": [
      { "channel": "facebook",  "sessions": 1240, "sales_cents": 234500 },
      { "channel": "instagram", "sessions": 980,  "sales_cents": 175000 },
      { "channel": "direct",    "sessions": 320,  "sales_cents":  45000 }
    ]
  },
  "message": "Channel breakdown for campaign"
}
```

**Notes**:
- `channel = COALESCE(NULLIF(LOWER(TRIM(utm_source)), ''), 'direct')`
- Empty `channels: []` when no data in window (UI shows "No data for this date range.")

---

## `GET /breakdown/utm`

Drives "Sessions by UTM parameters" and "Sales by UTM parameters" panels. Returns top N (default 20) `(source, medium, campaign, term, content)` combos by sessions; UI shows top-by-sessions and top-by-sales as separate sorts of the same payload.

**Query**:
- `date_from`, `date_to`, `attribution_model` (same as above)
- `limit` (int, optional, default 20, max 100)

**Response**:
```json
{
  "data": {
    "campaign_id": "uuid",
    "date_from": "...",
    "date_to": "...",
    "attribution_model": "last_touch",
    "combos": [
      {
        "utm_source":   "facebook",
        "utm_medium":   "social",
        "utm_campaign": "eid-sale-AB7K9X",
        "utm_term":     null,
        "utm_content":  "headline-A",
        "sessions": 540,
        "sales_cents": 98000
      },
      { ... }
    ]
  },
  "message": "UTM combo breakdown for campaign"
}
```

---

## `GET /breakdown/customer-type`

Drives "Orders from new vs returning customers" donut.

**Query**: `date_from`, `date_to`, `attribution_model`

**Response**:
```json
{
  "data": {
    "campaign_id": "uuid",
    "date_from": "...",
    "date_to": "...",
    "attribution_model": "last_touch",
    "new_customers":      { "orders": 47, "sales_cents": 89500 },
    "returning_customers": { "orders": 32, "sales_cents": 124000 }
  },
  "message": "Customer-type breakdown for campaign"
}
```

**Notes**:
- "New" = customer's `first_touch_at` falls inside the window AND their first attributed order is in this campaign. "Returning" = customer has ≥ 1 prior attributed order on this store regardless of which campaign (per the spec's assumption).

---

## `GET /breakdown/order-size`

Drives the "Sales by order" histogram. Returns 10 fixed bins by order total.

**Query**: `date_from`, `date_to`, `attribution_model`

**Response**:
```json
{
  "data": {
    "campaign_id": "uuid",
    "date_from": "...",
    "date_to": "...",
    "attribution_model": "last_touch",
    "bins": [
      { "lower_cents":      0, "upper_cents":   5000, "orders": 12 },
      { "lower_cents":   5000, "upper_cents":  10000, "orders": 28 },
      { "lower_cents":  10000, "upper_cents":  20000, "orders": 19 },
      { "lower_cents":  20000, "upper_cents":  50000, "orders":  9 },
      { "lower_cents":  50000, "upper_cents": 100000, "orders":  4 },
      { "lower_cents": 100000, "upper_cents": 200000, "orders":  2 },
      { "lower_cents": 200000, "upper_cents": 500000, "orders":  1 },
      { "lower_cents": 500000, "upper_cents":1000000, "orders":  0 },
      { "lower_cents":1000000, "upper_cents":2000000, "orders":  0 },
      { "lower_cents":2000000, "upper_cents":      null, "orders":  0 }
    ]
  },
  "message": "Order-size histogram for campaign"
}
```

**Notes**:
- Bins are fixed (not auto-derived) so the histogram is stable across renders and stores.
- Last bin's `upper_cents = null` represents the "any larger" overflow.

---

## `GET /breakdown/device`

Drives "Sessions by device" donut.

**Query**: `date_from`, `date_to`, `attribution_model`

**Response**:
```json
{
  "data": {
    "campaign_id": "uuid",
    "date_from": "...",
    "date_to": "...",
    "attribution_model": "last_touch",
    "devices": [
      { "device": "mobile",  "sessions": 1820 },
      { "device": "desktop", "sessions":  640 },
      { "device": "tablet",  "sessions":   80 },
      { "device": "unknown", "sessions":   45 }
    ]
  },
  "message": "Device breakdown for campaign"
}
```

**Notes**:
- `unknown` bucket aggregates rows where `device IS NULL` (historical events before the device-tracking deployment).

---

## Error responses (all five endpoints)

| HTTP | Cause | Body |
| ---- | ----- | ---- |
| 401 | Missing/invalid auth | `{ "detail": "Not authenticated" }` |
| 403 | User doesn't own store | `{ "detail": "Forbidden" }` |
| 404 | Campaign not found in store | `{ "detail": "Campaign not found" }` |
| 422 | Invalid `attribution_model` enum value | `{ "detail": [...] }` (FastAPI validation) |
| 400 | Date window exceeds 365 days | `{ "detail": "Date window cannot exceed 365 days" }` |
