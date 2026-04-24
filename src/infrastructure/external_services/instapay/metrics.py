"""InstaPay-specific metric definitions.

Kept in one place so the dashboard definitions (and the migration path
to real Prometheus later) have a single source of truth for metric
names, labels, and descriptions. Call sites import the module-level
instances directly.
"""

from __future__ import annotations

from src.infrastructure.observability import counter, timer

# How many proofs came in, split by final status. Labels let dashboards
# build conversion funnels (submitted → auto_approved vs awaiting_review)
# and per-store heat maps.
proof_submissions_total = counter(
    "instapay_proof_submissions_total",
    description="InstaPay proofs submitted, labelled by status + store.",
    labels=["status", "store_id"],
)

# Fired every time the rules engine soft-routes to review. The reason
# label lets us see which rule trips most often — if
# ``daily_auto_approve_count_exceeded`` dominates, the merchant's cap
# is too tight; if ``amount_above_auto_approve_threshold`` dominates,
# the threshold is.
proof_autoapprove_blocks_total = counter(
    "instapay_autoapprove_blocks_total",
    description="Auto-approval soft-blocks, labelled by reason + store.",
    labels=["reason", "store_id"],
)

# Merchant decision latency: upload timestamp → approve/reject
# timestamp. Useful for SLA alerting (orders sitting in review > 24h).
proof_review_latency_seconds = timer(
    "instapay_proof_review_latency_seconds",
    description=(
        "Seconds between proof upload and merchant decision "
        "(approve + reject). Auto-approvals emit 0."
    ),
    labels=["decision", "store_id"],
)

# Background sweeper outcomes. Useful to watch for a stuck sweeper or
# a sudden spike in escalations (merchants going offline for a week).
sweeper_runs_total = counter(
    "instapay_sweeper_runs_total",
    description="Expiry sweeper tick outcomes, labelled by bucket.",
    labels=["bucket"],
)
