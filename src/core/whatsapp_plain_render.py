"""Render a system WhatsApp template to PLAIN TEXT.

The Meta Cloud transport sends a *template reference* — a name, a language and
positional parameters — and Meta holds the approved copy. Any transport that
isn't Meta has no such store, so it has to send the finished message. This
module produces that text from the two definitions we already keep:

* ``EGYPTIAN_TEMPLATES`` (``core.interfaces.services.messaging_service``) —
  which named parameters fill each component, **in order**.
* ``RICH_TEMPLATES`` (``core.whatsapp_rich_templates``) — the body copy with
  ``{{n}}`` placeholders, the footer, and the button definitions.

Keeping both as the input is deliberate: the placeholder order in RICH_TEMPLATES
is already documented as having to match the parameter order in
EGYPTIAN_TEMPLATES, so rendering from the same pair means a template can never
say one thing over Meta and another over a plain-text transport.

## Buttons become numbered replies

whatsmeow — and therefore GOWA — cannot send interactive quick-reply buttons.
The COD confirm flow depends on them: it reads ``type == "button"`` and a
``button.payload`` of ``<action>:<subdomain>/<order_id>``. So on a plain-text
transport the buttons are rendered as a numbered list and the customer replies
with a digit, which the inbound webhook maps back to the same action. The
numbering follows the template's own button ORDER, so "1" means whatever the
first button meant on Meta and the two transports stay behaviourally identical.

URL buttons are rendered as the resolved link on its own line — a tappable link
is the closest plain-text equivalent of a URL CTA.
"""

from __future__ import annotations

import re
from typing import Any

from src.core.interfaces.services.messaging_service import (
    EGYPTIAN_TEMPLATES,
    MessageType,
)
from src.core.whatsapp_rich_templates import RICH_TEMPLATES

__all__ = [
    "PlainMessage",
    "quick_reply_actions",
    "render_plain_template",
]

# Copy for templates that exist at Meta but predate RICH_TEMPLATES, so they have
# no local body text.
#
# Deliberately NOT added to RICH_TEMPLATES: that list has two other consumers —
# the seed migration and `scripts/submit_platform_whatsapp_templates.py`, which
# POSTs every entry to Meta. These templates are already live there, so adding
# them would make the submission script try to re-create existing ones. This
# registry is read only by the plain-text renderer.
#
# CAVEAT: the wording below was authored here, not exported from Meta, so it may
# not be word-for-word identical to the approved Meta body. It matches the
# template's meaning and parameter order; if exact parity matters, replace it
# with the live body text pulled from the WABA template list.
_PLAIN_ONLY_TEMPLATES: list[dict[str, Any]] = [
    {
        "name": "out_for_delivery_en",
        "language": "en",
        # {{1}} customer_name, {{2}} order_number — must match the component
        # parameter order in EGYPTIAN_TEMPLATES[OUT_FOR_DELIVERY]["en"].
        "body": (
            "Hi {{1}} 👋 Your order {{2}} is *out for delivery* today. 🚚\n\n"
            "Our courier will reach you shortly — please keep your phone nearby."
        ),
        "footer": "Thank you for shopping with us.",
        "buttons": [],
    },
    {
        "name": "out_for_delivery_ar",
        "language": "ar",
        "body": (
            "أهلاً يا {{1}} 👋 طلبك {{2}} *خرج للتوصيل* النهاردة. 🚚\n\n"
            "المندوب هيوصلك قريب — خلي تليفونك جنبك من فضلك."
        ),
        "footer": "شكراً لتسوقك معنا.",
        "buttons": [],
    },
]

# (name, language) -> definition. Built once; both sources are module-level
# constants that never mutate at runtime.
_RICH_BY_KEY: dict[tuple[str, str], dict[str, Any]] = {
    (t["name"], t["language"]): t for t in (*RICH_TEMPLATES, *_PLAIN_ONLY_TEMPLATES)
}

_PLACEHOLDER = re.compile(r"\{\{(\d+)\}\}")

# Prompt that introduces the numbered options, per language. Kept here rather
# than in the template copy because the buttons only exist as a numbered list on
# this transport — the Meta rendering of the same template must not show it.
_REPLY_PROMPT = {
    "en": "Reply with a number:",
    "ar": "رد برقم:",
}

