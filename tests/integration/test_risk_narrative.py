"""Tests for the risk narrative service (backend-024 / spec 011).

Snapshot-style tests for the PII tokenization + residual-PII detection
since those are the load-bearing safety net per spec 011 CL-002 + FR-006.
The actual LLM call is exercised in a separate test that mocks the
``openai.AsyncOpenAI`` client.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.application.services.risk_narrative_service import (
    SENTINEL_ADDRESS,
    SENTINEL_CUSTOMER,
    SENTINEL_EMAIL,
    SENTINEL_ORDER,
    SENTINEL_PHONE,
    EntityValuesForTokenization,
    NarrativeFactor,
    NarrativeResult,
    detect_residual_pii,
    generate_narrative,
    tokenize_pii,
)

# ---------------------------------------------------------------------------
# Tokenization (pure-Python — no DB / LLM)
# ---------------------------------------------------------------------------


class TestTokenization:
    """Spec 011 CL-002 — entity-driven, not regex-driven, with regex backstop."""

    def test_replaces_full_name_when_present(self):
        entities = EntityValuesForTokenization(
            customer_first_name="Yahia",
            customer_last_name="Mohamed",
        )
        out = tokenize_pii(
            "Customer Yahia Mohamed has refused 3 of 5 orders.", entities
        )
        assert SENTINEL_CUSTOMER in out
        assert "Yahia" not in out
        assert "Mohamed" not in out

    def test_replaces_first_name_only_when_alone(self):
        entities = EntityValuesForTokenization(customer_first_name="Mohammed")
        out = tokenize_pii("Mohammed placed the order at 3am.", entities)
        assert "Mohammed" not in out
        assert SENTINEL_CUSTOMER in out

    def test_case_insensitive_substitution(self):
        entities = EntityValuesForTokenization(customer_first_name="YAHIA")
        out = tokenize_pii("yahia placed an order.", entities)
        assert "yahia" not in out.lower().replace("<customer>".lower(), "")

    def test_replaces_phone_via_regex_backstop(self):
        """Even without entity-side knowledge of the phone, the regex catches it."""
        entities = EntityValuesForTokenization()
        out = tokenize_pii("Reach the customer at +201001234567.", entities)
        assert "+201001234567" not in out
        assert SENTINEL_PHONE in out

    def test_replaces_eg_local_mobile_via_regex(self):
        entities = EntityValuesForTokenization()
        out = tokenize_pii("The phone 01001234567 was used.", entities)
        assert "01001234567" not in out
        assert SENTINEL_PHONE in out

    def test_replaces_address_when_known(self):
        entities = EntityValuesForTokenization(shipping_address1="10 Tahrir Square")
        out = tokenize_pii("Shipped to 10 Tahrir Square in Cairo.", entities)
        assert "10 Tahrir Square" not in out
        assert SENTINEL_ADDRESS in out

    def test_replaces_email_when_known(self):
        entities = EntityValuesForTokenization(customer_email="yahia@example.com")
        out = tokenize_pii("Email confirmation sent to yahia@example.com", entities)
        assert "yahia@example.com" not in out
        assert SENTINEL_EMAIL in out

    def test_replaces_order_id_when_known(self):
        entities = EntityValuesForTokenization(shopify_order_id="gid://1234567890")
        out = tokenize_pii("Order gid://1234567890 was flagged at risk.", entities)
        assert "gid://1234567890" not in out
        assert SENTINEL_ORDER in out

    def test_empty_text_passthrough(self):
        assert tokenize_pii("", EntityValuesForTokenization()) == ""

    def test_no_entities_only_regex_runs(self):
        """No entity values supplied → only regex backstop fires."""
        out = tokenize_pii(
            "Customer placed order at +201234567890.", EntityValuesForTokenization()
        )
        assert "+201234567890" not in out


# ---------------------------------------------------------------------------
# Residual-PII detection (the post-generation safety net)
# ---------------------------------------------------------------------------


class TestResidualPiiDetection:
    """Spec 011 FR-006 — discard generations that contain raw PII."""

    def test_clean_narrative_passes(self):
        entities = EntityValuesForTokenization(customer_first_name="Yahia")
        result = detect_residual_pii(
            "The customer has refused several recent orders. Worth confirming "
            "via WhatsApp before shipping.",
            entities,
        )
        assert result is None

    def test_e164_phone_in_output_caught(self):
        entities = EntityValuesForTokenization()
        result = detect_residual_pii(
            "The customer's phone +201234567890 was first seen this week.",
            entities,
        )
        assert result == "e164_phone"

    def test_eg_local_mobile_in_output_caught(self):
        entities = EntityValuesForTokenization()
        result = detect_residual_pii(
            "We tried calling 01112345678 with no answer.",
            entities,
        )
        assert result == "eg_local_phone"

    def test_long_digit_run_caught(self):
        entities = EntityValuesForTokenization()
        result = detect_residual_pii(
            "Order id 1234567 was flagged.",
            entities,
        )
        assert result == "digit_run"

    def test_short_digit_runs_pass(self):
        """4-6 digit numbers (years, common amounts) are not flagged."""
        entities = EntityValuesForTokenization()
        result = detect_residual_pii(
            "Risk score 78 — recovered EGP 480 last week.",
            entities,
        )
        assert result is None

    def test_literal_name_substring_caught(self):
        """If the LLM somehow leaks a known name, the entity-pass catches it."""
        entities = EntityValuesForTokenization(customer_first_name="Mahmoud")
        result = detect_residual_pii("Mahmoud has prior refusals.", entities)
        assert result is not None
        assert result.startswith("literal_match:")

    def test_short_entity_value_not_false_positive(self):
        """Names ≤ 3 chars don't trigger literal-match (would false-positive)."""
        entities = EntityValuesForTokenization(customer_first_name="Ali")
        result = detect_residual_pii("All risk factors look normal here.", entities)
        assert result is None


