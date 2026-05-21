# Feature Specification: Trust Network Correctness

**Feature Branch**: `backend-014-trust-network-correctness`
**Created**: 2026-05-09
**Status**: Draft

## Why this exists

A 2026-05-09 audit found that the cross-merchant trust network — the
strategic moat numu-trust-network is positioned around — is **scaffold
only, not load-bearing**. Schema and write/read paths exist, but four
gaps mean it never functions in production:

  1. `PLATFORM_SECRET_SALT` defaults to empty string. With no salt,
     `extract_phone_hash_from_string()` logs an error and returns
     `None`. Every `write_network_event` call becomes a no-op. The
     network table stays empty. Risk scoring falls back to baseline
     55. The "moat" is silent fail-open.
  2. No per-store opt-in. Writes fire unconditionally for every COD
     order. GDPR Recital 47 "legitimate interest" is words in a spec,
     not a column on the database.
  3. `customers/redact` only deletes from `risk_assessments` keyed on
     **email**. The network signal is keyed on phone hash; deleted
     customers' contributions persist forever.
  4. `customers/data_request` exports risk assessments only. Network
     contributions — which contain the customer's cross-merchant
     reputation footprint — are silently excluded from the DSAR
     report.

## Requirements

- **FR-001**: In `settings.environment == "production"`, missing
  `platform_secret_salt` MUST raise on app boot, not silently allow
  the system to start with phone hashing disabled. Dev/test still
  allow empty.
- **FR-002**: New `trust_network_enabled: bool` column on
  `shopify_app_settings`, default `True` (consent captured at
  install via the disclosure modal).
  `network_reputation_service.write_network_event` MUST consult the
  store's flag when a settings repo is provided and skip the write
  when disabled. Bypass paths log
  `network_event_consent_check_bypassed` so they're auditable.
- **FR-003**: New `NetworkReputationRepository.delete_customer_network_data(
  store_id, phone_hash)` method that replays the contribution log
  for that customer at that store, decrements the matching
  `network_reputation` aggregates (clamped at zero), deletes the
  contribution rows, recomputes the cached score, and anonymizes
  the row when all aggregates reach zero.
- **FR-004**: `customers/redact` webhook handler MUST extract the
  customer's phone from the payload, hash it, and call
  `delete_customer_network_data(...)`. Email-based deletion of
  `risk_assessments` continues alongside.
- **FR-005**: New `NetworkReputationRepository.list_customer_contributions(
  store_id, phone_hash)` method returning the contribution-log rows
  for one customer at one store.
- **FR-006**: `customers/data_request` webhook handler MUST include
  the customer's network contributions in the export when phone is
  provided.

## User Stories

### Story 1 — Production refuses to start without a salt (P0)

When the deployment env is set to `production` and
`PLATFORM_SECRET_SALT` is empty, the app boot MUST fail with a clear
error message. Better than silent fail-open + an empty network six
weeks into operation.

### Story 2 — Merchants can opt out of the network (P0)

A merchant who flips `trust_network_enabled = false` in
`shopify_app_settings` MUST stop contributing any signal to the
cross-merchant network. No webhook event records. No leak.

### Story 3 — DSAR erasure removes the customer from the network (P0)

When Shopify fires `customers/redact` with the customer's phone in
the payload, the customer's contributions to the network signal MUST
be erased. The `network_reputation` aggregate is decremented by
exactly the contribution count for that store + phone hash. Zero
aggregate rows are anonymized.

### Story 4 — DSAR export shows network contributions (P0)

When Shopify fires `customers/data_request` with the customer's
phone, the export MUST include the network contribution log for
that customer + store, alongside the risk assessments.

## Success Criteria

- **SC-001**: `pytest tests/api/test_trust_network_correctness.py -v` green.
- **SC-002**: Grep for `PLATFORM_SECRET_SALT.*default=""\|default=" "` returns no production-fail-open.
- **SC-003**: A `customers/redact` webhook with phone in the payload
  returns `network.contribution_rows_deleted >= 1` for any customer
  who had previously placed an order on that store.

## Out of scope

- Native NUMU storefront opt-in. The `trust_network_enabled` flag
  applies to Shopify-integrated stores. Native stores still write
  through `write_network_event` with no settings_repo (the bypass
  path), which logs each event but doesn't gate it. A separate
  spec will add a native-store consent column.
- Configurable consent disclosure copy. The merchant-facing modal
  text is owned by the Shopify-app frontend.
