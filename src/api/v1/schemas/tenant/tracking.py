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

# Wave 2 Phase 12 — COD-aware Purchase / Lead timing. Each is optional
# (None = legacy behavior: paymob/fawry webhooks remain the sole
# Purchase source, no Lead from status transitions).
#
# Recommended defaults the merchant hub UI should surface:
#   * Online-only stores: leave both None (current behavior).
#   * COD-enabled stores: ``purchase_trigger="delivered"`` (Meta sees
#     real conversions only, not no-show COD placements) +
#     ``lead_trigger="confirmed"`` (top-of-funnel signal for the algo).
PurchaseTrigger = Literal["confirmed", "processing", "shipped", "delivered"]
LeadTrigger = Literal["confirmed", "processing", "shipped", "delivered"]

# Wave 2 Phase 13 — Multi-pixel role assignment. Purely a UI label so
# the merchant hub can group "primary" pixels visually distinct from
# "retargeting" or "agency-owned" pixels. Backend treats every entry
# identically — fans out the same events to each capi-enabled pixel.
PixelRole = Literal["primary", "retargeting", "agency"]

# Wave 3 Phase 18 — Region default mode for the consent banner. ``auto``
# means the storefront uses the Cloudflare ``CF-IPCountry`` header (or
# Accept-Language fallback) to pick opt-in for EU/EEA/UK and opt-out
# for everywhere else. ``force_*`` overrides for merchants who know
# their audience is concentrated in one regime.
ConsentRegionMode = Literal["auto", "force_opt_in", "force_opt_out"]


class ConsentSettings(BaseModel):
    """Wave 3 Phase 18 — Per-store granular consent defaults.

    Mirrors Shopify's Customer Privacy API surface: four boolean
    categories the merchant can enable + a region-mode selector that
    drives the banner's default state. The storefront's
    ``<ConsentBanner>`` reads ``granular_enabled`` to decide whether
    to render the 4-toggle UI or the legacy 1-toggle UX, and reads
    ``region_default_mode`` to decide whether to opt-in or opt-out
    by default.

    Per-user choices are still stored in localStorage on the
    storefront (``numu_consent_v2``); this struct only carries the
    *merchant's policy*, not any individual visitor's decision.
    """

    granular_enabled: bool = Field(
        default=False,
        description=(
            "When true, the storefront banner renders 4 per-category toggles "
            "(analytics, marketing, preferences, sale_of_data). When false, "
            "shows the legacy single Accept/Reject pair."
        ),
    )
    region_default_mode: ConsentRegionMode = Field(
        default="force_opt_out",
        description=(
            "Default decision when the user hasn't chosen yet. "
            "``force_opt_out`` is the conservative MENA default; "
            "``auto`` enables Cloudflare-header region detection."
        ),
    )
    # Defaults the banner pre-checks when shown in granular mode. The
    # user can still toggle individual flags before clicking Save.
    default_analytics: bool = True
    default_marketing: bool = True
    default_preferences: bool = True
    default_sale_of_data: bool = False  # CCPA opt-OUT semantics — never default-on


class PixelEntry(BaseModel):
    """One pixel in a store's multi-pixel configuration.

    Wave 2 Phase 13: a store can register N pixels (EasyOrders parity;
    beats Shopify which is 1-only natively). Each pixel fires the same
    event stream; `event_id` is namespaced per-pixel by Meta's own
    dedup contract (``(pixel_id, event_name, event_id)`` tuple), so
    the same browser-side `eventID` value works across all pixels.

    All pixels under one store share a single CAPI access token in v1
    (Option A in the design: "one Business Manager, one System User
    token, many pixels"). Per-pixel credentials are a v1.1 follow-up.
    """

    pixel_id: str = Field(..., min_length=1, max_length=32)
    pixel_enabled: bool = True
    capi_enabled: bool = True
    label: str | None = Field(default=None, max_length=64)
    role: PixelRole | None = None

    @field_validator("pixel_id")
    @classmethod
    def _validate_pixel_id(cls, v: str) -> str:
        import re

        if not re.match(r"^\d{15,16}$", v):
            raise ValueError("pixel_id must be 15-16 digits (Meta Pixel ID format)")
        return v


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

    # Wave 2 Phase 12 — COD-aware Purchase / Lead firing. Both optional;
    # None preserves the legacy paymob/fawry-only Purchase path.
    purchase_trigger: PurchaseTrigger | None = None
    lead_trigger: LeadTrigger | None = None
    # Wave 2 Phase 15 — fire a Meta CAPI Lead event when a COD customer
    # confirms via WhatsApp reply (handle_verification_reply.apply_reply
    # outcome="confirmed"). Off by default — opt-in. Bridges WhatsApp
    # commerce into Meta's ad-attribution loop for merchants who drive
    # Meta ads → WhatsApp chat → manual confirmation.
    whatsapp_lead_enabled: bool = False

    # Wave 2 Phase 13 — Optional multi-pixel list. When set, every CAPI
    # fire fans out to each capi_enabled entry; the storefront's
    # MetaPixel mount iterates and runs ``fbq('init', pid)`` per entry.
    # Backward-compatible: None preserves legacy single-pixel behavior
    # (the top-level ``pixel_id`` field above remains authoritative).
    # The PUT route auto-syncs the top-level pixel_id to pixels[0] so
    # legacy readers continue to work even when a merchant has 2+ pixels.
    pixels: list[PixelEntry] | None = Field(default=None, max_length=10)

    # Wave 3 Phase 18 — Granular consent policy. When None, the
    # storefront falls back to the legacy ``consent_required`` boolean
    # above and shows the simple 1-toggle banner. When set, the
    # storefront renders the 4-flag granular banner.
    consent_settings: ConsentSettings | None = None

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
    # Wave 2 Phase 12 — surfaced so the merchant hub UI can pre-populate
    # the timing-config selectors when the merchant returns to the panel.
    purchase_trigger: PurchaseTrigger | None = None
    lead_trigger: LeadTrigger | None = None
    # Wave 2 Phase 15 — WhatsApp confirmation Lead-fire toggle.
    whatsapp_lead_enabled: bool = False
    # Wave 2 Phase 13 — list of pixels (None when legacy single-pixel).
    pixels: list[PixelEntry] | None = None
    # Wave 3 Phase 18 — granular consent policy (None = legacy 1-toggle).
    consent_settings: ConsentSettings | None = None


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
