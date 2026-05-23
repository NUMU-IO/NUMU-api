# Contracts — Campaign actions (US6 duplicate + US7 compare + US8 tips)

Three new endpoints + one ingredient that powers the comparison UI.

---

## `POST /marketing/campaigns/{campaign_id}/duplicate` — US6

Create a Draft clone of an existing campaign.

**Body**: empty (no parameters)

**Response (201)**:
```json
{
  "data": {
    "id": "uuid-of-new-campaign",
    "channel": "email",
    "name": "Newsletter (Copy)",
    "status": "draft",
    "template_id": "uuid-or-null",
    "inline_subject": "...",
    "inline_body": "...",
    "segment_id": "uuid-or-null",
    "audience_filter": { ... },
    "scheduled_at": null,
    "started_at": null,
    "completed_at": null,
    "canceled_at": null,
    "total_recipients": 0,
    "sent_count": 0,
    "delivered_count": 0,
    "failed_count": 0,
    "note": null,
    "short_code": "ABC123",
    "created_at": "2026-05-24T15:00:00Z",
    "updated_at": "2026-05-24T15:00:00Z"
  },
  "message": "Campaign duplicated"
}
```

**Semantics (FR-029, FR-030)**:
- Copied: `name` (suffixed " (Copy)"), `channel`, `inline_subject`, `inline_body`, `template_id`, `audience_filter`, `segment_id`, `note`
- NOT copied: `scheduled_at`, `started_at`, `completed_at`, `canceled_at`, `total_recipients`, `sent_count`, `delivered_count`, `failed_count`
- Generated fresh: `short_code` (new Crockford base32), `created_at`, `id`, `status` (always `draft`)
- NOT carried over: trackable links, auto-match rules, activity log, campaign-attached coupons. Empty in the new draft.

---

## `GET /marketing/campaigns/compare?ids=a,b,c[,d]` — US7

Side-by-side compare for 2-4 campaigns. Returns each campaign's KPI summary + sessions-over-time series for the requested window.

**Query**:
- `ids` (comma-separated UUIDs, 2-4 items required)
- `date_from`, `date_to` (ISO 8601, required)
- `attribution_model` (enum, optional, default `last_touch`)
- `granularity` (enum `day` | `week`, optional, default `day` for windows < 60 days, `week` otherwise)

**Response**:
```json
{
  "data": {
    "date_from": "...",
    "date_to": "...",
    "attribution_model": "last_touch",
    "granularity": "day",
    "campaigns": [
      {
        "id": "uuid-a",
        "name": "Eid Sale",
        "short_code": "AB7K9X",
        "status": "completed",
        "found": true,
        "kpis": {
          "sessions": 1820,
          "sales_cents": 412000,
          "orders": 47,
          "average_order_value_cents": 8765
        },
        "series": [
          { "date": "2026-05-01", "sessions": 50, "sales_cents":  9000 },
          { "date": "2026-05-02", "sessions": 80, "sales_cents": 14500 },
          { "...one entry per granularity bucket..." }
        ]
      },
      {
        "id": "uuid-deleted",
        "name": null, "short_code": null, "status": null,
        "found": false,
        "kpis": null, "series": []
      }
    ]
  },
  "warnings": [
    { "code": "campaign_unavailable", "message": "1 of 3 campaigns is no longer available" }
  ],
  "message": "Comparison ready"
}
```

**Validation (FR-035)**:
- < 2 ids: 400 `"Pick 2 to 4 campaigns"`
- > 4 ids: 400 `"Pick 2 to 4 campaigns"`
- ids referencing a different store: 403 (existing tenant guard)
- Deleted/unknown ids: included in response with `found: false` + a `warnings` entry (FR-036)

---

## `GET /marketing/campaigns/{campaign_id}/tips` — US8

Heuristic optimization tips for a campaign, computed from existing aggregations.

**Query**:
- `date_from`, `date_to`, `attribution_model` (same window context as the detail page)

**Response**:
```json
{
  "data": {
    "campaign_id": "uuid",
    "date_from": "...",
    "date_to": "...",
    "attribution_model": "last_touch",
    "tips": [
      {
        "id": "boost-channel",
        "severity": "info",
        "title": "Facebook converts 3.2× better than Instagram",
        "body": "Consider shifting budget toward Facebook on the next send. Facebook delivered 12% CVR vs Instagram's 3.7%.",
        "data": { "winner_channel": "facebook", "winner_cvr": 0.12, "median_cvr": 0.0375 }
      },
      {
        "id": "coupon-cannibalization",
        "severity": "warning",
        "title": "Most revenue came via discount codes",
        "body": "72% of this campaign's revenue was discounted via coupons. Consider sending without a coupon next time to test full-price demand.",
        "data": { "coupon_revenue_share": 0.72 }
      }
    ]
  },
  "message": "Tips for campaign"
}
```

**Tip catalog (FR-038)**:

| `id` | Triggered when | `severity` |
| ---- | -------------- | ---------- |
| `boost-channel` | max channel CVR / median channel CVR > 2.5 (≥ 2 channels with ≥ 30 sessions each) | `info` |
| `coupon-cannibalization` | coupon_redeemed_revenue / total_revenue > 0.7 | `warning` |
| `mobile-skew` | mobile_session_share < 0.3 AND total_sessions ≥ 100 | `warning` |
| `top-product-concentration` | top_product_revenue / total_revenue > 0.6 (≥ 3 products with ≥ 1 order each) | `info` |

**Notes**:
- Returns up to 4 tips (one per trigger). Sorted by `severity` (`warning` > `info`) then by impact score.
- Empty `tips: []` is a normal response (no tip fired).
- Client-side: store dismissals in `sessionStorage` keyed by `(campaign_id, tip_id)`. Backend has no dismissal endpoint (per FR-039 + research §8).

---

## Errors

| HTTP | Cause | Endpoints |
| ---- | ----- | --------- |
| 401 / 403 / 404 | Standard | All |
| 400 | Invalid `ids` count, window > 365 days, invalid `granularity` for window | compare |
| 422 | Body / query validation | All |
