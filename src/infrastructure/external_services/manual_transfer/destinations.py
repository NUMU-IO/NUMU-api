"""Validation + normalization for manual-rail destination strings.

Each manual rail publishes a different kind of "send money here" string:

  * **InstaPay** — an IPA, ``handle@bank`` (e.g. ``merchant@cib``).
  * **Vodafone Cash** — an Egyptian mobile-wallet number (``010…``).

This module is the single place that knows those shapes. It exists
because the pre-existing ``VodafoneCashValidator`` in
``gateway_validators/payment_validators.py`` modelled Vodafone Cash as
an API gateway (``merchant_id`` / ``api_key`` / ``pin``) — a product
that requires a commercial partnership and an aggregator, and one NUMU
does not use. Validating a wallet number is what actually applies.
"""

from __future__ import annotations

import re

from src.core.entities.instapay import ManualPaymentMethod

# Vodafone Egypt holds the 010 prefix. Accepted input forms:
#   01012345678, 1012345678, +201012345678, 00201012345678
# and the same with spaces / hyphens / Arabic-Indic digits.
_VODAFONE_LOCAL_RE = re.compile(r"^010\d{8}$")

# Arabic-Indic (U+0660..) and Eastern Arabic-Indic (U+06F0..) digits.
# Merchants copy their number out of the Ana Vodafone app, which
# renders Arabic-Indic digits on an Arabic UI.
_ARABIC_DIGITS = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)

_IPA_RE = re.compile(r"^[A-Za-z0-9._-]{2,}@[A-Za-z][A-Za-z0-9-]{1,30}$")


class InvalidDestinationError(ValueError):
    """The merchant-supplied destination isn't valid for the rail."""


def normalize_wallet_number(raw: str) -> str:
    """Return an Egyptian Vodafone wallet number as ``010XXXXXXXX``.

    Normalizing on the way in (rather than storing whatever the
    merchant typed) matters for two downstream consumers: the OCR
    wallet-number match rule compares against this string, and the
    customer-facing instructions panel shows it as a tap-to-copy value
    that has to be dialable as-is.

    Raises :class:`InvalidDestinationError` on anything that isn't a
    Vodafone Egypt mobile number.
    """
    if not raw:
        raise InvalidDestinationError("A Vodafone Cash wallet number is required.")

    digits = re.sub(r"\D", "", raw.translate(_ARABIC_DIGITS))

    # Strip the international prefixes merchants habitually paste.
    if digits.startswith("0020"):
        digits = digits[4:]
    elif digits.startswith("20") and len(digits) == 12:
        digits = digits[2:]
    if not digits.startswith("0"):
        digits = "0" + digits

    if not _VODAFONE_LOCAL_RE.match(digits):
        raise InvalidDestinationError(
            "That doesn't look like a Vodafone Cash number. Egyptian "
            "Vodafone wallets start with 010 and are 11 digits "
            "(e.g. 01012345678)."
        )
    return digits


def normalize_ipa(raw: str) -> str:
    """Return a trimmed, lower-cased InstaPay address (``handle@bank``)."""
    if not raw:
        raise InvalidDestinationError("An InstaPay address (IPA) is required.")
    ipa = raw.strip().lower()
    if not _IPA_RE.match(ipa):
        raise InvalidDestinationError(
            "That doesn't look like an InstaPay address. IPAs are "
            "'name@bank' (e.g. merchant@cib)."
        )
    return ipa


def normalize_destination(method: ManualPaymentMethod, raw: str) -> str:
    """Dispatch to the right normalizer for ``method``."""
    if method is ManualPaymentMethod.VODAFONE_CASH:
        return normalize_wallet_number(raw)
    return normalize_ipa(raw)


def mask_destination(method: ManualPaymentMethod, value: str) -> str:
    """Partially mask a destination for merchant-facing config screens.

    Unlike an API key, a destination is not really a secret — every
    customer at checkout sees it. It is masked in the settings API only
    so a shoulder-surfed dashboard doesn't hand over the full string,
    and so the hub can tell "saved" from "empty" without echoing it.
    """
    if not value:
        return ""
    if method is ManualPaymentMethod.VODAFONE_CASH:
        # 010****5678 — enough for the merchant to recognise their own.
        return f"{value[:3]}{'*' * max(0, len(value) - 7)}{value[-4:]}"
    handle, _, bank = value.partition("@")
    if not bank:
        return f"{value[:2]}***"
    keep = handle[:2]
    return f"{keep}{'*' * max(1, len(handle) - 2)}@{bank}"
