"""Attribution value objects — first-/last-touch UTM snapshots.

These shapes travel three places:
  1. The storefront's ``numu_attribution`` cookie payload.
  2. The request body of ``POST /track`` and ``POST /checkout``.
  3. The JSONB column ``orders.attribution`` and ``customers.first_touch_attribution``.

Identical shape everywhere — so a snapshot read out of the DB can be
round-tripped back through the storefront unchanged.

Size caps (SEC-004): per-field ``max_length`` ceilings plus a 4 KB
envelope cap defend against oversized cookies inflating storage. Defaults
match the cookie schema documented in research.md R-01.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Per-field caps. Matches the `String(N)` lengths on the DB columns +
# the cookie schema in research.md R-01.
_UTM_MAX = 200
_REFERRER_MAX = 500
_LANDING_PATH_MAX = 500
_CLICK_ID_MAX = 256
_SESSION_ID_MAX = 64
_ENVELOPE_MAX_BYTES = 4096

# Current schema version for the cookie envelope. Bumping requires a
# migration of stored payloads in `orders.attribution` and
# `customers.first_touch_attribution`.
ATTRIBUTION_SCHEMA_VERSION = 1


class AttributionTouch(BaseModel):
    """A single touch point — first or last.

    All fields are nullable. The minimum useful touch carries at least one
    UTM dimension; touches with no UTMs at all are not emitted by the
    client and are not stamped server-side.
    """

    model_config = ConfigDict(extra="ignore")

    ts: datetime
    utm_source: str | None = Field(default=None, max_length=_UTM_MAX)
    utm_medium: str | None = Field(default=None, max_length=_UTM_MAX)
    utm_campaign: str | None = Field(default=None, max_length=_UTM_MAX)
    utm_term: str | None = Field(default=None, max_length=_UTM_MAX)
    utm_content: str | None = Field(default=None, max_length=_UTM_MAX)
    gclid: str | None = Field(default=None, max_length=_CLICK_ID_MAX)
    fbclid: str | None = Field(default=None, max_length=_CLICK_ID_MAX)
    referrer: str | None = Field(default=None, max_length=_REFERRER_MAX)
    landing_path: str | None = Field(default=None, max_length=_LANDING_PATH_MAX)


class AttributionSnapshot(BaseModel):
    """Full envelope. Stable across the visitor journey.

    ``first_touch`` is set once on the first inbound campaign URL and
    never overwritten. ``last_touch`` overwrites on every subsequent
    inbound campaign URL. ``session_id`` is a ULID generated client-side
    and kept stable for the cookie's lifetime; lets server-side analytics
    correlate a visitor's funnel events across requests.
    """

    model_config = ConfigDict(extra="ignore")

    v: int = Field(default=ATTRIBUTION_SCHEMA_VERSION)
    first_touch: AttributionTouch
    last_touch: AttributionTouch
    session_id: str | None = Field(default=None, max_length=_SESSION_ID_MAX)

    @model_validator(mode="after")
    def _enforce_envelope_size(self) -> AttributionSnapshot:
        """Reject payloads whose serialized form exceeds 4 KB.

        Defensive bound against a malicious / buggy client crafting an
        oversized cookie that would blow up the JSONB columns where
        snapshots land. Per-field max_length above catches each string
        individually; this catches the combined size including JSON
        overhead.
        """
        serialized = self.model_dump_json()
        if len(serialized.encode("utf-8")) > _ENVELOPE_MAX_BYTES:
            raise ValueError(
                f"attribution envelope exceeds {_ENVELOPE_MAX_BYTES} bytes"
            )
        return self
