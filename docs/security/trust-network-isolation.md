# Trust Network — Tenant Isolation & Data-Privacy Model (P1-6)

> Due-diligence reference for how the COD Trust Network isolates merchant data
> and why its one intentionally-global table is safe. Pairs with the structural
> regression test `tests/unit/security/test_trust_network_isolation.py`.

## TL;DR

- A merchant can **never read another merchant's private order/risk data**.
- The cross-merchant **network reputation graph is intentionally global** — that
  is the product (a "credit score for COD"). It is safe to share because it
  holds **only an HMAC-SHA256 phone hash plus aggregate counters** — no raw PII,
  no per-store attribution in the read path.

## Two isolation patterns

### 1. Tenant-scoped tables — PostgreSQL RLS + explicit filter (defense in depth)
Tables that hold a merchant's private data (`orders`, `otp_codes`, …) are
isolated by **Row-Level Security**. Every request runs under a session variable
`app.current_tenant` (set by `infrastructure/tenancy/rls.py::set_tenant_context`
/ `narrow_to_tenant`); RLS policies restrict rows to that tenant. Repositories
**also** apply an explicit `tenant_id` filter as defense in depth, so a misset
policy alone cannot leak data. Admin/cross-tenant jobs must opt into
`app.rls_bypass` explicitly and disable it immediately after.

### 2. Public, store-scoped tables — explicit `store_id` filter
`risk_assessments` and `network_contribution_log` live in the `public` schema
and carry `store_id`. They are **never exposed by an API without a store scope**
derived from the authenticated merchant, so Store A cannot enumerate Store B's
assessments. `network_contribution_log` is internal-only (it backs the GDPR
decrement and the contributing-store count) — there is no merchant-facing read.

## The one global table — `network_reputation` (by design)

| Property | Value |
|---|---|
| Tenant column | **none** (no `tenant_id`, no `store_id`) — intentionally cross-merchant |
| Key | `phone_hash` (unique) = `HMAC-SHA256(E.164 phone, PLATFORM_SECRET_SALT)` |
| Contents | aggregate counters only: orders / RTOs / deliveries / refunds, `contributing_store_count`, a derived score |
| Raw PII | **none** — no phone, name, email, or address column |
| Reverse lookup | infeasible without the platform salt (kept in env, never in code/DB, rotatable) |

A store looking up a phone's network score reads a **global aggregate**, not any
other store's private rows. The aggregate carries no store attribution, so it
discloses nothing about *where* a buyer shopped — only their network-wide COD
reliability. This is the moat, and it is privacy-preserving by construction
(constitution Principle II).

## GDPR erasure

On `customers/redact` the customer's contribution rows are replayed from
`network_contribution_log` to **decrement** the matching `network_reputation`
aggregates (clamped at zero); a row whose counters all reach zero is
anonymized. On `shop/redact` the same decrement runs for the whole store. So a
buyer (or merchant) leaving the network removes exactly their contribution while
the shared aggregates stay accurate (`backend-014`).

## "Store A can't read Store B" — the guarantee

| Data | Cross-store readable? | Why it's safe |
|---|---|---|
| `orders`, `otp_codes` | No | RLS (`app.current_tenant`) + explicit tenant filter |
| `risk_assessments` | No | `public`, but every query is `store_id`-scoped; no unscoped API |
| `network_contribution_log` | No (internal-only) | no merchant-facing read; GDPR/back-office use only |
| `network_reputation` | **Yes — and intended** | global aggregate, HMAC hash + counts, **no PII, no store attribution** |
