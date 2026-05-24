"""Pre-send guard for every WhatsApp outbound message (FR-036–FR-038).

Pure logic — no I/O. All lookups (opt-in status, notification settings,
template approval state, 24h window) are passed in by callers (the
messaging service or scheduled-send dispatcher).

Two-tier opt-in policy per FR-011 / clarification Q1:
  * Utility & Authentication templates → opt-out respected, opt-in NOT
    required.
  * Marketing templates → opt-in required AND opt-out respected.
  * Free-form (non-template) → opt-out respected only.

Bypass allowlist (TASK-SEC-010) — exactly two template names may send
even without an active opt-in row AND even with an explicit opt-out: the
STOP-acknowledgement replies. Nothing else can bypass.
"""

from dataclasses import dataclass

from src.core.enums.whatsapp import SendSkipReason, TemplateCategory

# TASK-SEC-010 — defense-in-depth allowlist constant. Any other code path
# that tries to bypass the guard at runtime MUST go through this set; tests
# assert this set is exactly the two STOP-ack templates.
OPT_IN_BYPASS_ALLOWLIST: frozenset[str] = frozenset({
    "optout_confirmation_en",
    "optout_confirmation_ar",
})


@dataclass(frozen=True)
class GuardContext:
    """All inputs the guard needs to decide. Pure data — never reads a repo.

    Callers prefetch and pass in. This keeps the guard testable as a
    truth-table.
    """

    # Outbound message metadata
    phone: str | None
    """The recipient's E.164 phone (None or empty triggers NO_PHONE)."""

    template_name: str | None
    """If a template-based send: the local template name. None for free-form."""

    template_category: TemplateCategory | None
    """Required when ``template_name`` is set. Drives the two-tier policy."""

    template_status: str | None
    """The local ``whatsapp_templates.status`` for ``template_name``.
    Required when ``template_name`` is set. Anything other than ``APPROVED``
    rejects the send (FR-029)."""

    # Store-side state
    store_has_credentials: bool
    """``True`` if the resolver found valid platform or BYO credentials."""

    store_credentials_marked_invalid: bool
    """``True`` if the store's WhatsApp status is in credential-error state
    (FR-025)."""

    notification_setting_enabled: bool
    """The per-message-type toggle for this event (FR-019a). For free-form
    sends triggered by inbound conversation, this is conventionally True."""

    # Customer-side state
    has_active_opt_in: bool
    """``True`` if a ``whatsapp_opt_ins`` row exists for (store, phone) with
    ``opted_out_at IS NULL``."""

    has_opt_out: bool
    """``True`` if any ``whatsapp_opt_ins`` row exists for (store, phone)
    with ``opted_out_at IS NOT NULL``. Explicit opt-out is honoured for
    every category — including utility and authentication — unless the
    template is in ``OPT_IN_BYPASS_ALLOWLIST``."""

    window_is_open: bool
    """For free-form (non-template) sends: True if the 24h customer-service
    window is still open. Ignored for template sends."""

    # Idempotency
    already_sent: bool
    """``True`` if ``message_log`` already has a successful send for the
    same idempotency key (e.g., ``order_id + event_type``). Drives the
    ``already_sent`` skip reason for replay-safety (FR-005)."""


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reason: SendSkipReason | None


def check(ctx: GuardContext) -> GuardDecision:
    """Run the guard. Order matches FR-037 exactly.

    Returns the first failing reason encountered (short-circuit).
    """
    # Bypass — STOP-acknowledgement replies.
    bypass = (
        ctx.template_name is not None and ctx.template_name in OPT_IN_BYPASS_ALLOWLIST
    )

    # (a) Phone present + parseable.
    if not ctx.phone:
        return GuardDecision(False, SendSkipReason.NO_PHONE)
    if not _looks_like_e164(ctx.phone):
        return GuardDecision(False, SendSkipReason.INVALID_PHONE)

    # (b) Credentials.
    if not ctx.store_has_credentials:
        return GuardDecision(False, SendSkipReason.NO_CREDENTIALS)
    if ctx.store_credentials_marked_invalid:
        return GuardDecision(False, SendSkipReason.CREDENTIALS_INVALID)

    # (c) Merchant notification setting (skipped for bypass-allowlist sends —
    # the STOP-ack must always go out).
    if not bypass and not ctx.notification_setting_enabled:
        return GuardDecision(False, SendSkipReason.MERCHANT_SETTING_OFF)

    # (d) Explicit opt-out — always enforced except for the bypass allowlist.
    if not bypass and ctx.has_opt_out:
        return GuardDecision(False, SendSkipReason.OPT_OUT)

    # (e) Marketing-only active opt-in requirement.
    if not bypass and ctx.template_category == TemplateCategory.MARKETING:
        if not ctx.has_active_opt_in:
            return GuardDecision(False, SendSkipReason.NO_OPT_IN)

    # (f) 24h window for non-template (free-form) sends. Template sends
    # have no window restriction at WhatsApp level.
    if not bypass and ctx.template_name is None and not ctx.window_is_open:
        return GuardDecision(False, SendSkipReason.WINDOW_CLOSED)

    # (g) Template must be APPROVED — FR-029 / analyze finding C1.
    # Applies to every category; the bypass allowlist is checked first so
    # the STOP-ack templates (which are seeded as APPROVED system rows in
    # the migration) pass this check naturally.
    if ctx.template_name is not None and ctx.template_status != "APPROVED":
        return GuardDecision(False, SendSkipReason.TEMPLATE_NOT_APPROVED)

    # Idempotency last — cheapest to check after structural guards pass.
    if ctx.already_sent:
        return GuardDecision(False, SendSkipReason.ALREADY_SENT)

    return GuardDecision(True, None)


def _looks_like_e164(phone: str) -> bool:
    """Light E.164 shape check. The canonicalization is the canonical
    enforcer; this is a defense-in-depth check inside the guard so any
    upstream slip is caught.
    """
    if not phone.startswith("+"):
        return False
    digits = phone[1:]
    if not digits.isdigit():
        return False
    return 8 <= len(digits) <= 15
