"""Sensitive actions registry requiring step-up verification.

These permission codes require elevated verification (2FA) before execution.
Located in a central place for enforcement by require_step_up dependency.
"""

SENSITIVE_ACTIONS = {
    "orders.refund",
    "orders.export",
    "products.edit.price",
    "products.delete",
    "customers.export",
    "customers.delete",
    "analytics.export",
    "analytics.financial.view",
    "marketing.campaigns.send",
    "settings.domain.edit",
    "settings.payments.edit",
    "billing.manage",
    "billing.transfer_ownership",
    "staff.remove",
    "staff.roles.edit",
    "themes.publish",
}

ACTION_CONFIRMATION_STRINGS = {
    "customers.delete": "DELETE",
    "billing.transfer_ownership": "TRANSFER",
}


def is_sensitive_action(code: str) -> bool:
    """Check if permission code requires step-up verification."""
    return code in SENSITIVE_ACTIONS


def requires_confirmation(code: str) -> bool:
    """Check if permission code requires typed confirmation."""
    return code in ACTION_CONFIRMATION_STRINGS


def get_confirmation_message(code: str) -> str | None:
    """Get confirmation message for permission code."""
    return ACTION_CONFIRMATION_STRINGS.get(code)


def get_step_up_age_limit(code: str, default: int = 300) -> int:
    """Get max age in seconds since last 2FA verification.

    HIGH risk: 5 minutes (300s)
    CRITICAL risk: immediate (0s)
    """
    if code == "billing.transfer_ownership":
        return 0
    if code in ("customers.delete", "staff.remove"):
        return 0
    return default
