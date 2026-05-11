"""Pydantic schemas for the Meta tracking settings endpoints.

These cover the merchant-hub Wave 1C UI surface (plan §13.2):

  * GET  /stores/{id}/settings/tracking                      → MetaTrackingResponse
  * PUT  /stores/{id}/settings/tracking/meta                 → SaveMetaTrackingRequest → MetaTrackingResponse
  * DELETE /stores/{id}/settings/tracking/meta               → MetaTrackingResponse (with both flags false)
  * POST /stores/{id}/settings/tracking/meta/test-event      → SendMetaTestEventRequest → SendMetaTestEventResponse
  * GET  /stores/{id}/settings/tracking/meta/events          → MetaEventLogEntry[]
  * GET  /stores/{id}/settings/tracking/meta/status          → MetaTrackingStatusResponse

The PUT request schema intentionally allows ``capi_access_token`` to be
omitted — the route preserves the existing encrypted credential when no
new token is supplied. This lets the merchant tweak ``debug_mode`` or
``test_event_code`` without re-pasting their bearer token.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Activation mode is derived from the two persisted booleans
# (``pixel_enabled``, ``capi_enabled``) — see meta_tracking_resolver.py.
TrackingMode = Literal["off", "pixel_only", "capi_only", "both"]
TrackingStatus = Literal["disabled", "configured_no_events", "connected", "failing"]


class SaveMetaTrackingRequest(BaseModel):
    """Body for ``PUT /stores/{id}/settings/tracking/meta``."""

    pixel_id: str = Field(..., min_length=1, max_length=32)
    pixel_enabled: bool
    capi_enabled: bool
    # Optional: only sent when (re)setting the token. When ``capi_enabled``
    # is true and no token is on file AND none is provided here, the route
    # rejects with 422.
    capi_access_token: str | None = Field(default=None, min_length=20, max_length=512)
    test_event_code: str | None = Field(default=None, max_length=64)
    consent_required: bool = False
    # Debug-mode UX contract (see plan §C in the implementation notes):
    # when set true, the route persists ``debug_mode_expires_at = now+60min``.
    # The Celery task reads that timestamp at execution time and auto-attaches
    # ``test_event_code`` to every event until it expires. Frontend just
    # toggles a bool — the expiry math lives server-side.
    debug_mode: bool = False

    @field_validator("pixel_id")
    @classmethod
    def _validate_pixel_id(cls, v: str) -> str:
        # Meta Pixel IDs are 15-16 numeric digits.
        import re

        if not re.match(r"^\d{15,16}$", v):
            raise ValueError("pixel_id must be 15-16 digits (Meta Pixel ID format)")
        return v

    @field_validator("test_event_code")
    @classmethod
    def _validate_test_event_code(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        import re

        if not re.match(r"^TEST\d+$", v):
            raise ValueError("test_event_code must match ^TEST\\d+$ (e.g. TEST12345)")
        return v


class MetaTrackingResponse(BaseModel):
    """Shape returned by GET, PUT, DELETE on the meta-tracking endpoints.

    NEVER includes the raw CAPI access token — only the masked form. The
    raw token lives in ``service_credentials.credentials_encrypted`` and
    is decrypted only at Celery-task execution time.
    """

    pixel_id: str | None = None
    pixel_enabled: bool = False
    capi_enabled: bool = False
    mode: TrackingMode = "off"
    capi_access_token_masked: str | None = None
    domain_verification_token: str | None = None
    test_event_code: str | None = None
    consent_required: bool = False
    debug_mode: bool = False
    debug_mode_expires_at: datetime | None = None
    last_validated_at: datetime | None = None
    status: TrackingStatus = "disabled"


class TrackingSettingsResponse(BaseModel):
    """Shape returned by GET /stores/{id}/settings/tracking — wrapper for
    the per-channel tracking configs. Today only ``meta``; Google Ads /
    TikTok will land here in v2.
    """

    meta: MetaTrackingResponse


class SendMetaTestEventRequest(BaseModel):
    """Body for the test-event endpoint."""

    test_event_code: str = Field(..., min_length=1, max_length=64)

    @field_validator("test_event_code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        import re

        if not re.match(r"^TEST\d+$", v):
            raise ValueError("test_event_code must match ^TEST\\d+$ (e.g. TEST12345)")
        return v


class SendMetaTestEventResponse(BaseModel):
    """Synthetic-Purchase fan-out result (the actual CAPI POST is async)."""

    enqueued: bool
    test_event_code: str
    queued_event_id: str


class MetaEventLogEntry(BaseModel):
    """One row from the merchant dashboard's "Recent events" table.

    The ``request_payload.user_data`` sub-object is **dropped entirely**
    by the route layer before this schema sees it — the dashboard shows
    only "hashed ✓" indicators, not raw or hashed PII.

    ``channel`` is always ``"server"`` today because we only log
    server-side fires. Wave 2E will fire the browser event with the same
    ``event_id``; a follow-up enhancement can join the two and surface
    a "both" badge here.
    """

    id: str
    event_id: str
    event_name: str
    event_time: datetime
    pixel_id: str
    response_status: int | None = None
    fbtrace_id: str | None = None
    attempt_count: int = 1
    last_error: str | None = None
    sent_at: datetime | None = None
    created_at: datetime
    channel: Literal["browser", "server", "both"] = "server"
    # Redacted snapshot — only non-PII keys (custom_data, event_name,
    # event_time, event_source_url) survive. user_data is replaced by
    # boolean indicators so the dashboard can show "Email: hashed ✓".
    request_payload_redacted: dict


class MetaTrackingStatusResponse(BaseModel):
    """Live status badge for the dashboard header (plan §7.5)."""

    status: TrackingStatus
    mode: TrackingMode
    last_validated_at: datetime | None = None
    # Recent failure rate as a fraction of recent events (0.0 = healthy).
    recent_failure_rate: float = 0.0
    # Total recent events considered when computing the failure rate.
    recent_event_count: int = 0
