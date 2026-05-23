# Contracts — Best-time-to-send suggestions (US9)

One endpoint, called from the Schedule dialog open.

---

## `GET /stores/{store_id}/marketing/send-time-suggestions`

Returns up to 3 suggested send times for the store, ranked by historical open-rate (or fallback to send-count habit when no open data exists).

**Query**:
- `channel` (enum `email` | `sms` | `whatsapp`, optional, defaults to `email`) — restricts suggestions to historical sends of this channel
- `tz` (IANA timezone string, optional, defaults to store's timezone) — chips returned in this timezone

**Response (rich)**:
```json
{
  "data": {
    "store_id": "uuid",
    "channel": "email",
    "tz": "Africa/Cairo",
    "based_on": "open_rate",
    "sample_size": 32,
    "suggestions": [
      {
        "weekday": 4,
        "weekday_name": "Thursday",
        "hour":    19,
        "avg_open_rate": 0.42,
        "avg_sent": 1250,
        "label": "Thu 7 PM (avg 42% open)"
      },
      {
        "weekday": 0,
        "weekday_name": "Monday",
        "hour":    10,
        "avg_open_rate": 0.38,
        "avg_sent": 980,
        "label": "Mon 10 AM (avg 38% open)"
      },
      {
        "weekday": 6,
        "weekday_name": "Sunday",
        "hour":    20,
        "avg_open_rate": 0.34,
        "avg_sent": 1500,
        "label": "Sun 8 PM (avg 34% open)"
      }
    ]
  },
  "message": "Best-time suggestions for store"
}
```

**Response (fallback when no open data — FR-041)**:
```json
{
  "data": {
    "store_id": "uuid",
    "channel": "sms",
    "tz": "Africa/Cairo",
    "based_on": "send_count",
    "sample_size": 14,
    "suggestions": [
      { "weekday": 4, "weekday_name": "Thursday", "hour": 19, "avg_sent": 1000, "avg_open_rate": null, "label": "Thu 7 PM (your usual time)" },
      { "...two more..." }
    ]
  },
  "message": "Best-time suggestions for store (no open data — using send-count habit)"
}
```

**Response (insufficient data — FR-042)**:
```json
{
  "data": {
    "store_id": "uuid",
    "channel": "email",
    "tz": "Africa/Cairo",
    "based_on": null,
    "sample_size": 3,
    "suggestions": []
  },
  "message": "Not enough send history for suggestions yet (need 10+ prior sends)"
}
```

**Semantics**:
- Window: last 90 days of `marketing_campaign_sends` for this store and channel.
- Aggregation: GROUP BY weekday × hour. `avg_open_rate = SUM(opens_count) / NULLIF(SUM(sent_count), 0)`. `avg_sent = AVG(sent_count)`.
- Open data source: Resend `email.opened` webhook events joined to `marketing_campaign_sends` via `message_id`. Currently EMAIL only — SMS + WhatsApp fall back to `based_on: send_count` automatically (per research §4).
- Cap at 3 suggestions; ranked by `avg_open_rate DESC` (or `avg_sent DESC` in fallback mode).
- Each `label` is server-rendered for i18n cleanliness (frontend just displays).
- `weekday` follows Python's `weekday()` convention (0 = Monday, 6 = Sunday) — matches Postgres `EXTRACT(DOW FROM ts)::int` after we shift Sunday from 0 to 6.

**Frontend integration (FR-043)**:
- On chip click, the frontend computes the NEXT occurrence of the `weekday × hour` in the store's tz:
  ```ts
  function nextOccurrence(weekday: number, hour: number, tz: string): Date {
    // If today's weekday+hour > now, return today; else next week's occurrence
    // If returned date is in the past (edge case: chip clicked late in the slot's hour), advance another week
  }
  ```
- Setting the datetime picker to that occurrence is the entire chip behavior.

---

## Caching

In-memory cache per `(store_id, channel)` keyed on calendar day. Cache TTL = 1 hour. Implemented via FastAPI's request-scoped dependency injection backed by a process-local `cachetools.TTLCache`. SC-010 (200ms p95) achievable without persistent cache at single-store scale; revisit when multi-store agency rollout lands.

---

## Errors

| HTTP | Cause |
| ---- | ----- |
| 401 / 403 / 404 | Standard auth/ownership/store-not-found |
| 422 | Invalid `tz` (not a recognized IANA zone) or `channel` enum |
