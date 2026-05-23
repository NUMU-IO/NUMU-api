# Contracts — Campaign activities (US5)

Manual attribution backfill — runs as a Celery task, exposed via two endpoints (one to kick off, one to list/poll status).

All endpoints under `/api/v1/stores/{store_id}/marketing/campaigns/{campaign_id}/activities`.

---

## `GET /activities`

List activity log entries for the campaign (most recent first).

**Query**:
- `limit` (int, optional, default 20, max 100)
- `type` (enum, optional — filters by activity type; v1 supports `backfill_attribution`)

**Response**:
```json
{
  "data": [
    {
      "id": "uuid",
      "type": "backfill_attribution",
      "status": "completed",
      "payload": {
        "utm_filters": [
          { "field": "utm_source", "operator": "equals", "value": "instagram" }
        ],
        "starts_at": "2026-03-01T00:00:00Z",
        "ends_at":   "2026-04-30T23:59:59Z"
      },
      "affected_count": 47,
      "skipped_count": 12,
      "error_message": null,
      "run_at":       "2026-05-24T14:00:00Z",
      "completed_at": "2026-05-24T14:00:23Z",
      "run_by": "uuid"
    },
    { "...status: running, completed_at: null..." }
  ],
  "message": "Activities for campaign"
}
```

---

## `POST /activities/backfill`

Kick off a backfill task. Returns 202 Accepted immediately with the activity row in `running` status; the task runs in the background.

**Body**:
```json
{
  "utm_filters": [
    { "field": "utm_source", "operator": "equals",     "value": "instagram" },
    { "field": "referrer",   "operator": "contains",   "value": "t.co/"     }
  ],
  "starts_at": "2026-03-01T00:00:00Z",
  "ends_at":   "2026-04-30T23:59:59Z"
}
```

**Validation**:
- `utm_filters`: 1-5 items. Each is `{ field, operator, value }` where:
  - `field`: enum `utm_source` | `utm_medium` | `utm_campaign` | `utm_term` | `utm_content` | `referrer`
  - `operator`: enum `equals` | `starts_with` | `contains`
  - `value`: string ≤ 500 chars
- `starts_at` < `ends_at`
- Window ≤ 365 days (FR-028) — 400 if exceeded
- No concurrent backfill on the same campaign — 409 if an activity with `type=backfill_attribution AND status=running` exists for this campaign

**Response (202)**:
```json
{
  "data": {
    "id": "uuid",
    "type": "backfill_attribution",
    "status": "running",
    "payload": { ... },
    "affected_count": null,
    "skipped_count": null,
    "run_at": "2026-05-24T14:00:00Z",
    "completed_at": null,
    "run_by": "uuid"
  },
  "message": "Backfill queued"
}
```

**Polling**: the UI polls `GET /activities?limit=5` every 3 seconds while a `running` entry exists, then stops polling once it transitions to `completed` or `failed`.

---

## Task semantics (Celery, not an HTTP endpoint)

Task name: `numu_api.marketing.backfill_campaign_attribution`
Args: `(activity_id: UUID, store_id: UUID, campaign_id: UUID)`

Execution:
1. Mark activity row `status=running` if not already.
2. Build a parametrized SQL `UPDATE` against `orders` AND `funnel_events`:
   ```sql
   UPDATE orders SET campaign_id = $1
     WHERE store_id = $2
       AND created_at >= $starts_at AND created_at <= $ends_at
       AND campaign_id IS NULL                          -- FR-025 skip already-attributed
       AND <utm_filter_expression>
   ```
3. Process in 5,000-row chunks via SAVEPOINT (per research §5). Sum `affected_count` per chunk.
4. Same for `funnel_events`.
5. On completion: update activity row to `status=completed`, set `affected_count + skipped_count + completed_at`.
6. On any uncaught exception: update activity row to `status=failed` with `error_message`. Task does not retry — the merchant re-submits if they want.

**Idempotency**: re-running the same backfill on unchanged data: the `WHERE campaign_id IS NULL` filter excludes everything attributed on the prior run → affected_count = 0, skipped_count = the previously-attributed rows. Matches FR-027.

---

## Errors

| HTTP | Cause |
| ---- | ----- |
| 401 / 403 / 404 | Standard |
| 400 | Window > 365 days; starts_at ≥ ends_at; invalid filter |
| 409 | Concurrent backfill on same campaign |
| 422 | Body validation (filter shape, enum values) |
