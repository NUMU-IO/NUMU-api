# Backend Spec 025: WhatsApp OTP Verification

**Feature Branch:** `backend-025-whatsapp-otp`
**Created:** 2026-05-11
**Status:** Draft
**Repo:** `NUMU-api` (Python / FastAPI / Postgres / Celery)
**Sibling spec:** `numu-payments-intelligence/specs/015-whatsapp-otp-verification` (the storefront / checkout UI consumer)
**Input:** Spec 015 (P2) declares NUMU-api as the owner of OTP issuance, verification, rate-limiting, and the trust-signal contribution that follows a successful verify. This spec is the backend half — UI consumer is a separate spec.

> **Constitutional alignment:** Principle II (no raw PII cross-store; phone hashed via the existing platform pepper); Principle III (legitimate-interest = fraud prevention; DSAR exports include `OtpCode` rows where `phone_hash` matches; erasure path on `customers/redact` deletes those rows within 30 days); Principle IV (deterministic — no ML in the verify decision); Principle V (acceptance scenarios → tests); Principle VI (recovery-first framing — copy says "Confirm your number to continue", never "verify identity for fraud").

## Why this feature exists

Fake-phone fraud is the cheapest fraud vector to defeat in MENA COD: a customer enters a random number, the COD never gets verified, the courier knocks on a non-existent door, and the order RTO's. WhatsApp OTP at first-order pins the phone to a real WhatsApp account on the spot.

The existing infrastructure already has all the primitives:
- Meta WhatsApp client + `MessageLog` (spec 000)
- Phone HMAC pipeline (`phone_hash`)
- Risk scoring (extending the trust factor — spec 010 / backend-022)

What's missing: the OTP table, issuance + verification endpoints, the rate-limit guards, and the hook that turns a successful verify into a positive trust signal.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — First-order OTP issuance (Priority: P1)

As a customer placing a first-ever COD order on a store that has OTP enabled, I receive a 6-digit code via WhatsApp within 30 seconds.

**Independent Test:** call `POST /shopify/{store_id}/otp/issue` with a phone; verify (a) a `OtpCode` row exists with `phone_hash` matching the platform-pepper hash, `expires_at` ≈ now + 5 min, `attempts_left = 3`; (b) a WhatsApp message was sent via the existing client with the code in the template variable.

**Acceptance Scenarios:**

1. **Given** an issuance request with a valid phone, **When** the endpoint runs, **Then** an `OtpCode` row is INSERTed with a HMAC-hashed `code_hash` (never the cleartext code), `expires_at = now + 5 min`, `attempts_left = 3`, and the WhatsApp template `otp_verification_{lang}` is sent with the cleartext code as the `{{1}}` variable.
2. **Given** the same phone receives 5 issuance requests in the last hour, **When** a 6th request arrives, **Then** the endpoint returns 429 with `Retry-After`; no new row, no new WhatsApp send.
3. **Given** Meta's WhatsApp API returns a permanent error (number blocked, template rejected), **When** the issuance handler catches it, **Then** the `OtpCode` row is marked `failed_send_at = now()` and the endpoint returns the failure to the caller (the storefront UI degrades to "We couldn't reach this number — try a different one or skip").

### User Story 2 — OTP verification + trust signal (Priority: P1)

As a customer who entered the 6-digit code, the verify endpoint marks my phone as verified and emits a positive trust signal so future orders at this merchant (and across the network) get bumped on the trust factor.

**Acceptance Scenarios:**

1. **Given** a valid `OtpCode` row with `attempts_left > 0` and `expires_at > now`, **When** the customer submits the matching code, **Then** the row is marked `verified_at = now()`, an `OtpVerifiedEvent` is emitted, and the response is `{verdict: "verified"}`.
2. **Given** the same conditions but the code doesn't match, **When** the verify runs, **Then** `attempts_left` decrements by 1 and the response is `{verdict: "wrong_code", attempts_left: N}`. After 3 failed attempts the row is locked (further verifies return `{verdict: "locked"}`) until expiry.
3. **Given** an expired row, **When** verify runs, **Then** the response is `{verdict: "expired"}` and the customer is invited to issue a new code.
4. **Given** a successful verify, **When** the `OtpVerifiedEvent` handler runs, **Then** `write_network_event` is called with `event_type='order'` (positive baseline contribution) — the phone's network reputation gains a "verified buyer" signal that the customer_trust formula consumes via `network_positive_events` (spec 010 FR-001).

### Edge Cases

