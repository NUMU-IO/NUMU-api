"""Risk narrative service (backend-024).

Generates a 2-3 sentence Arabic-first explanation of a risk score, narrated
over the deterministic factor set returned by the risk scoring engine.

PII boundary: never pass raw customer names, phones, or addresses to the
LLM. Caller provides ``EntityValuesForTokenization``; this module replaces
each value with a sentinel (``<customer>``, ``<phone>``, ``<address>``,
``<order>``, ``<email>``) before the prompt is built. A regex pass after
substitution catches any residual PII the substitution missed.

This file is a re-scaffold of the original (lost) implementation. The
LLM-calling part is intentionally inert (returns a ``failure_reason``)
so the API surface boots while the Claude-prompt wiring is reauthored
against the spec.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

from src.config import get_settings

logger = logging.getLogger(__name__)


NarrativePurpose = Literal["merchant_dashboard", "customer_recovery_personalization"]


LENGTH_BOUNDS_CHARS: dict[NarrativePurpose, tuple[int, int]] = {
    "merchant_dashboard": (80, 360),
    "customer_recovery_personalization": (40, 240),
}


SENTINEL_CUSTOMER = "<customer>"
SENTINEL_PHONE = "<phone>"
SENTINEL_ADDRESS = "<address>"
SENTINEL_ORDER = "<order>"
SENTINEL_EMAIL = "<email>"


_E164_RE = re.compile(r"\+\d{8,15}")
_EG_LOCAL_MOBILE_RE = re.compile(r"0?1[0125]\d{8}")
_DIGIT_RUN_RE = re.compile(r"\d{7,}")


@dataclass(frozen=True)
class NarrativeFactor:
    name: str
    score: int
    weight: float
    reason_tokenized: str


@dataclass(frozen=True)
class EntityValuesForTokenization:
    customer_first_name: str | None = None
    customer_last_name: str | None = None
    customer_email: str | None = None
    customer_phone: str | None = None
    shipping_address1: str | None = None
    shipping_address2: str | None = None
    shipping_city: str | None = None
    shipping_country: str | None = None
    order_number: str | None = None
    shopify_order_id: str | None = None


@dataclass
class NarrativeResult:
    narrative_en: str | None = None
    narrative_ar: str | None = None
    model_version: str | None = None
    failure_reason: str | None = None


def _build_substitutions(
    entities: EntityValuesForTokenization,
) -> list[tuple[str, str]]:
    """Build a (raw, sentinel) substitution list ordered longest-first.

    Longest-first ordering prevents partial matches when one PII value is a
    substring of another (e.g., a first name that appears inside an
    address line).
    """
    pairs: list[tuple[str, str]] = []
    if entities.customer_first_name:
        pairs.append((entities.customer_first_name, SENTINEL_CUSTOMER))
    if entities.customer_last_name:
        pairs.append((entities.customer_last_name, SENTINEL_CUSTOMER))
    if entities.customer_email:
        pairs.append((entities.customer_email, SENTINEL_EMAIL))
    if entities.customer_phone:
        pairs.append((entities.customer_phone, SENTINEL_PHONE))
    if entities.shipping_address1:
        pairs.append((entities.shipping_address1, SENTINEL_ADDRESS))
    if entities.shipping_address2:
        pairs.append((entities.shipping_address2, SENTINEL_ADDRESS))
    if entities.shipping_city:
        pairs.append((entities.shipping_city, SENTINEL_ADDRESS))
    if entities.shipping_country:
        pairs.append((entities.shipping_country, SENTINEL_ADDRESS))
    if entities.order_number:
        pairs.append((entities.order_number, SENTINEL_ORDER))
    if entities.shopify_order_id:
        pairs.append((entities.shopify_order_id, SENTINEL_ORDER))
    # Longest-first
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return pairs


def tokenize_pii(reason: str, entities: EntityValuesForTokenization) -> str:
    """Replace known PII values with sentinels, then sweep for stragglers."""
    out = reason
    for raw, sentinel in _build_substitutions(entities):
        out = out.replace(raw, sentinel)
    # Belt-and-suspenders regex pass for digit-y residuals.
    out = _E164_RE.sub(SENTINEL_PHONE, out)
    out = _EG_LOCAL_MOBILE_RE.sub(SENTINEL_PHONE, out)
    out = _DIGIT_RUN_RE.sub(SENTINEL_PHONE, out)
    return out


def detect_residual_pii(text: str) -> str | None:
    """Return a label of the residual PII type if any is still present."""
    if _E164_RE.search(text):
        return "e164_phone"
    if _EG_LOCAL_MOBILE_RE.search(text):
        return "eg_local_mobile"
    if _DIGIT_RUN_RE.search(text):
        return "long_digit_run"
    return None


_SYSTEM_PROMPT_MERCHANT = (
    "You are NUMU, a COD-recovery analyst for an Egyptian merchant. "
    "Summarize the deterministic risk-factor set as a 2-3 sentence "
    "explanation in the requested language. Never speculate beyond the "
    "factors. Never include any sentinel markers (<customer>, <phone>, "
    "<address>, <order>, <email>) in your reply."
)


_SYSTEM_PROMPT_CUSTOMER = (
    "You are NUMU, writing a friendly, non-shaming reason a customer's "
    "order needs prepayment. 1-2 sentences, no fear language, no urgency. "
    "Refer to the customer in a warm, neutral way."
)


async def generate_narrative(
    factors: list[NarrativeFactor],
    purpose: NarrativePurpose,
    language: Literal["ar", "en"],
    entities: EntityValuesForTokenization | None = None,
) -> NarrativeResult:
    """Generate a narrative for the given factor set.

    The LLM-call wiring is intentionally inert in this scaffold (returns a
    ``failure_reason`` so the dashboard falls back to the factor list per
    spec 011 FR-005). Re-implement against the original spec when ready.
    """
    settings = get_settings()
    enabled = getattr(settings, "RISK_NARRATIVE_ENABLED", False)
    if not enabled:
        return NarrativeResult(
            failure_reason="narrative_disabled",
        )

    # Future: build prompt, call Claude with prompt caching, validate
    # response length against LENGTH_BOUNDS_CHARS[purpose], check
    # detect_residual_pii on the result, persist to RiskAssessment.
    logger.info(
        "narrative.scaffold",
        extra={"purpose": purpose, "language": language, "n_factors": len(factors)},
    )
    return NarrativeResult(failure_reason="narrative_scaffold_only")
