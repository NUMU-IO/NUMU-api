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

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.api.v1.schemas.tenant.tracking_validation import (
    META_MIN_TOKEN_LENGTH,
    META_PIXEL_ID_ERROR,
    META_TEST_EVENT_CODE_ERROR,
    TIKTOK_MIN_TOKEN_LENGTH,
    TIKTOK_PIXEL_ID_ERROR,
    TIKTOK_TEST_EVENT_CODE_ERROR,
    is_valid_meta_pixel_id,
    is_valid_meta_test_event_code,
    is_valid_tiktok_pixel_id,
    is_valid_tiktok_test_event_code,
)

# Activation mode is derived from the two persisted booleans
# (``pixel_enabled``, ``capi_enabled``) — see meta_tracking_resolver.py.
TrackingMode = Literal["off", "pixel_only", "capi_only", "both"]
# `pending` and `browser_only` are additive — widening a Literal cannot break
# an existing client, and both replace a badge that used to lie:
#   pending      — events queued, none acknowledged by the vendor yet. Was
#                  reported as "connected", i.e. green before anything landed.
#   browser_only — the store runs pixel-only, so the SERVER event log will
#                  always be empty by design. It used to render
#                  "configured_no_events" forever, which reads as broken.
TrackingStatus = Literal[
    "disabled",
    "configured_no_events",
    "connected",
    "failing",
    "pending",
    "browser_only",
]

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
        # Strip first: merchants paste from Events Manager and bring
        # surrounding whitespace/newlines with them. Normalising is kinder
        # than a 422, and it keeps the stored value safe to interpolate
        # into a Graph API path.
        v = v.strip()
        if not is_valid_meta_pixel_id(v):
            raise ValueError(META_PIXEL_ID_ERROR)
        return v