- **Customer enters the code twice in quick succession.** The `verified_at` field acts as the idempotency gate — second verify returns the same `{verdict: "verified"}` without re-emitting the event.
- **Customer changes phone mid-flow.** The new phone gets a new OTP issuance; the old `OtpCode` row times out + is purged by the existing `data_retention_task`.
- **Merchant has WhatsApp disconnected.** Issuance returns 503 with `{detail: "whatsapp_not_connected"}`; the storefront UI degrades to skipping OTP for this merchant.
- **`customers/redact` arrives.** All `OtpCode` rows for the customer's phone hash are deleted within 30 days per the existing GDPR pipeline.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001:** A new `OtpCode` SQLAlchemy model is added at `src/infrastructure/database/models/tenant/otp_code.py` with the schema: `id`, `tenant_id`, `store_id`, `phone_hash` (indexed), `code_hash` (HMAC-SHA256 of the cleartext code with the platform pepper), `language` (`'ar'|'en'`), `expires_at`, `attempts_left`, `verified_at`, `failed_send_at`, `created_at`, `updated_at`.
- **FR-002:** Issue endpoint at `POST /api/v1/shopify/{store_id}/otp/issue`. Body: `{phone, language}`. Generates a 6-digit numeric code using `secrets.randbelow(900_000) + 100_000`; hashes it; stores it; sends WhatsApp template `otp_verification_{language}` with the cleartext code as the first variable; returns `{otp_id, expires_at, language}`.
- **FR-003:** Verify endpoint at `POST /api/v1/shopify/{store_id}/otp/verify`. Body: `{otp_id, code}`. Verdicts: `verified`, `wrong_code`, `locked`, `expired`, `unknown`. Decrement `attempts_left` on `wrong_code`; never reveal the cleartext code in the response or logs.
- **FR-004:** Rate limit: 5 issuances per phone per rolling hour. Enforced via a Redis counter keyed by `f"otp:issue:{phone_hash}"`.
- **FR-005:** TTL: 5 minutes from issuance. Attempts: 3. Both are constants in `src/application/services/otp_service.py` so retro changes happen in one place.
- **FR-006:** Code never persisted in cleartext. Always hashed via HMAC-SHA256 with the existing `PLATFORM_SECRET_SALT`. Verify compares HMAC-of-input to stored hash via constant-time `hmac.compare_digest`.
- **FR-007:** New `OtpVerifiedEvent` (`src/core/events/otp_events.py`) emitted on successful verify. Carries `tenant_id`, `store_id`, `phone_hash`, `verified_at`. Subscribed to by a new handler that calls `write_network_event(event_type='order')` per spec 010 FR-001's positive-signal pipeline.
- **FR-008:** Per Principle III (GDPR Recital 47): legitimate interest = fraud prevention; DSAR includes `OtpCode` rows for the requesting phone; erasure on `customers/redact` deletes rows within 30 days via the existing `data_retention_task`; opt-out = the customer can refuse to enter the code (the storefront UI proceeds without the trust bump).
- **FR-009:** Per Principle II: `phone_hash` is the only phone representation in the `otp_codes` table; raw phone is passed to the WhatsApp client at send time but never stored.
- **FR-010:** Constitution V: every acceptance scenario above maps to a test in `tests/integration/test_whatsapp_otp.py`.

### Key Entities

```python
class OtpCode(Base, UUIDMixin, TenantMixin, TimestampMixin):
    __tablename__ = "otp_codes"
    __table_args__ = (
        Index("ix_otp_codes_phone_hash", "phone_hash"),
        Index("ix_otp_codes_store_phone", "store_id", "phone_hash"),
        {"schema": "public"},
    )
    store_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str] = mapped_column(String(2), nullable=False, server_default="'ar'")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts_left: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_send_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

## Success Criteria *(mandatory)*

- **SC-001:** Issue → WhatsApp sent within 30 seconds (p95). Measured by `MessageLog.sent_at - OtpCode.created_at`.
- **SC-002:** Verification accuracy: 100% of cleartext codes that match their `code_hash` return `verified`; 100% of mismatches return `wrong_code` and decrement attempts. Verified by integration test fixtures.
- **SC-003:** No cleartext codes in logs or DB. Quarterly audit greps `MessageLog.metadata` + `otp_codes` for 6-digit patterns; finds zero stored cleartexts beyond the rendered template body (which Meta-side log retention controls per spec 009 CL-010).
- **SC-004:** Rate limit fires correctly: 5 issuances allowed per rolling hour, 6th returns 429.
- **SC-005:** OtpVerifiedEvent → positive `network_event` written within 30s for consenting merchants.

## Assumptions

- The existing WhatsApp client supports a template named `otp_verification_ar` / `otp_verification_en` with a single `{{1}}` variable for the cleartext code. If not, the templates are submitted to Meta as part of `/speckit.implement`.
- Redis is available for the rate-limit counter (already used by other parts of NUMU-api).
- The storefront (or checkout) page that renders the OTP entry surface lives in `numu-storefront` per spec 015's frontend scope; this backend does NOT prescribe a UI route.

## Out of scope

- **Voice OTP fallback** — WhatsApp-only in v1.
- **TOTP / authenticator app** — different flow, future spec.
- **Pre-checkout OTP** for marketing email signup — use case is fraud-prevention at order time, not list-building.
- **Per-merchant template overrides** for the OTP message — uses the system-shipped templates.
