"""Pure rules engine for auto-approving InstaPay proofs.

Split from the use case so it can be unit-tested without touching the
database. The engine is deliberately framework-agnostic: it takes
already-resolved facts (the store's config, the proof-so-far, the
cumulative daily stats, the dedup lookups) and returns a verdict.

Order of checks matters because some are cheaper than others — but also
because the returned ``AutoApprovalDecision.reasons`` should reflect
*all* failing rules, not just the first, so the merchant review UI can
show the full picture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.core.entities.instapay import InstapayIntent, PaymentProof


@dataclass
class AutoApprovalConfig:
    """The per-store thresholds that gate auto-approval.

    Sourced from the merchant's PaymentSetup form, with module defaults
    applied upstream when a field is unset.

    ``amount_mismatch_tolerance_bps`` is expressed in basis points of
    the order total — 100 bps = 1%. Default 100 matches the audit
    recommendation: a customer who declared an amount within 1% of the
    order total auto-approves; further off and we route to merchant
    review. Set to 0 to auto-reject any non-exact match; set to None
    to disable the rule entirely (e.g. when the storefront doesn't
    collect declared_amount).
    """

    threshold_cents: int
    daily_cap_cents: int
    daily_count_cap: int
    amount_mismatch_tolerance_bps: int | None = 100


@dataclass
class AutoApprovalFacts:
    """Everything the engine needs that it can't compute itself.

    Dedup (image-hash, transaction-ref) is intentionally *not* a fact
    the engine consults. The use case short-circuits with 409 before
    calling here — trying to reuse a proof is misuse, not a soft
    block, and silently routing to merchant review would reward the
    behaviour with a human reviewing fraud artefacts.
    """

    order_total_cents: int
    daily_auto_approved_count: int
    daily_auto_approved_cents: int
    now: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class AutoApprovalDecision:
    """Verdict the use case acts on.

    ``approved`` is the outcome. ``reasons`` is populated whether or not
    the decision was to approve — when approved, it's empty; when
    rejected, it lists every failing rule so the caller can log/display
    them all. ``soft_block`` means "don't auto-approve, but route to
    merchant review" rather than "reject the upload entirely".
    """

    approved: bool
    reasons: list[str] = field(default_factory=list)
    soft_block: bool = True  # default: send to merchant, don't hard-reject


def evaluate(
    *,
    intent: InstapayIntent,
    proof: PaymentProof,
    config: AutoApprovalConfig,
    facts: AutoApprovalFacts,
) -> AutoApprovalDecision:
    """Return the auto-approval decision for a freshly submitted proof.

    Rules, in order:
      1. Intent not expired (hard block — customer should re-start).
      2. Amount ≤ threshold (soft — route to review).
      3. Declared amount matches order total within tolerance (soft).
      4. Daily count + amount caps not exceeded (soft — route to review).

    Only when none of the above trip does the proof auto-approve.
    """
    reasons: list[str] = []
    hard_block = False

    if intent.is_expired(now=facts.now):
        reasons.append("intent_expired")
        hard_block = True

    if facts.order_total_cents > config.threshold_cents:
        reasons.append("amount_above_auto_approve_threshold")

    # Amount-mismatch — only evaluated when the storefront captured a
    # declared amount AND the merchant left the tolerance enabled.
    # Rule: |declared - order_total| / order_total > tolerance_bps/10000
    # → soft-route to merchant review. A straight equality check would
    # reject legitimate variance from bank fees / rounding, so we
    # express tolerance in bps for sensible defaults (100 bps = 1%).
    if (
        config.amount_mismatch_tolerance_bps is not None
        and proof.declared_amount_cents is not None
        and facts.order_total_cents > 0
    ):
        diff = abs(proof.declared_amount_cents - facts.order_total_cents)
        tolerance_cents = (
            facts.order_total_cents * config.amount_mismatch_tolerance_bps
        ) // 10_000
        if diff > tolerance_cents:
            reasons.append("declared_amount_mismatch")

    # Daily cap applies even when this single order is below threshold:
    # if the merchant has already auto-approved the full daily budget,
    # further proofs fall to manual review.
    projected_cents = facts.daily_auto_approved_cents + facts.order_total_cents
    if facts.daily_auto_approved_count >= config.daily_count_cap:
        reasons.append("daily_auto_approve_count_exceeded")
    if projected_cents > config.daily_cap_cents:
        reasons.append("daily_auto_approve_amount_exceeded")

    if not reasons:
        return AutoApprovalDecision(approved=True, reasons=[], soft_block=False)

    return AutoApprovalDecision(
        approved=False,
        reasons=reasons,
        soft_block=not hard_block,
    )
