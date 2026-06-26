"""Domain-scope guardrail (Constitution VIII, research R10).

Three layers protect the agent's identity: (1) the system-prompt persona, (2) the
tool boundary (only NUMU store/knowledge tools exist), and (3) this lightweight
pre-check that short-circuits clearly off-domain requests with a polite redirect —
without spending a model call or touching open-domain knowledge.

The pre-check is intentionally NARROW (high precision) so it never blocks a real
store question; the system prompt remains the primary guard for subtler cases.
"""

from __future__ import annotations

import re

# Patterns that signal an off-NUMU / general-assistant request or a jailbreak.
_OFF_DOMAIN_PATTERNS = [
    re.compile(
        r"\b(write|generate|create|build)\s+(me\s+)?(a|an|some)?\s*(python|javascript|js|java|c\+\+|c#|php|ruby|go|rust|bash|shell|sql|html|css|code|script|program|function|regex)\b",
        re.I,
    ),
    re.compile(r"\bact\s+(like|as)\s+(chatgpt|gpt|an?\s+ai|a\s+general)\b", re.I),
    re.compile(
        r"\b(ignore|disregard|forget)\s+(your|the|all|previous)\s+(rules?|instructions?|system\s*prompt|guidelines?)\b",
        re.I,
    ),
    re.compile(r"\bpretend\s+(you('?re|\s+are)|to\s+be)\b", re.I),
    re.compile(
        r"\b(write|compose|draft)\s+(me\s+)?(a|an)\s+(essay|poem|story|song|novel|article)\b",
        re.I,
    ),
    re.compile(r"\btell\s+me\s+a\s+joke\b", re.I),
    re.compile(r"\b(solve|do)\s+my\s+(homework|math)\b", re.I),
]

_DECLINE_EN = (
    "I'm your NUMU store assistant — I can help with your store and using NUMU "
    "(orders, inventory, products, your theme, and how-to questions about the platform). "
    "I can't help with that. What would you like to do for your store?"
)
_DECLINE_AR = (
    "أنا مساعد متجرك على NUMU — بقدر أساعدك في متجرك وفي استخدام NUMU "
    "(الأوردرات، المخزون، المنتجات، الثيم، وأسئلة عن المنصة). "
    "مش هقدر أساعد في ده. عايز تعمل إيه في متجرك؟"
)


def off_domain_reason(message: str) -> str | None:
    """Return a short reason if the message is clearly off-domain, else None."""
    text = message or ""
    for pattern in _OFF_DOMAIN_PATTERNS:
        if pattern.search(text):
            return "off_domain_request"
    return None


def decline_message(locale: str) -> str:
    return _DECLINE_AR if locale == "ar" else _DECLINE_EN
