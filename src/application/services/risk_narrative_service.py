"""Risk narrative generation service (backend-024 / spec 011).

Wraps the deterministic ``RiskAssessment.factors[]`` output with a
2-3 sentence Arabic-first explanation generated via the existing
LLM client (OpenAI-compatible, points at Google AI Studio for Gemini
or any other compatible endpoint).

PII tokenization is **entity-driven, not regex-driven** per spec 011
CL-002: known PII values from the assessment's order/customer are
replaced with `<customer>` / `<phone>` / `<address>` / `<order>`
sentinels before the prompt is constructed. A belt-and-suspenders
post-generation regex scan rejects any leftover PII (E.164 phone +
Egyptian local mobile + 7+ consecutive digits + literal substrings of
known entity values).

Constitution Principle IV: this is a *narration* layer, not a
*scoring* layer. The numeric ``risk_score`` and ``risk_level`` come
from the deterministic factor engine in ``risk_scoring_engine.py``;
the LLM only describes them in plain language.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

from src.config import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — purpose-specific prompts + length bounds (spec 011 FR-007/008)
# ---------------------------------------------------------------------------

NarrativePurpose = Literal["merchant_dashboard", "customer_recovery_personalization"]

# 40-200 words EN ≈ 280-1400 chars. Customer purpose is ≤ 100 chars.
LENGTH_BOUNDS_CHARS = {
    "merchant_dashboard": (80, 1400),
    "customer_recovery_personalization": (10, 100),
}


# Tokenization sentinels — used in both prompt construction and
# post-generation PII scan.
SENTINEL_CUSTOMER = "<customer>"
SENTINEL_PHONE = "<phone>"
SENTINEL_ADDRESS = "<address>"
SENTINEL_ORDER = "<order>"
SENTINEL_EMAIL = "<email>"

# Belt-and-suspenders regexes for the post-generation scan.
_E164_RE = re.compile(r"\+\d{8,15}")
_EG_LOCAL_MOBILE_RE = re.compile(r"\b01[0125]\d{8}\b")
_DIGIT_RUN_RE = re.compile(r"\d{7,}")


# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------


@dataclass
class NarrativeFactor:
    """A single factor row passed into the LLM prompt.

    Mirrors ``RiskFactor`` from the scoring engine but drops the raw
    reason string in favour of an already-tokenized variant so the
    caller has done its PII work before this struct enters the prompt.
    """

    name: str
    score: int  # 0-100
    weight: float  # 0-1
    reason_tokenized: str  # PII-stripped version of the original reason


@dataclass
class EntityValuesForTokenization:
    """The set of known PII values to substitute before the LLM call.

    All optional — the caller passes whatever is on the assessment's
    linked order/customer. Empty/None values are skipped.
    """

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
    narrative_en: str | None
    narrative_ar: str | None
    model_version: str | None = None
    generation_id: str | None = None
    failure_reason: str | None = None  # Set when both narratives are None


# ---------------------------------------------------------------------------
# PII tokenization (entity-driven per spec 011 CL-002)
# ---------------------------------------------------------------------------


def _build_substitutions(
    entities: EntityValuesForTokenization,
) -> list[tuple[str, str]]:
    """Build the ordered list of (literal, sentinel) pairs.

    Order matters: longer strings substituted first so a name "Mohammed"
    is replaced before a partial match like "moh" might appear in another
    pass. Sentinels themselves (``<customer>`` etc.) are never substituted.
    """
    pairs: list[tuple[str, str]] = []

    def _push(value: str | None, sentinel: str) -> None:
        if value and len(value) >= 2:
            pairs.append((value, sentinel))

    full_name = " ".join(
        n for n in (entities.customer_first_name, entities.customer_last_name) if n
    ).strip()

    _push(full_name or None, SENTINEL_CUSTOMER)
    _push(entities.customer_first_name, SENTINEL_CUSTOMER)
    _push(entities.customer_last_name, SENTINEL_CUSTOMER)
    _push(entities.customer_email, SENTINEL_EMAIL)
    _push(entities.customer_phone, SENTINEL_PHONE)
    _push(entities.shipping_address1, SENTINEL_ADDRESS)
    _push(entities.shipping_address2, SENTINEL_ADDRESS)
    _push(entities.shipping_city, SENTINEL_ADDRESS)
    _push(entities.shipping_country, SENTINEL_ADDRESS)
    _push(entities.order_number, SENTINEL_ORDER)
    _push(entities.shopify_order_id, SENTINEL_ORDER)

    # Sort longest-first so longer literals win over shorter substrings.
    pairs.sort(key=lambda p: -len(p[0]))
    return pairs


def tokenize_pii(text: str, entities: EntityValuesForTokenization) -> str:
    """Replace every known PII value in ``text`` with its sentinel.

    Case-insensitive, whitespace-normalised. Pure function — easy to
    snapshot-test against canonical inputs (spec 011 CL-002).
    """
    if not text:
        return text
    out = text
    for literal, sentinel in _build_substitutions(entities):
        # Case-insensitive replace using regex with escape for literal safety.
        pattern = re.compile(re.escape(literal), re.IGNORECASE)
        out = pattern.sub(sentinel, out)
    # Belt-and-suspenders phone regex pass.
    out = _E164_RE.sub(SENTINEL_PHONE, out)
    out = _EG_LOCAL_MOBILE_RE.sub(SENTINEL_PHONE, out)
    return out


def detect_residual_pii(
    narrative: str, entities: EntityValuesForTokenization
) -> str | None:
    """Return the offending substring if PII slipped through; None if clean.

    Per spec 011 FR-006: any substring ≥ 4 chars of a known entity value
    counts as PII; phone-shaped digit runs also count. Used as a
    post-generation guard — the narrative is discarded on hit.
    """
    if not narrative:
        return None

    if _E164_RE.search(narrative):
        return "e164_phone"
    if _EG_LOCAL_MOBILE_RE.search(narrative):
        return "eg_local_phone"
    if _DIGIT_RUN_RE.search(narrative):
        return "digit_run"

    text = narrative.lower()
    for literal, _sentinel in _build_substitutions(entities):
        # Skip very short literals (would false-positive on common words).
        if len(literal) < 4:
            continue
        if literal.lower() in text:
            return f"literal_match:{literal[:8]}…"
    return None


# ---------------------------------------------------------------------------
# Narrative generation
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT_MERCHANT = (
    "You are an explainability layer for a deterministic fraud-risk scoring "
    "engine for Egyptian e-commerce merchants. Given the structured factor "
    "list below, write a 2-3 sentence summary in {language_label} that:\n"
    "  - leads with the operational takeaway "
    "(e.g., 'Worth confirming before shipping' / 'يستحق التأكيد قبل الشحن'),\n"
    "  - cites the dominant factors,\n"
    "  - never references any specific person, phone, address, or order id,\n"
    "  - stays neutral in tone, not accusatory.\n"
    "Output ONLY the narrative paragraph — no preamble, no factor listing.\n"
    "Sentinels like <customer>, <phone>, <address>, <order> in the inputs "
    "are PLACEHOLDERS — preserve their meaning ('the customer', 'a phone', "
    "etc.) without expanding them."
)

_SYSTEM_PROMPT_CUSTOMER = (
    "You are a polite, non-shaming customer-service voice. Given the risk "
    "factors below (which are about a specific buyer placing a high-risk "
    "COD order), write ONE phrase ≤ 100 characters in {language_label} "
    "that the merchant can include in a recovery message asking for "
    "confirmation or prepayment. Friendly, never accusatory. "
    "Output ONLY the phrase — no preamble, no factor reference."
)


async def generate_narrative(
    factors: list[NarrativeFactor],
    *,
    purpose: NarrativePurpose = "merchant_dashboard",
    language: str = "ar",
    entities: EntityValuesForTokenization | None = None,
) -> NarrativeResult:
    """Generate the EN+AR narrative pair for a risk assessment.

    On any failure (LLM unavailable, content-policy block, residual PII,
    length out-of-bounds), the corresponding ``narrative_*`` field is
    None — the dashboard falls back to the existing factor list per
    spec 011 FR-005.
    """
    settings = get_settings()
    api_key = getattr(settings, "google_ai_api_key", None)
    base_url = getattr(settings, "google_ai_base_url", None)
    if not api_key:
        return NarrativeResult(
            narrative_en=None,
            narrative_ar=None,
            failure_reason="llm_not_configured",
        )

    entities = entities or EntityValuesForTokenization()

    # Build the structured factor list — all already tokenized by caller.
    factor_lines = [
        f"- {f.name} (weight {int(f.weight * 100)}%, score {f.score}/100): "
        f"{f.reason_tokenized}"
        for f in factors
    ]
    factor_text = "\n".join(factor_lines)

    if purpose == "merchant_dashboard":
        system_template = _SYSTEM_PROMPT_MERCHANT
    else:
        system_template = _SYSTEM_PROMPT_CUSTOMER

    # Generate both EN and AR narratives in parallel for the merchant
    # dashboard purpose; the customer purpose only needs the requested
    # language (it's used inline in a templated message).
    languages = ("en", "ar") if purpose == "merchant_dashboard" else (language,)

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        outputs: dict[str, str] = {}
        for lang in languages:
            label = "Arabic (Egyptian register)" if lang == "ar" else "English"
            system_prompt = system_template.format(language_label=label)
            response = await client.chat.completions.create(
                model=getattr(settings, "ai_insights_model", "gemini-2.0-flash"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Risk factors:\n{factor_text}"},
                ],
                temperature=0.2,
                max_tokens=300 if purpose == "merchant_dashboard" else 60,
            )
            text = (response.choices[0].message.content or "").strip()
            outputs[lang] = text
    except Exception as exc:
        logger.warning("risk_narrative_llm_call_failed: %s", exc)
        return NarrativeResult(
            narrative_en=None,
            narrative_ar=None,
            failure_reason=f"llm_call_failed:{type(exc).__name__}",
        )

    # Validation pass — length + PII detection per spec 011 FR-006/007.
    lo, hi = LENGTH_BOUNDS_CHARS[purpose]
    final_en: str | None = outputs.get("en")
    final_ar: str | None = outputs.get("ar")

    def _validate(text: str | None) -> str | None:
        if text is None:
            return None
        if not (lo <= len(text) <= hi):
            return None
        if detect_residual_pii(text, entities) is not None:
            return None
        return text

    final_en = _validate(final_en) if "en" in outputs else None
    final_ar = _validate(final_ar) if "ar" in outputs else None

    # Spec 011 CL-005: per-locale partial success NOT allowed for
    # merchant_dashboard — both succeed atomically or both NULL.
    if purpose == "merchant_dashboard":
        if final_en is None or final_ar is None:
            return NarrativeResult(
                narrative_en=None,
                narrative_ar=None,
                failure_reason="atomic_locale_failure",
            )

    return NarrativeResult(
        narrative_en=final_en,
        narrative_ar=final_ar,
        model_version=getattr(settings, "ai_insights_model", "gemini-2.0-flash"),
    )
