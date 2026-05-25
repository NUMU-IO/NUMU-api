"""Verify that ``redact_sensitive_fields`` scrubs BYO secrets and other
sensitive values from structured log events (TASK-SEC-006).

Covers ``access_token``, ``app_secret``, ``phone_number_id``, ``waba_id``
(the four WhatsApp BYO secrets) plus the generic credential allowlist
(``password``, ``api_key``, etc.).
"""

import logging

from src.config.logging_config import (
    REDACTION_MARKER,
    SENSITIVE_LOG_KEYS,
    redact_sensitive_fields,
)

# ``logger`` arg only used by structlog's protocol; the processor itself
# ignores it. Same for ``method_name``.
_DUMMY_LOGGER = logging.getLogger("dummy")
_DUMMY_METHOD = "info"


def _process(event_dict: dict) -> dict:
    return redact_sensitive_fields(_DUMMY_LOGGER, _DUMMY_METHOD, event_dict)


def test_access_token_at_top_level_redacted() -> None:
    result = _process({"event": "byo_connect", "access_token": "EAA_secret_xyz"})
    assert result["access_token"] == REDACTION_MARKER
    assert result["event"] == "byo_connect"


def test_all_whatsapp_byo_secrets_redacted() -> None:
    event = {
        "access_token": "EAAxxx",
        "app_secret": "abcd1234",
        "phone_number_id": "111222333",
        "waba_id": "999888777",
    }
    result = _process(event)
    assert all(result[k] == REDACTION_MARKER for k in event)


def test_redaction_handles_nested_dicts() -> None:
    result = _process({
        "event": "byo_connect_failed",
        "submitted_credentials": {
            "access_token": "EAA_inner",
            "app_secret": "inner_secret",
            "phone_number_id": "555",
        },
    })
    nested = result["submitted_credentials"]
    assert nested["access_token"] == REDACTION_MARKER
    assert nested["app_secret"] == REDACTION_MARKER
    assert nested["phone_number_id"] == REDACTION_MARKER


def test_redaction_handles_lists_of_dicts() -> None:
    result = _process({
        "event": "bulk_op",
        "items": [
            {"name": "row1", "access_token": "t1"},
            {"name": "row2", "access_token": "t2"},
        ],
    })
    assert result["items"][0]["access_token"] == REDACTION_MARKER
    assert result["items"][1]["access_token"] == REDACTION_MARKER
    assert result["items"][0]["name"] == "row1"


def test_case_insensitive_key_match() -> None:
    result = _process({"Access_Token": "uppercase", "APP_SECRET": "loud"})
    assert result["Access_Token"] == REDACTION_MARKER
    assert result["APP_SECRET"] == REDACTION_MARKER


def test_non_sensitive_keys_passed_through() -> None:
    event = {
        "event": "ok",
        "store_id": "11111111-aaaa-bbbb-cccc-222222222222",
        "phone_last4": "4567",
        "amount_cents": 12345,
    }
    result = _process(event)
    assert result == event


def test_generic_secrets_also_redacted() -> None:
    """The allowlist is the union of WhatsApp-specific keys + standard
    credential field names; the generic ones must also work."""
    for key in ["password", "passwd", "api_key", "apikey", "authorization"]:
        result = _process({key: "some-secret"})
        assert result[key] == REDACTION_MARKER, f"key={key!r} not redacted"


def test_sensitive_log_keys_set_is_immutable() -> None:
    """Make accidental runtime mutation of the allowlist impossible."""
    assert isinstance(SENSITIVE_LOG_KEYS, frozenset)
    assert "access_token" in SENSITIVE_LOG_KEYS
    assert "app_secret" in SENSITIVE_LOG_KEYS
