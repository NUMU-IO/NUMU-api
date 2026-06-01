"""Unit tests for the rich (Bosta-style) WhatsApp templates + button routing.

Guards the invariants that broke during rollout:
- placeholder count must equal the EGYPTIAN_TEMPLATES param count and the
  Meta body_examples count (a mismatch → Meta #132012 at send time);
- button labels must be plain (no emoji / newline / formatting / variables),
  or Meta rejects the submission with subcode 2388060;
- the confirm-request payload routing (confirm / postpone / cancel, with
  legacy no-prefix → confirm) the inbound webhook depends on.
"""

import re

import pytest

from src.application.services.order_confirmation_service import (
    _parse_order_id,
    parse_quick_reply_action,
)
from src.core.interfaces.services.messaging_service import (
    EGYPTIAN_TEMPLATES,
    MessageType,
)
from src.core.whatsapp_rich_templates import RICH_TEMPLATES
from src.infrastructure.external_services.whatsapp.messaging_service import (
    WhatsAppMessagingService,
    payment_label,
)

_PLACEHOLDER_RE = re.compile(r"\{\{(\d+)\}\}")

# Codepoint ranges that Meta treats as "emoji / pictographs" in button text.
# Arabic (0x0600–0x06FF) and Latin are intentionally NOT in here.
_EMOJI_RANGES = [
    (0x1F000, 0x1FAFF),  # pictographs, transport, symbols & emoji
    (0x2600, 0x27BF),  # misc symbols + dingbats
    (0x2190, 0x21FF),  # arrows
    (0xFE00, 0xFE0F),  # variation selectors
    (0x1F1E6, 0x1F1FF),  # regional indicators
    (0x2B00, 0x2BFF),  # misc symbols & arrows
]


def _has_emoji(text: str) -> bool:
    return any(any(lo <= ord(ch) <= hi for lo, hi in _EMOJI_RANGES) for ch in text)


def _name_to_template():
    out = {}
    for _mt, langs in EGYPTIAN_TEMPLATES.items():
        for _lang, tmpl in langs.items():
            out[tmpl.name] = tmpl
    return out


@pytest.mark.parametrize(
    "t", RICH_TEMPLATES, ids=lambda t: f"{t['name']}-{t['language']}"
)
def test_placeholder_param_example_alignment(t):
    """Body {{n}} count == EGYPTIAN_TEMPLATES body params == body_examples."""
    nums = [int(x) for x in _PLACEHOLDER_RE.findall(t["body"])]
    n_ph = max(nums) if nums else 0

    tmpl = _name_to_template().get(t["name"])
    assert tmpl is not None, f"{t['name']} missing from EGYPTIAN_TEMPLATES"
    body_params = next(
        (c.get("parameters") or [] for c in tmpl.components if c.get("type") == "body"),
        [],
    )
    assert n_ph == len(body_params), (
        f"{t['name']}: body has {n_ph} placeholders but EGYPTIAN_TEMPLATES "
        f"declares {len(body_params)} params"
    )
    assert n_ph == len(t.get("body_examples") or []), (
        f"{t['name']}: {n_ph} placeholders but "
        f"{len(t.get('body_examples') or [])} examples"
    )


@pytest.mark.parametrize(
    "t", RICH_TEMPLATES, ids=lambda t: f"{t['name']}-{t['language']}"
)
def test_button_labels_are_plain(t):
    """Meta forbids emoji / newline / formatting / variables in button text."""
    for btn in t.get("buttons") or []:
        label = btn.get("text", "")
        assert "\n" not in label
        assert "{{" not in label
        assert not any(c in label for c in "*_~"), f"formatting in {label!r}"
        assert not _has_emoji(label), f"emoji in button label {label!r}"


def test_body_no_leading_or_trailing_variable():
    """Meta rejects bodies that start or end on a variable."""
    for t in RICH_TEMPLATES:
        body = t["body"].strip()
        assert not body.startswith("{{"), f"{t['name']} body starts with a variable"
        assert not body.endswith("}}"), f"{t['name']} body ends with a variable"


class TestParseQuickReplyAction:
    @pytest.mark.parametrize(
        "payload,expected",
        [
            ("confirm:yarab-test/abc", "confirm"),
            ("postpone:yarab-test/abc", "postpone"),
            ("cancel:yarab-test/abc", "cancel"),
            # Legacy _v1 payloads carry no action prefix → confirm.
            ("yarab-test/abc", "confirm"),
            ("abc", "confirm"),
            ("", "confirm"),
            # Unknown prefix → safe default.
            ("frobnicate:x/y", "confirm"),
        ],
    )
    def test_action(self, payload, expected):
        assert parse_quick_reply_action(payload) == expected


class TestParseOrderId:
    def test_with_action_and_subdomain(self):
        oid = "11111111-1111-1111-1111-111111111111"
        assert str(_parse_order_id(f"cancel:yarab-test/{oid}")) == oid

    def test_bare_uuid(self):
        oid = "11111111-1111-1111-1111-111111111111"
        assert str(_parse_order_id(oid)) == oid

    def test_action_then_bare_uuid_no_subdomain(self):
        oid = "11111111-1111-1111-1111-111111111111"
        assert str(_parse_order_id(f"postpone:{oid}")) == oid

    def test_garbage(self):
        assert _parse_order_id("not-a-uuid") is None
        assert _parse_order_id("") is None


class TestPaymentLabel:
    def test_cod(self):
        assert payment_label("cod", "en") == "Cash on delivery"
        assert payment_label("COD", "ar") == "الدفع عند الاستلام"

    def test_card_aliases(self):
        assert payment_label("paymob", "en") == "Card"
        assert payment_label("kashier", "en") == "Card"

    def test_unknown_titlecased(self):
        assert payment_label("bank_transfer", "en") == "Bank Transfer"

    def test_empty(self):
        assert payment_label("", "en") == "—"
        assert payment_label(None, "ar") == "—"


def test_confirm_request_builds_three_action_payloads():
    """The confirm-request send must emit 3 quick_reply button components
    whose payloads are confirm:/postpone:/cancel: of the same base locator.
    """
    svc = WhatsAppMessagingService(
        access_token="t", phone_number_id="1", business_account_id="2", app_secret="s"
    )
    # Dict key is the short locale ("en"); the template's .language is "en_US".
    tmpl = EGYPTIAN_TEMPLATES[MessageType.ORDER_CONFIRMATION_REQUEST]["en"]
    params = {
        "customer_name": "Ahmed",
        "store_name": "Cairo Style",
        "order_number": "ORD-1",
        "total": "EGP 250.00",
        "payment_label": "Cash on delivery",
        "item_count": "2",
        "address": "12 Tahrir St",
        "confirm_payload": "confirm:yarab-test/abc",
        "postpone_payload": "postpone:yarab-test/abc",
        "cancel_payload": "cancel:yarab-test/abc",
    }
    msg = svc._build_template_message(tmpl.name, tmpl.language, params, tmpl.components)

    buttons = [c for c in msg["components"] if c["type"] == "button"]
    assert len(buttons) == 3
    payloads = [b["parameters"][0]["payload"] for b in buttons]
    assert payloads == [
        "confirm:yarab-test/abc",
        "postpone:yarab-test/abc",
        "cancel:yarab-test/abc",
    ]
    # Each button param is a payload (not text) so the webhook gets it back.
    assert all(b["parameters"][0]["type"] == "payload" for b in buttons)

    body = next(c for c in msg["components"] if c["type"] == "body")
    assert len(body["parameters"]) == 7
