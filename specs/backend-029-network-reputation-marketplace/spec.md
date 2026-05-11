# Backend Spec 029: Network Reputation Marketplace (v1 — bidirectional reputation + fast-lane)

**Feature Branch:** `backend-029-network-reputation-marketplace`
**Created:** 2026-05-11
**Status:** Draft (gated — DO NOT implement until network signal coverage ≥ 30% of EG COD phones)
**Repo:** `NUMU-api`
**Sibling spec:** `numu-payments-intelligence/specs/018-network-reputation-marketplace`
**Input:** Spec 018 (P3): "credit score for COD" framing. Buyers, merchants, couriers all carry network reputation. Trusted-buyer fast-lane checkout reduces friction → conversion lift. Only viable after spec 010 (positive signals) has earned data + spec 014 (benchmarking) proves the appetite.

> **Constitutional alignment:** Principle II (HMAC throughout); Principle III (DSAR + erasure paths for buyer reputation data; opt-in for merchants AND customers in checkout); Principle VI (frame the buyer-side as "Faster checkout for trusted customers", never "Suspicious customers slowed down").

## Why this feature exists + why it's gated

The meta-roadmap calls this the "credit score for COD" — the moat that emerges when the network has enough cross-merchant signal to identify trusted buyers proactively. Implementation depends on:

- Spec 010 in production with ≥ 30% of EG COD phones having a network record (positive or negative).
- Spec 014 in production with merchants demonstrating they want cross-store data (benchmarking adoption is the leading indicator).
- Legal review of buyer-side reputation surfacing (showing a buyer their own network trust score requires a privacy disclosure flow).

**Implementation gate:** all three preconditions met. Spec authored now to preserve design intent; defer implementation.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Trusted Buyer fast-lane in checkout (Priority: P1)

As a buyer with `network_trust >= 90`, when I reach checkout on any participating merchant, the COD verification step is skipped (no WhatsApp confirmation, no OTP) — trust is conferred from the network.

**Acceptance Scenarios:**

1. **Given** a buyer's hashed phone has `network_trust >= 90` AND the merchant participates in the fast-lane program, **When** the buyer reaches checkout, **Then** the verification UI is hidden + the order skips spec 015 OTP.
2. **Given** the buyer's network trust drops below 85 (delivery failure on another merchant), **When** they next checkout, **Then** the fast-lane disengages — verification UI returns. (Hysteresis between 90 entry / 85 exit prevents flicker.)
3. **Given** a buyer who has explicitly opted out of buyer-side reputation, **When** they checkout anywhere in the network, **Then** their data is NOT consulted — every merchant treats them as `network_trust=null`.

### User Story 2 — Merchant reputation badge (Priority: P2)

As a customer landing on a merchant's payment-link page, I see a "Reliable Merchant" badge if the merchant has a high network reputation (consistent recovery honors, low refund-to-recovery rate).

**Acceptance Scenarios:**

1. **Given** a merchant's `merchant_reputation >= 80` AND they've opted into surfacing the badge, **When** the customer loads `/p/{session_id}`, **Then** the badge renders next to the merchant name.

### User Story 3 — Courier reputation directory (Priority: P3)

As an internal admin (or, in a later iteration, the public), the courier reputation directory shows aggregated success rates per region per courier — sourced from spec 013's data.

### Edge Cases

- **Brand-new buyer with no network record.** No fast-lane. Existing OTP flow applies.
- **Network trust falling sharply (1 RTO drops Gold → Bronze).** Already-in-flight orders proceed under the fast-lane; the next checkout drops to verification.
- **Customer requests `customers/redact` mid-fast-lane session.** Session completes (the lookup happened before redact); the customer's network trust record is deleted within 30d.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001:** Extend `NetworkReputationModel` with computed `network_trust` int column (0-100; reuses backend-022 trust formula, but expressed network-wide rather than per-merchant). Computed nightly via Celery rollup; cached in Redis with 1-hour TTL.
- **FR-002:** New `MerchantReputationModel` storing per-merchant aggregate reputation: recovery-honor rate, refund-to-recovery rate, contributor-count of customers who've shopped here.
- **FR-003:** New `BuyerReputationOptOutModel` keyed by hashed phone; rows here exclude the buyer from any fast-lane lookup network-wide.
- **FR-004:** Extension to checkout API contract: pre-checkout call returns `{fast_lane_eligible: bool, merchant_reputation_badge: bool}`. The storefront / Shopify Checkout UI Extension consumes this.
- **FR-005:** Per Principle III: privacy disclosure surface for buyer-side reputation. Implementation: at first checkout that would benefit from fast-lane, present a one-time consent prompt: "Numu can speed up your checkout based on your shopping history. [Allow] [Decline]." Decline writes to `BuyerReputationOptOut`.
- **FR-006:** Hysteresis on fast-lane: enter at trust ≥ 90, exit at trust < 85. Prevents flicker on small score changes.
- **FR-007:** Per Principle VI: copy frames the feature as "Faster checkout for trusted customers" (positive). The opt-out UI says "Your shopping history can speed up future checkouts" (recovery-first), never "We track you across merchants" (defensive).

### Key Entities

```python
class MerchantReputationModel(Base, ...):
    __tablename__ = "merchant_reputation"
    merchant_id_hash: Mapped[str] = mapped_column(primary_key=True)  # HMAC of store_id
    recovery_honor_rate: Mapped[float] = mapped_column()
    refund_to_recovery_rate: Mapped[float] = mapped_column()
    contributor_count: Mapped[int] = mapped_column()
    badge_eligible: Mapped[bool] = mapped_column()
    last_refreshed_at: Mapped[datetime] = mapped_column()


class BuyerReputationOptOutModel(Base, ...):
    __tablename__ = "buyer_reputation_opt_out"
    phone_hash: Mapped[str] = mapped_column(primary_key=True)
    opted_out_at: Mapped[datetime] = mapped_column()
    opted_out_via: Mapped[str] = mapped_column(String(32))  # 'checkout_prompt', 'dsar_request', etc.
```

## Success Criteria *(mandatory)*

- **SC-001:** Fast-lane conversion lift: A/B test shows ≥ 10pp checkout-completion uplift on fast-lane vs non-fast-lane sessions.
- **SC-002:** Hysteresis effective: < 1% of buyers oscillate in/out of fast-lane within a single 24h period.
- **SC-003:** Opt-out enforcement: buyers who decline are excluded from EVERY merchant's fast-lane within 1 hour (Redis cache TTL).
- **SC-004:** Per Principle II: zero raw PII in any of the new tables. Quarterly audit.
- **SC-005:** Per Principle V: tests for every acceptance scenario.

## Assumptions

- Buyer-side reputation surfacing has been legally reviewed for EG + GCC + EU jurisdictions.
- The Shopify Checkout UI Extension permits the consent prompt + the verification-step hide.
- Network coverage at implementation time is ≥ 30% of EG COD phones (gating precondition).

## Out of scope

- **Public courier reputation directory.** Internal-only in v1; surfacing publicly requires courier consent.
- **Cross-platform reputation portability.** Once spec 017 platform adapters exist, reputation crosses platforms naturally — but v1 is platform-agnostic by virtue of `phone_hash` already being so.
- **Buyer-facing trust score UI.** Buyer can see "fast-lane eligible" but not the numeric score.
- **Merchant-tunable trust threshold.** System-managed (90 enter / 85 exit) for cross-merchant consistency.
