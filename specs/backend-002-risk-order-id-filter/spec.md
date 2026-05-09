# Feature Specification: shopify_order_id Filter on /risk/orders

**Feature Branch**: `backend-002-risk-order-id-filter`
**Created**: 2026-05-08
**Status**: Draft
**Effort**: ~2 hours (smallest P0 in the backend roadmap).

## Why this exists

The Shopify-app's order-risk-card admin block extension (feature 003,
shipped) calls `GET /api/v1/shopify/{store_id}/risk/orders?shopify_order_id={id}`
expecting a single-row response. Today the endpoint ignores the query
parameter and returns the full ~50-item list, forcing the extension to
search client-side and creating ~50KB responses where ~5KB would do.

This adds the missing filter — a one-line query addition + test.

## User Story 1 — single-order lookup (Priority: P1)

As the order-risk-card extension's proxy route, when I pass
`?shopify_order_id=gid://shopify/Order/123` to the existing risk-orders
endpoint, I get back at most one record (the one matching that Shopify
order id) instead of the full page-1 list.

**Acceptance**:

1. **Given** an existing risk-assessment row with
   `shopify_order_id="gid://shopify/Order/X"`, **When** the endpoint is
   called with `?shopify_order_id=gid://shopify/Order/X`, **Then** the
   response data array contains exactly that record.
2. **Given** no row matches the supplied `shopify_order_id`, **When**
   the endpoint is called, **Then** the response is `200 {data: []}`.
   (Not 404 — the route's contract is "list, possibly empty"; 404 is
   handled by the Shopify-app's proxy as `{status: "pending"}`.)
3. **Given** the parameter is omitted, **When** the endpoint is called,
   **Then** the existing behavior is preserved (paginated full list).

## Requirements

- **FR-001**: `GET /api/v1/shopify/{store_id}/risk/orders` MUST accept
  an optional `shopify_order_id` query parameter (string, max 255 chars).
- **FR-002**: When supplied, results MUST be filtered to rows whose
  `shopify_order_id` column equals the parameter exactly.
- **FR-003**: When omitted, behavior is unchanged from prior versions
  (paginated by `limit` + `offset`).

## Success Criteria

- **SC-001**: With seed data and the param set, response payload size
  is < 5KB and contains 0 or 1 records.
- **SC-002**: `pytest tests/api/test_shopify_risk_filter.py -v` green.