# Meta's approved copy tells the customer to TAP A BUTTON, because on Meta there
# is one. On this transport there isn't — the message ends in a numbered list —
# so that wording reads as a broken app: "tap Confirm Order" with nothing to
# tap. Verified live: the first real send said "tap a button below" above a
# numbered list.
#
# These rewrites apply ONLY when the template actually has quick-reply buttons
# that we degraded to numbers, so a template with no buttons is never touched.
# The Meta path keeps its original copy — its buttons are real.
_BUTTON_PHRASE_REWRITES: dict[str, tuple[tuple[str, str], ...]] = {
    "en": (
        ("and tap a button below", "and reply with a number below"),
        ("and tap a button", "and reply with a number"),
        ("tap a button below", "reply with a number below"),
        ("Tap *Confirm Order*", "Reply *1*"),
        ("Tap *Track order*", "Open the link"),
        ("tap the button below", "reply with a number below"),
    ),
    "ar": (
        ("واضغط أحد الأزرار", "ورد برقم"),
        ("اضغط أحد الأزرار", "رد برقم"),
        ("اضغط *تأكيد الطلب*", "رد بـ *1*"),
    ),
}


def _degrade_button_phrases(body: str, language: str) -> str:
    """Rewrite 'tap a button' wording for a transport that has no buttons."""
    for needle, replacement in _BUTTON_PHRASE_REWRITES.get(language, ()):
        body = body.replace(needle, replacement)
    return body


class PlainMessage:
    """A rendered message plus the reply mapping it expects back.

    ``quick_replies`` maps the digit shown to the customer onto the button
    index in the original template.

    ``quick_reply_payloads`` maps that same digit onto the **exact payload
    string** Meta would have delivered had the customer tapped the button —
    e.g. ``"confirm:nile/abc123"``. Those values already exist in
    ``template_params`` (``confirm_payload``, ``postpone_payload``, …) because
    the Meta path sends them as the button parameters, so this is a lookup, not
    a reconstruction. Persisting them against the recipient lets the inbound
    webhook hand the existing COD handlers byte-identical input, which is why
    the two transports can share those handlers unchanged.
    """

    __slots__ = ("text", "quick_replies", "quick_reply_payloads")

    def __init__(
        self,
        text: str,
        quick_replies: dict[str, int],
        quick_reply_payloads: dict[str, str] | None = None,
    ) -> None:
        self.text = text
        self.quick_replies = quick_replies
        self.quick_reply_payloads = quick_reply_payloads or {}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"PlainMessage(text={self.text[:40]!r}…, quick_replies={self.quick_replies})"


def _resolve_lang(message_type: MessageType, language: str) -> tuple[Any, str] | None:
    """Pick the template entry for ``language``, falling back to English.

    Mirrors how the Meta path resolves language so a store that sends Arabic
    over one transport doesn't silently get English over the other.
    """
    langs = EGYPTIAN_TEMPLATES.get(message_type)
    if not langs:
        return None
    # Accept "ar-EG"/"en_US" style codes by matching on the leading subtag.
    base = (language or "en").replace("_", "-").split("-")[0].lower()
    for candidate in (language, base, "en"):
        if candidate and candidate in langs:
            return langs[candidate], candidate
    # Any entry is better than nothing — a message in the wrong language beats
    # no message at all on an order-lifecycle notification.
    first = next(iter(langs.items()), None)
    return (first[1], first[0]) if first else None


def _ordered_body_values(
    template_components: list[dict[str, Any]],
    parameters: dict[str, Any],
) -> list[str]:
    """Positional body values, resolved exactly as the Meta payload builder does.

    Same rule as ``_build_template_message``: walk the component's named
    ``parameters`` list in order and look each one up. A key that is missing is
    skipped there, so it is skipped here too — otherwise the placeholder
    numbering would shift between transports and every ``{{n}}`` after the gap
    would render the wrong value.
    """
    for comp in template_components:
        if (comp.get("type") or "body") == "body":
            keys = comp.get("parameters") or []
            return [str(parameters[k]) for k in keys if k in parameters]
    return []


def _fill(body: str, values: list[str]) -> str:
    """Substitute ``{{1}}``-style placeholders with positional values."""

    def sub(match: re.Match[str]) -> str:
        idx = int(match.group(1)) - 1
        # Out-of-range placeholder → empty string rather than a literal
        # "{{4}}" leaking to a customer.
        return values[idx] if 0 <= idx < len(values) else ""

    return _PLACEHOLDER.sub(sub, body)