class SaveMetaTrackingRequest(BaseModel):
    """Body for ``PUT /stores/{id}/settings/tracking/meta``."""

    pixel_id: str = Field(..., min_length=1, max_length=32)
    pixel_enabled: bool
    capi_enabled: bool
    # Optional: only sent when (re)setting the token. When ``capi_enabled``
    # is true and no token is on file AND none is provided here, the route
    # rejects with 422.
    capi_access_token: str | None = Field(
        default=None, min_length=META_MIN_TOKEN_LENGTH, max_length=512
    )
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
    #
    # Tri-state on the wire (True / False / omitted). A plain `bool = False`
    # could not distinguish "the merchant turned this off" from "this client
    # did not render the control", so any partial save silently disabled it —
    # along with the rest of the fields now on the no-clobber contract in
    # `save_meta_tracking`. Omitting the field preserves the stored value.
    whatsapp_lead_enabled: bool | None = None

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

    # Meta Business connection IDs — required by the audience-sync (US4)
    # and Promote-on-Meta (US7) features which call Meta's Marketing
    # Graph API. Pixel + CAPI alone (Pixel ID + access_token above) only
    # unlock event ingestion; creating Custom Audiences, building
    # Lookalikes, or pushing a draft ad needs ad_account_id, and
    # publishing an ad creative needs page_id. Both are optional here so
    # merchants who only use Pixel + CAPI never get gated; downstream
    # routes that need them raise 412 with a clear "complete OAuth"
    # message. Stored verbatim in store.settings.tracking.meta.
    ad_account_id: str | None = Field(default=None, max_length=64)
    page_id: str | None = Field(default=None, max_length=64)

    # Domain verification. Meta MINTS this value (Business Manager → Brand
    # Safety → Domains → "Add a meta-tag to your website"); the storefront
    # emits it as <meta name="facebook-domain-verification">. It is Meta's
    # value, not ours — before this field existed the route minted a random
    # ``token_urlsafe`` and had no way to accept the real one, so the tag on
    # the storefront never matched what Meta looked for and verification
    # could not succeed for any merchant.
    # Omitted (None) means "leave whatever is stored alone", matching how
    # ``capi_access_token`` and the business IDs above behave.
    domain_verification_token: str | None = Field(default=None, max_length=512)

    @field_validator("pixel_id")
    @classmethod
    def _validate_pixel_id(cls, v: str) -> str:
        # Strip first: merchants paste from Events Manager and bring
        # surrounding whitespace/newlines with them. Normalising is kinder
        # than a 422, and it keeps the stored value safe to interpolate
        # into a Graph API path.
        v = v.strip()
        if not is_valid_meta_pixel_id(v):
            raise ValueError(META_PIXEL_ID_ERROR)
        return v

    @field_validator("domain_verification_token")
    @classmethod
    def _validate_domain_verification_token(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        if not v:
            return None
        # Business Manager shows the token inside a ready-to-copy <meta> tag,
        # so that whole tag is what actually lands on the clipboard. Pull the
        # token out rather than 422-ing the merchant for following the UI.
        tag = re.search(r"content=[\"']([^\"']+)[\"']", v)
        if tag:
            v = tag.group(1).strip()
        if not v:
            return None
        # Loosely bounded on purpose — see docs/external-contracts.md. Meta
        # publishes no format for this token, so we only catch paste errors
        # (leftover markup, embedded whitespace) and let Meta's own verify
        # step judge the value.
        if len(v) > 128 or any(ch.isspace() or ch in "<>\"'" for ch in v):
            raise ValueError(
                "domain_verification_token must be the token itself "
                "(no <meta> wrapper, no spaces)"
            )
        return v

    @field_validator("test_event_code")
    @classmethod
    def _validate_test_event_code(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        if not v:
            return None
        if not is_valid_meta_test_event_code(v):
            raise ValueError(META_TEST_EVENT_CODE_ERROR)
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
    # Meta Business connection IDs — surfaced so the hub's tracking
    # panel can render the current values and the downstream-feature
    # gates ("Connect Meta to use Audiences / Promote-on-Meta") can
    # decide whether to render the empty state.
    ad_account_id: str | None = None
    page_id: str | None = None
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


# ============================================================================
# TikTok tracking (Pixel + Events API) — sibling of the Meta schemas above.
# Deltas: server toggle is ``api_enabled`` (TikTok calls it "Events API"),
# pixel IDs are alphanumeric (not 15-16 digits), the click id is ``ttclid``
# and the response carries a ``request_id`` (not ``fbtrace_id``).
# ============================================================================


class TikTokPixelEntry(BaseModel):
    """One pixel in a store's multi-pixel TikTok configuration."""

    pixel_id: str = Field(..., min_length=1, max_length=64)
    pixel_enabled: bool = True
    api_enabled: bool = True
    label: str | None = Field(default=None, max_length=64)
    role: PixelRole | None = None

    @field_validator("pixel_id")
    @classmethod
    def _validate_pixel_id(cls, v: str) -> str:
        v = v.strip()
        if not is_valid_tiktok_pixel_id(v):
            raise ValueError(TIKTOK_PIXEL_ID_ERROR)
        return v


class SaveTikTokTrackingRequest(BaseModel):
    """Body for ``PUT /stores/{id}/settings/tracking/tiktok``."""

    pixel_id: str = Field(..., min_length=1, max_length=64)
    pixel_enabled: bool
    api_enabled: bool
    # Optional: only sent when (re)setting the Events API token. When
    # ``api_enabled`` is true and no token is on file AND none is provided
    # here, the route rejects with 422.
    api_access_token: str | None = Field(
        default=None, min_length=TIKTOK_MIN_TOKEN_LENGTH, max_length=512
    )
    test_event_code: str | None = Field(default=None, max_length=64)
    consent_required: bool = False
    debug_mode: bool = False
    # COD-aware CompletePayment timing — None preserves legacy behaviour
    # (payment webhooks remain the sole CompletePayment source).
    purchase_trigger: PurchaseTrigger | None = None
    # Optional multi-pixel list. When set, every Events API fire fans out
    # to each api_enabled entry; the storefront pixel mount iterates.
    pixels: list[TikTokPixelEntry] | None = Field(default=None, max_length=10)
    # TikTok advertiser id — reserved for the Marketing API phase
    # (campaigns / catalog). Optional; stored verbatim.
    advertiser_id: str | None = Field(default=None, max_length=64)

    @field_validator("pixel_id")
    @classmethod
    def _validate_pixel_id(cls, v: str) -> str:
        v = v.strip()
        if not is_valid_tiktok_pixel_id(v):
            raise ValueError(TIKTOK_PIXEL_ID_ERROR)
        return v

    @field_validator("test_event_code")
    @classmethod
    def _validate_test_event_code(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        if not v:
            return None
        if not is_valid_tiktok_test_event_code(v):
            raise ValueError(TIKTOK_TEST_EVENT_CODE_ERROR)
        return v


class TikTokTrackingResponse(BaseModel):
    """Shape returned by GET, PUT, DELETE on the tiktok-tracking endpoints.

    NEVER includes the raw Events API access token — only the masked form.
    """

    pixel_id: str | None = None
    pixel_enabled: bool = False
    api_enabled: bool = False
    mode: TrackingMode = "off"
    api_access_token_masked: str | None = None
    # Whether an active credential row exists — the mask above is None when
    # the token cannot be decrypted for display, which the hub used to read
    # as "no token on file" while events were flowing.
    has_token: bool = False
    test_event_code: str | None = None
    consent_required: bool = False
    debug_mode: bool = False
    debug_mode_expires_at: datetime | None = None
    last_validated_at: datetime | None = None
    status: TrackingStatus = "disabled"
    purchase_trigger: PurchaseTrigger | None = None
    pixels: list[TikTokPixelEntry] | None = None
    advertiser_id: str | None = None


class SendTikTokTestEventRequest(BaseModel):
    """Body for the TikTok test-event endpoint."""

    test_event_code: str = Field(..., min_length=1, max_length=64)

    @field_validator("test_event_code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        v = v.strip()
        if not is_valid_tiktok_test_event_code(v):
            raise ValueError(TIKTOK_TEST_EVENT_CODE_ERROR)
        return v


class SendTikTokTestEventResponse(BaseModel):
    """Synthetic-CompletePayment fan-out result (the actual POST is async)."""

    enqueued: bool
    test_event_code: str
    queued_event_id: str


class TikTokEventLogEntry(BaseModel):
    """One row from the merchant dashboard's "Recent events" table.

    The ``request_payload.user`` sub-object is dropped by the route layer;
    only boolean presence indicators survive.
    """

    id: str
    event_id: str
    event_name: str
    event_time: datetime
    pixel_id: str
    response_status: int | None = None
    response_code: int | None = None
    request_id: str | None = None
    attempt_count: int = 1
    last_error: str | None = None
    sent_at: datetime | None = None
    created_at: datetime
    channel: Literal["browser", "server", "both"] = "server"
    request_payload_redacted: dict


class TikTokTrackingStatusResponse(BaseModel):
    """Live status badge for the dashboard header."""

    status: TrackingStatus
    mode: TrackingMode
    last_validated_at: datetime | None = None
    recent_failure_rate: float = 0.0
    recent_event_count: int = 0


class TikTokReportResponse(BaseModel):
    """Aggregated TikTok Marketing report for the hub reporting card.

    ``connected`` is False when the store has no ``advertiser_id`` on file (or
    no OAuth-scoped token) — the UI then renders a "Connect with TikTok to see
    ad performance" empty state instead of a zero-filled table.
    """

    connected: bool
    advertiser_id: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    spend: float = 0.0
    impressions: int = 0
    clicks: int = 0
    conversions: float = 0.0
    cost_per_conversion: float = 0.0
    ctr: float = 0.0
    # Present when the report call failed (token lacks reporting scope, TikTok
    # outage, …) — the UI shows this instead of misleading zeros.
    error: str | None = None


class TrackingSettingsResponse(BaseModel):
    """Shape returned by GET /stores/{id}/settings/tracking — wrapper for
    the per-channel tracking configs. ``meta`` and ``tiktok`` today; Google
    Ads will land here later.
    """

    meta: MetaTrackingResponse
    tiktok: TikTokTrackingResponse | None = None


class SendMetaTestEventRequest(BaseModel):
    """Body for the test-event endpoint."""

    test_event_code: str = Field(..., min_length=1, max_length=64)

    @field_validator("test_event_code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        v = v.strip()
        if not is_valid_meta_test_event_code(v):
            raise ValueError(META_TEST_EVENT_CODE_ERROR)
        return v


class SendMetaTestEventResponse(BaseModel):
    """Synthetic-Purchase fan-out result (the actual CAPI POST is async)."""

    enqueued: bool
    test_event_code: str
    queued_event_id: str


class VerifyConnectionResponse(BaseModel):
    """Result of asking the provider whether a pixel is real and reachable.

    This is the answer a regex can never give. Our validation rules only catch
    paste errors; whether ``1712515290084839`` is a dataset that exists, is
    active, and that this token may write to is a question only Meta / TikTok
    can answer. A typo'd-but-well-formed ID used to validate fine and then
    silently never deliver — that entire failure class disappears once the
    merchant can press a button and see the dataset's real name.

    ``verified=False`` with ``error`` set means the provider answered and said
    no; ``verified=False`` with ``error`` describing a missing prerequisite
    means we could not ask. The two are deliberately not conflated: "Meta says
    this pixel doesn't exist" and "add a token so we can check" need different
    actions from the merchant.
    """

    verified: bool
    # The dataset / pixel name as the provider knows it — the single most
    # convincing confirmation for a merchant ("✅ Connected to 'new 1'").
    name: str | None = None
    is_active: bool | None = None
    # The provider's verbatim message when it refused. Forwarded rather than
    # reworded: their error names the actual problem (expired token, wrong
    # business, no permission) far better than any mapping we could invent.
    error: str | None = None
    # Which platform answered — lets one shared hub component render both.
    platform: Literal["meta", "tiktok"]


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


class MetaDeliveryHealth(BaseModel):
    """What the outbox still owes Meta, and what it gave up on.

    The failure rate above is computed over the last 20 rows, so it answers
    "is this store healthy right now". These counters answer the question it
    cannot: "is anything stuck". A store can show a perfectly clean recent
    window while a hundred conversions sit in the retry ladder behind it.
    """

    # Persisted, delivery not yet acknowledged.
    pending: int = 0
    # Failed retryably; waiting on the backoff ladder.
    retrying: int = 0
    # Retryable, but the attempt budget ran out. Meta or the network was
    # down — not a merchant misconfiguration.
    dead_letter: int = 0
    # Past the point where sending would merge rather than double-count, so
    # deliberately never sent. Not an error.
    expired: int = 0
    # Permanently rejected: bad payload, dead token, unknown pixel. The only
    # bucket here that a merchant can act on.
    failed: int = 0
    # Window the counts cover.
    window_hours: int = 24


class MetaTrackingStatusResponse(BaseModel):
    """Live status badge for the dashboard header (plan §7.5)."""

    status: TrackingStatus
    mode: TrackingMode
    last_validated_at: datetime | None = None
    # Recent failure rate as a fraction of recent events (0.0 = healthy).
    recent_failure_rate: float = 0.0
    # Total recent events considered when computing the failure rate.
    recent_event_count: int = 0
    delivery: MetaDeliveryHealth = Field(default_factory=MetaDeliveryHealth)


class MetaMatchKeyCoverage(BaseModel):
    """One match key and how many of this event's instances carried it."""

    identifier: str
    coverage_percentage: float


class MetaMatchQualityEvent(BaseModel):
    """EMQ for one event name, as Meta last reported it."""

    event_name: str
    pixel_id: str
    emq_score: float = Field(..., description="Meta's composite_score, 0.0-10.0")
    total_events: int = 0
    dedup_rate: float | None = None
    event_coverage: float | None = Field(
        default=None,
        description=(
            "7-day average % of browser Pixel events also covered by CAPI — "
            "Meta measuring the browser-vs-server gap directly."
        ),
    )
    data_freshness: str | None = None
    match_keys: list[MetaMatchKeyCoverage] = Field(default_factory=list)
    diagnostics: list[dict] = Field(
        default_factory=list,
        description=(
            "Meta's own diagnostics — each names a problem AND states the "
            "solution. Rendered verbatim; their copy is better than ours."
        ),
    )
    captured_at: datetime


class MetaMatchQualityResponse(BaseModel):
    """Latest EMQ snapshot per event for a store.

    ``events`` empty with ``last_polled_at`` null means no poll has landed
    yet — distinct from "this store has no Meta connection", which the caller
    already knows from the tracking config.
    """

    events: list[MetaMatchQualityEvent] = Field(default_factory=list)
    last_polled_at: datetime | None = None
    low_score_threshold: float = 6.5
