"""Marketing campaign entity — Phase 8.6.

Covers EMAIL + SMS broadcasts. WhatsApp campaigns keep their existing
dedicated table (`whatsapp_campaigns`) since their flow is different
(template approval, per-message reads/deliveries from the WhatsApp
Business API webhook). The hub's Campaigns page lists both together
via a union view.

State machine:
    DRAFT      → being composed by the merchant
    SCHEDULED  → scheduled_at set; awaiting the Celery sweep
    SENDING    → sweep started — partial sends may be in flight
    COMPLETED  → all recipients processed
    FAILED     → sweep aborted (e.g. SMTP/Twilio outage)
    CANCELED   → merchant called off the send

Stock movement-style audit: every transition stamps a timestamp on
the campaign row so the hub's "Sent on Mar 12 at 10:00 GMT+2" copy
reads from a column, not a derived value.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class CampaignChannel(StrEnum):
    EMAIL = "email"
    SMS = "sms"


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    SENDING = "sending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


VALID_CAMPAIGN_TRANSITIONS: dict[CampaignStatus, list[CampaignStatus]] = {
    CampaignStatus.DRAFT: [
        CampaignStatus.SCHEDULED,
        CampaignStatus.SENDING,  # "send now" goes straight to SENDING
        CampaignStatus.CANCELED,
    ],
    CampaignStatus.SCHEDULED: [
        CampaignStatus.SENDING,
        CampaignStatus.CANCELED,
        CampaignStatus.DRAFT,  # un-schedule back to draft
    ],
    CampaignStatus.SENDING: [
        CampaignStatus.COMPLETED,
        CampaignStatus.FAILED,
        # Merchant can cancel mid-flight. The dispatch task polls the
        # status before each recipient send and short-circuits when it
        # sees CANCELED — see _dispatch_campaign_async.
        CampaignStatus.CANCELED,
    ],
    CampaignStatus.COMPLETED: [],
    CampaignStatus.FAILED: [],
    CampaignStatus.CANCELED: [],
}


class MarketingCampaign(BaseEntity):
    tenant_id: UUID
    store_id: UUID
    channel: CampaignChannel
    name: str = Field(min_length=1, max_length=255)
    status: CampaignStatus = CampaignStatus.DRAFT
    # Template reference. For EMAIL channel, references
    # `email_templates.id`. For SMS, references `sms_templates.id`
    # (Phase 8.6 ships a stub `sms_templates` row shape via the
    # existing email_template machinery for now — a dedicated
    # sms_templates table lands when there's > 1 SMS template per
    # store).
    template_id: UUID | None = None
    # Inline body when not using a template (transactional one-off
    # broadcasts).
    inline_subject: str | None = None
    inline_body: str | None = None
    # Audience targeting. References a CustomerSegment (Phase 8.7) by
    # id, or carries an inline filter dict. v1 supports just an
    # inline filter: `{rfm: 'champion'}`, `{tags: ['vip']}`, etc.
    # Phase 8.7's segment rule engine will replace this with a
    # segment_id FK.
    segment_id: UUID | None = None
    audience_filter: dict[str, Any] | None = None
    # Scheduling. NULL when status=DRAFT.
    scheduled_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    canceled_at: datetime | None = None
    # Counters. Updated by the Celery runner as messages are dispatched.
    total_recipients: int = 0
    sent_count: int = 0
    delivered_count: int = 0  # SMS only — email uses sent_count
    failed_count: int = 0
    # Free-form merchant note.
    note: str | None = None
    # "What is this campaign promoting?" — drives both the message body
    # template (Vionne-styled email, generated client-side at v1; will
    # move to /marketing/templates/preview when feature-007 lands) and
    # the trackable-link panel auto-prefill in the hub.
    #
    # Shape: {
    #   "kind": "product" | "collection" | "page",
    #   "ref_id": "<product_id | collection_slug | page_path>",
    #   "snapshot": {
    #     "name": "...",
    #     "image_url": "...",
    #     "price": "...",
    #     "currency": "EGP",
    #     "url": "https://<store>.numueg.app/..."
    #   }
    # }
    #
    # Snapshot is cached at create / update time so a campaign's preview
    # always shows what the customer received — even if the underlying
    # product is renamed or repriced after the send. Stored verbatim;
    # backend doesn't re-resolve. Hub-side picker pre-fills the snapshot.
    promoted_item: dict[str, Any] | None = None
    created_by: UUID | None = None
    # Stable Crockford base32 identifier embedded in trackable-link
    # utm_campaign values. Generated server-side at create time so links
    # survive campaign renames. Per-store uniqueness is enforced at the
    # DB level (see uq_campaigns_store_short_code).
    short_code: str = Field(min_length=6, max_length=8)
    # Meta Custom Conversion id auto-created at send time when Meta is
    # connected. Lets Ads Manager filter Purchase events by this
    # campaign's UTM without the merchant manually creating a Custom
    # Conversion. NULL when Meta isn't connected, the create call
    # hasn't fired yet, or it failed (best-effort, never blocks send).
    # See ``meta_custom_conversion_service.py`` + the send-now flow.
    meta_custom_conversion_id: str | None = None

    def can_transition_to(self, target: CampaignStatus) -> bool:
        return target in VALID_CAMPAIGN_TRANSITIONS.get(self.status, [])
