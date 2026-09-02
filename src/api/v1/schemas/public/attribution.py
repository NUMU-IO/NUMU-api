"""Acquisition attribution reported by the landing page.

Shared by both public signup doors. Everything here is visitor-supplied
and optional — direct traffic carries no UTMs at all, and a merchant who
pastes the URL into WhatsApp and opens it there loses the referrer. The
fields are recorded, never trusted: they are stored on the lead row,
truncated to fit, and read only by admin reporting.
"""

from pydantic import BaseModel, Field


class AttributionPayload(BaseModel):
    """UTM parameters plus the referrer and landing path."""

    utm_source: str | None = Field(None, max_length=120)
    utm_medium: str | None = Field(None, max_length=120)
    utm_campaign: str | None = Field(None, max_length=120)
    utm_content: str | None = Field(None, max_length=120)
    referrer: str | None = Field(None, max_length=500)
    landing_path: str | None = Field(None, max_length=255)