def _url_for_button(
    button: dict[str, Any],
    template_components: list[dict[str, Any]],
    parameters: dict[str, Any],
    button_index: int,
) -> str | None:
    """Resolve a URL button's href, substituting its own ``{{1}}`` parameter."""
    url = button.get("url")
    if not url:
        return None
    for comp in template_components:
        if comp.get("type") != "button":
            continue
        if comp.get("sub_type", "url") != "url":
            continue
        if int(comp.get("index", 0) or 0) != button_index:
            continue
        keys = comp.get("parameters") or []
        values = [str(parameters[k]) for k in keys if k in parameters]
        return _fill(url, values)
    return url


def _quick_reply_payload(
    template_components: list[dict[str, Any]],
    parameters: dict[str, Any],
    button_index: int,
) -> str | None:
    """The payload Meta would have sent for the quick-reply at ``button_index``.

    The Meta path passes these as the button component's parameters (see
    ``_build_template_message``), so the value is already in ``parameters``
    under a name like ``confirm_payload``. Reading it here rather than
    rebuilding it means a plain-text reply resolves to exactly the same string
    a button tap would have produced.
    """
    for comp in template_components:
        if comp.get("type") != "button":
            continue
        if comp.get("sub_type") != "quick_reply":
            continue
        if int(comp.get("index", 0) or 0) != button_index:
            continue
        for key in comp.get("parameters") or []:
            if key in parameters:
                return str(parameters[key])
    return None


def render_plain_template(
    message_type: MessageType,
    language: str,
    parameters: dict[str, Any],
) -> PlainMessage | None:
    """Render ``message_type`` to plain text, or None when we have no copy.

    Returning None is meaningful: it means this template has no body text on
    our side (it lives only in Meta's store), so a plain-text transport must
    NOT invent one. The caller reports a failed send instead of delivering
    something the merchant never approved.
    """
    resolved = _resolve_lang(message_type, language)
    if not resolved:
        return None
    template, lang = resolved

    rich = _RICH_BY_KEY.get((template.name, template.language))
    if not rich:
        return None

    components = template.components or []
    body = _fill(rich.get("body") or "", _ordered_body_values(components, parameters))

    parts: list[str] = [body.strip()]

    # Buttons → numbered replies (quick reply) or a link (URL).
    quick_replies: dict[str, int] = {}
    quick_reply_payloads: dict[str, str] = {}
    numbered: list[str] = []
    for idx, button in enumerate(rich.get("buttons") or []):
        btype = (button.get("type") or "").upper()
        label = button.get("text") or ""
        if btype == "QUICK_REPLY":
            digit = str(len(quick_replies) + 1)
            quick_replies[digit] = idx
            payload = _quick_reply_payload(components, parameters, idx)
            if payload is not None:
                quick_reply_payloads[digit] = payload
            numbered.append(f"{digit}) {label}")
        elif btype == "URL":
            url = _url_for_button(button, components, parameters, idx)
            if url:
                parts.append(f"{label}: {url}" if label else url)

    if numbered:
        # Only now do we know the body promised buttons we cannot deliver.
        parts[0] = _degrade_button_phrases(parts[0], lang)
        prompt = _REPLY_PROMPT.get(lang, _REPLY_PROMPT["en"])
        parts.append(prompt + "\n" + "\n".join(numbered))

    footer = (rich.get("footer") or "").strip()
    if footer:
        parts.append(footer)

    text = "\n\n".join(p for p in parts if p)
    return PlainMessage(
        text=text,
        quick_replies=quick_replies,
        quick_reply_payloads=quick_reply_payloads,
    )


def quick_reply_actions(message_type: MessageType, language: str) -> list[str]:
    """Button labels for ``message_type``, in template order.

    Exposed so the inbound webhook can turn a digit back into the action
    without re-rendering the whole message.
    """
    resolved = _resolve_lang(message_type, language)
    if not resolved:
        return []
    template, _ = resolved
    rich = _RICH_BY_KEY.get((template.name, template.language))
    if not rich:
        return []
    return [
        b.get("text") or ""
        for b in (rich.get("buttons") or [])
        if (b.get("type") or "").upper() == "QUICK_REPLY"
    ]
