"""Input sanitization utilities for user-provided strings.

Provides defense-in-depth HTML tag stripping and length validation
for all user-facing text fields. This is a secondary defense layer
on top of Pydantic's built-in field constraints.

Usage in schemas:
    from src.api.dependencies.sanitization import SanitizedStr

    class CreateProductRequest(BaseModel):
        name: SanitizedStr = Field(..., min_length=1, max_length=255)
        description: SanitizedStr | None = Field(None, max_length=10000)
"""

import re
from typing import Annotated

from pydantic import BeforeValidator

# Regex to match HTML/XML tags (including self-closing, attributes, etc.)
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Regex to match HTML entities like &amp; &lt; &#123; &#x1A;
_HTML_ENTITY_RE = re.compile(r"&(?:#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z]+);")

# Common dangerous patterns beyond HTML tags
_DANGEROUS_PATTERNS = [
    re.compile(r"javascript\s*:", re.IGNORECASE),
    re.compile(r"data\s*:\s*text/html", re.IGNORECASE),
    re.compile(r"vbscript\s*:", re.IGNORECASE),
]


def strip_html_tags(value: str) -> str:
    """Strip HTML/XML tags from a string.

    This is a defense-in-depth measure. The API returns JSON, so XSS
    is primarily a frontend concern, but stripping tags prevents stored
    XSS if the frontend renders user content without escaping.

    Args:
        value: The input string to sanitize.

    Returns:
        String with all HTML tags removed and dangerous patterns neutralized.
    """
    # Remove HTML tags
    cleaned = _HTML_TAG_RE.sub("", value)

    # Neutralize dangerous URI schemes in remaining text
    for pattern in _DANGEROUS_PATTERNS:
        cleaned = pattern.sub("", cleaned)

    # Collapse multiple whitespace left by tag removal, but preserve newlines
    cleaned = re.sub(r"[^\S\n]+", " ", cleaned)

    return cleaned.strip()


def _sanitize_string(value: object) -> object:
    """Pydantic BeforeValidator that sanitizes string inputs.

    Non-string values are passed through unchanged (Pydantic will
    handle type validation separately).
    """
    if isinstance(value, str):
        return strip_html_tags(value)
    return value


# Annotated type for use in Pydantic schemas.
# Strips HTML tags before Pydantic's own validation runs.
SanitizedStr = Annotated[str, BeforeValidator(_sanitize_string)]