# ---------------------------------------------------------------------------
# Narrative generation — graceful degradation when LLM is unavailable
# ---------------------------------------------------------------------------


class TestNarrativeGeneration:
    @pytest.mark.asyncio
    async def test_no_llm_configured_returns_failure_reason(self, monkeypatch):
        """Spec 011 FR-005 — LLM unavailable → narrative is None, no error UI."""
        from src.application.services import risk_narrative_service

        # Force the settings.google_ai_api_key to None.
        monkeypatch.setattr(
            risk_narrative_service,
            "get_settings",
            lambda: type("S", (), {"google_ai_api_key": None})(),
        )

        result = await generate_narrative(
            [
                NarrativeFactor(
                    name="customer_history",
                    score=70,
                    weight=0.35,
                    reason_tokenized="Refused 3 of 5 prior orders",
                )
            ],
        )
        assert isinstance(result, NarrativeResult)
        assert result.narrative_en is None
        assert result.narrative_ar is None
        assert result.failure_reason == "llm_not_configured"

    @pytest.mark.asyncio
    async def test_llm_call_failure_returns_none(self, monkeypatch):
        """Any exception in the LLM path → narrative None, no exception raised."""
        from src.application.services import risk_narrative_service

        monkeypatch.setattr(
            risk_narrative_service,
            "get_settings",
            lambda: type(
                "S",
                (),
                {
                    "google_ai_api_key": "fake-key",
                    "google_ai_base_url": "http://fake",
                    "ai_insights_model": "gemini-2.0-flash",
                },
            )(),
        )

        # Make the OpenAI client constructor raise.
        with patch(
            "openai.AsyncOpenAI",
            side_effect=RuntimeError("network down"),
        ):
            result = await generate_narrative(
                [
                    NarrativeFactor(
                        name="order_value",
                        score=60,
                        weight=0.20,
                        reason_tokenized="Order is 3x the store average",
                    )
                ],
            )
            assert result.narrative_en is None
            assert result.narrative_ar is None
            assert result.failure_reason is not None
            assert "llm_call_failed" in result.failure_reason
