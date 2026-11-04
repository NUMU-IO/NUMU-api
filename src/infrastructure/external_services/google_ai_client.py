"""The one shared "NUMU AI" client: Google Gemini through its OpenAI-compatible
API, used by the merchant AI generators (store policies, promotion content)
and by apps that run inside NUMU-api (the Lamaa app). One key, one base URL.
"""

import json
import re

from openai import AsyncOpenAI

from src.config import settings


class AIServiceNotConfigured(RuntimeError):
    """``google_ai_api_key`` is not set on this server."""


def numu_ai_client() -> AsyncOpenAI:
    """Build the shared client. Raises ``AIServiceNotConfigured`` when the key
    is missing; HTTP routes turn that into their 503."""
    if not settings.google_ai_api_key:
        raise AIServiceNotConfigured("AI service is not configured.")
    return AsyncOpenAI(
        api_key=settings.google_ai_api_key,
        base_url=settings.google_ai_base_url,
    )


def parse_json_object(raw: str) -> dict:
    """Parse a JSON object from an AI response, tolerating markdown fences.

    Gemini's OpenAI-compatible endpoint doesn't always honor
    ``response_format=json_object`` and may wrap the object in ```json
    fences, so we unwrap before parsing and degrade to ``{}`` on failure.
    """
    s = (raw or "").strip()
    s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s).strip()
    try:
        parsed = json.loads(s or "{}")
    except (json.JSONDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
