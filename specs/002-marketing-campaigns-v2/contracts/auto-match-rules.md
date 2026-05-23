# Contracts — Auto-match rules (US4)

CRUD for `campaign_auto_match_rules`. All endpoints under `/api/v1/stores/{store_id}/marketing/campaigns/{campaign_id}/auto-match-rules`.

A rule "group" is a logical AND/OR combination of N row-level conditions sharing a `group_id`. The API exposes groups as the unit of merchant interaction — clients send/receive group objects, not individual rows.

---

## `GET /auto-match-rules`

List all rule groups for a campaign.

**Response**:
```json
{
  "data": [
    {
      "group_id": "uuid",
      "combinator": "AND",
      "priority": 10,
      "conditions": [
        { "field": "utm_source",   "operator": "equals",     "value": "facebook" },
        { "field": "utm_medium",   "operator": "starts_with", "value": "cpc"     }
      ],
      "created_at": "2026-05-24T12:00:00Z",
      "created_by": "uuid"
    },
    { ... }
  ],
  "message": "Auto-match rules for campaign"
}
```

---

## `POST /auto-match-rules`

Create a new rule group.

**Body**:
```json
{
  "combinator": "AND",
  "priority": 10,
  "conditions": [
    { "field": "utm_source", "operator": "equals", "value": "facebook" }
  ]
}
```

**Response**: 201 Created with the group object (same shape as GET).

**Validation**:
- `combinator`: enum `AND` | `OR`, required
- `priority`: int ≥ 0, required, MUST be unique within `store_id` — 422 if collides
- `conditions`: 1-10 items, each with `field` (enum), `operator` (enum), `value` (string ≤ 200 chars)
- `value` is lowercased + trimmed server-side before storage (matches UTM normalization)

**Side-effect**: if any condition would overlap a higher-priority existing rule's match set, response includes a non-blocking `warnings` field:
```json
{
  "data": { ...the group... },
  "warnings": [
    { "code": "rule_overlap", "message": "This rule overlaps with Campaign X (priority 5) — that rule wins" }
  ],
  "message": "Rule created"
}
```

---

## `DELETE /auto-match-rules/{group_id}`

Delete a rule group (all rows sharing the `group_id`).

**Response**: 204 No Content.

**Notes**:
- Deletion only affects NEW traffic going forward (per FR-022). Historical attributions stand.
- Subsequent traffic that would have matched falls through to the next-priority rule, or to short_code resolution, or stays unattributed.

---

## Errors (all endpoints)

| HTTP | Cause |
| ---- | ----- |
| 401 / 403 / 404 | Standard auth/ownership/campaign-not-found |
| 422 | Validation errors (priority collision, invalid enum, value too long) |

---

## Ingest-time integration (not an endpoint)

When a funnel event POST arrives at `/api/v1/storefront/track`:

1. Existing path: if URL carries a recognized `short_code`, resolve campaign from short_code (no change).
2. NEW: if no short_code match, fetch active rules for this store ordered by `priority` ASC. For each rule group, evaluate conditions against the event's `utm_*` fields per the group's `combinator`. First matching group wins. Stamp `funnel_events.campaign_id = group.campaign_id`.
3. If no rule matches either, `funnel_events.campaign_id` stays NULL (current behavior).

**Performance**: rules fetched once per request via `lru_cache` keyed on `(store_id, request_id)`. ~0.5ms overhead per ingest event on stores with 100 rules.
