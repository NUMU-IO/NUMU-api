"""GOWA (go-whatsapp-web-multidevice) WhatsApp transport.

A second transport alongside :class:`WhatsAppMessagingService`. Where that one
talks to Meta's Cloud API, this one talks to a self-hosted GOWA instance, which
drives the WhatsApp **Web multi-device** protocol via ``whatsmeow``.

## What this buys, and what it costs

It sends free-form text, so nothing here waits on Meta template approval, and
it is not subject to Meta's marketing frequency cap (error 131049) that limits
abandoned-cart nudges to the first one. There is no per-conversation fee.

The cost is that this is an **unofficial** transport. GOWA logs in as a regular
WhatsApp account by scanning a QR / entering a pairing code, so the number in
use is exposed to the same enforcement any automated account is: WhatsApp can
ban it, and for a BYO merchant that is the number their customers know. That
tradeoff is a per-merchant decision made in the admin backoffice; this module
just implements the transport.

## Differences from the Meta transport, which the caller must expect

* **No interactive buttons.** whatsmeow cannot send quick replies, so system
  templates are rendered to plain text with a numbered reply list (see
  ``core.whatsapp_plain_render``). The inbound webhook maps the digit back to
  the action the button would have carried.
* **No status polling.** Meta answers ``GET /{message_id}``; GOWA reports
  delivery only by pushing ``message.ack`` webhooks. ``get_message_status``
  therefore reads what the webhook last persisted rather than calling out.
* **Templates without local copy cannot be sent.** The body text for a Meta
  template lives in Meta's store, not ours. When
  ``render_plain_template`` has no copy it returns ``None`` and we fail the
  send instead of improvising a message the merchant never approved.

## Device model

One GOWA instance hosts many logged-in accounts, selected per request by the
``X-Device-Id`` header. A store's device id comes from the pairing record in
``whatsapp_gowa_device`` and is injected by the resolver, so this class never
guesses which account it is sending as — a wrong guess would send one
merchant's message from another merchant's number.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any

import httpx

from src.config.settings import settings
from src.core.interfaces.services.messaging_service import (
    EGYPTIAN_TEMPLATES,
    MessageChannel,
    MessageContent,
    MessageRecipient,
    MessageResult,
    MessageStatus,
    MessageType,
)
from src.core.whatsapp_plain_render import render_plain_template
from src.infrastructure.external_services.whatsapp.gowa_guard import (
    FAILURE_STREAK_PAUSE,
    GowaSendGuard,
    GuardDecision,
)

logger = logging.getLogger(__name__)

# GOWA addresses chats by JID. For a 1:1 chat that is "<digits>@s.whatsapp.net".
_USER_JID_SUFFIX = "@s.whatsapp.net"

# A send that hangs is worse than one that fails: the caller is usually a Celery
# task holding a DB session, and the notification is time-sensitive anyway.
_TIMEOUT_SECONDS = 20.0


class GowaProvider:
    """WhatsApp transport backed by a self-hosted GOWA instance.

    Structurally compatible with :class:`WhatsAppMessagingService` for the
    methods the notification layer calls, so ``get_whatsapp_service`` can
    return either one without the callers knowing which.
    """

    def __init__(
        self,
        device_id: str,
        *,
        base_url: str | None = None,
        basic_auth: str | None = None,
        webhook_secret: str | None = None,
        is_own: bool = False,
        db_session: Any = None,
        store_id: Any = None,
        tenant_id: Any = None,
        paired_at: Any = None,
        device_status: str | None = None,
        store_settings: dict | None = None,
        guard: GowaSendGuard | None = None,
    ) -> None:
        self.device_id = device_id
        self.base_url = (base_url or settings.gowa_base_url or "").rstrip("/")
        self.basic_auth = basic_auth or settings.gowa_basic_auth
        self.webhook_secret = webhook_secret or settings.gowa_webhook_secret
        self._is_own = is_own
        self.enabled = bool(self.base_url and device_id)
        # Supplied by `get_whatsapp_service`, which already holds all three.
        # Needed to record what each digit of a numbered prompt means — without
        # them a reply cannot be correlated back to an order, so `send_message`
        # refuses to send a prompt it knows nobody could answer.
        self.db_session = db_session
        self.store_id = store_id
        self.tenant_id = tenant_id
        # Guard inputs. `paired_at` drives the warm-up ramp, `device_status`
        # the health gate, `store_settings` the message-type allowlist — all
        # already on the device row the resolver just read.
        self.paired_at = paired_at
        self.device_status = device_status
        self.store_settings = store_settings
        # Only guard real sends. A bare instance (webhook signature checks)
        # has no device to pace.
        self._guard = guard or (GowaSendGuard() if device_id else None)

    # ── identity ────────────────────────────────────────────────────────────

    @property
    def channel(self) -> MessageChannel:
        return MessageChannel.WHATSAPP

    @property
    def connection_type(self) -> str:
        """'own' when the merchant paired their own number, else 'shared'.

        Same vocabulary the Meta transport reports, so analytics and the
        merchant hub don't have to special-case the provider.
        """
        return "own" if self._is_own else "shared"

    @property
    def provider(self) -> str:
        return "gowa"

    # ── plumbing ────────────────────────────────────────────────────────────

    def _auth(self) -> tuple[str, str] | None:
        if not self.basic_auth or ":" not in self.basic_auth:
            return None
        user, _, password = self.basic_auth.partition(":")
        return (user, password)

    def _headers(self) -> dict[str, str]:
        # X-Device-Id is what selects WHICH logged-in account sends. Omitting it
        # makes GOWA reject the call (DEVICE_ID_REQUIRED) — which is the safe
        # failure, but we should never get there.
        return {"X-Device-Id": self.device_id}

    @staticmethod
    def _to_jid(phone: str) -> str:
        """Canonical E.164 -> WhatsApp user JID.

        Inbound contract matches the Meta transport: ``phone`` is the canonical
        E.164 string written by ``PhoneField``. whatsmeow wants bare digits with
        no ``+``, suffixed with the user-JID domain.
        """
        digits = "".join(ch for ch in (phone or "") if ch.isdigit())
        return f"{digits}{_USER_JID_SUFFIX}"

    async def _post(self, path: str, payload: dict[str, Any]) -> MessageResult:
        """POST to GOWA and normalise the reply into a MessageResult."""
        if not self.enabled:
            return MessageResult(
                success=False,
                error_message="GOWA transport is not configured for this store.",
                error_code="gowa_not_configured",
            )
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{self.base_url}{path}",
                    json=payload,
                    headers=self._headers(),
                    auth=self._auth(),
                )
        except httpx.HTTPError as exc:
            logger.warning(
                "gowa_send_transport_error",
                extra={"device_id": self.device_id, "error": str(exc)},
            )
            return MessageResult(
                success=False,
                error_message=str(exc),
                error_code="gowa_transport_error",
            )

        try:
            body = response.json()
        except ValueError:
            body = {}

        if response.status_code >= 400:
            # GOWA reports its own failures as {"code": "...", "message": "..."}.
            code = str(body.get("code") or response.status_code)
            logger.warning(
                "gowa_send_failed",
                extra={
                    "device_id": self.device_id,
                    "status": response.status_code,
                    "code": code,
                },
            )
            return MessageResult(
                success=False,
                error_message=str(body.get("message") or response.text)[:500],
                error_code=code,
            )

        results = body.get("results") or {}
        message_id = results.get("message_id") or results.get("id")
        # SENT, not DELIVERED: whatsmeow has handed it to WhatsApp, but delivery
        # is only known once a `message.ack` webhook arrives.
        return MessageResult(
            success=True,
            message_id=str(message_id) if message_id else None,
            status=MessageStatus.SENT,
        )

    # ── sending ─────────────────────────────────────────────────────────────

    async def send_text_message(
        self,
        recipient: MessageRecipient,
        text: str,
        *,
        message_type: str | None = None,
    ) -> MessageResult:
        """Send free-form text.

        The whole reason this transport exists: no template, no approval, no
        24-hour session window.

        Every send passes the guard first. Meta polices its own traffic; here
        the only thing between a notification loop and a banned merchant number
        is that check, so it is applied on the single path all sends funnel
        through rather than at each call site.
        """
        decision = await self._guard_check(message_type)
        if not decision.allowed:
            logger.warning(
                "gowa_send_blocked",
                extra={
                    "device_id": self.device_id,
                    "reason": decision.reason,
                    "message_type": message_type,
                },
            )
            return MessageResult(
                success=False,
                error_message=decision.detail or "Blocked by GOWA send guard.",
                error_code=decision.reason or "gowa_guard_blocked",
            )

        # Randomised spacing. Perfectly-timed sends are the cheapest automation
        # fingerprint there is, and the cost of a few seconds' delay on an order
        # notification is nil next to losing the merchant's number.
        if decision.delay_seconds:
            await asyncio.sleep(decision.delay_seconds)

        result = await self._post(
            "/send/message",
            {"phone": self._to_jid(recipient.phone), "message": text},
        )

        # Feed the health signal. A sustained run of failures means something is
        # wrong with the session; pushing harder into that is how a shaky
        # device becomes a dead one.
        if self._guard is not None:
            if result.success:
                await self._guard.record_success(self.device_id)
            else:
                streak = await self._guard.record_failure(self.device_id)
                if streak >= FAILURE_STREAK_PAUSE:
                    logger.error(
                        "gowa_device_paused_failure_streak",
                        extra={"device_id": self.device_id, "streak": streak},
                    )
        return result

    async def _guard_check(self, message_type: str | None) -> GuardDecision:
        """Run the send guard, defaulting to allow-with-jitter if unavailable.

        The guard is a risk control, not a correctness one. If it cannot run
        (no store context, Redis down), the merchant's order notifications must
        still go out — but the jitter is applied regardless, because that costs
        nothing and is the part that matters most.
        """
        if self._guard is None:
            return GuardDecision(allowed=True, delay_seconds=GowaSendGuard._jitter())
        return await self._guard.check(
            device_id=self.device_id,
            message_type=message_type,
            paired_at=self.paired_at,
            device_status=self.device_status,
            store_settings=self.store_settings,
        )

    async def send_media_message(
        self,
        recipient: MessageRecipient,
        media_url: str,
        caption: str = "",
    ) -> MessageResult:
        """Send an image by URL, with an optional caption."""
        return await self._post(
            "/send/image",
            {
                "phone": self._to_jid(recipient.phone),
                "image_url": media_url,
                "caption": caption,
            },
        )

    async def send_message(self, content: MessageContent) -> MessageResult:
        """Send a system template, rendered to plain text.

        Signature-compatible with the Meta transport so the notification layer
        is provider-agnostic.
        """
        language = content.recipient.language or "en"
        rendered = render_plain_template(
            content.type, language, content.template_params or {}
        )
        if rendered is None:
            # No approved copy on our side. Refusing beats inventing wording for
            # a customer-facing order notification.
            logger.warning(
                "gowa_template_copy_missing",
                extra={"message_type": str(content.type), "language": language},
            )
            return MessageResult(
                success=False,
                error_message=(
                    f"No plain-text copy for template '{content.type}' "
                    f"({language}); cannot send over GOWA."
                ),
                error_code="gowa_template_copy_missing",
            )

        # A numbered prompt is only useful if the reply can be traced back to
        # the order it refers to. Record that mapping BEFORE sending, so a
        # customer who replies instantly can never beat the write.
        if rendered.quick_reply_payloads:
            recorded = await self._record_pending_reply(content, rendered)
            if not recorded:
                # Sending anyway would put an unanswerable "reply 1 to confirm"
                # in front of a customer — the COD order would then sit
                # unconfirmed with the merchant believing they had asked.
                return MessageResult(
                    success=False,
                    error_message=(
                        "Cannot correlate numbered replies for "
                        f"'{content.type}' — refusing to send a prompt that "
                        "could not be answered."
                    ),
                    error_code="gowa_reply_correlation_unavailable",
                )

        return await self.send_text_message(content.recipient, rendered.text)

    async def _record_pending_reply(
        self, content: MessageContent, rendered: Any
    ) -> bool:
        """Persist digit -> payload for this recipient. True when stored."""
        if self.db_session is None or self.store_id is None:
            logger.error(
                "gowa_pending_reply_no_session",
                extra={"message_type": str(content.type)},
            )
            return False
        try:
            from src.infrastructure.repositories.whatsapp_gowa_pending_reply_repository import (  # noqa: E501
                WhatsAppGowaPendingReplyRepository,
            )

            await WhatsAppGowaPendingReplyRepository(self.db_session).record(
                tenant_id=self.tenant_id,
                store_id=self.store_id,
                phone=content.recipient.phone,
                message_type=str(content.type),
                payloads=rendered.quick_reply_payloads,
            )
            return True
        except Exception:
            logger.exception("gowa_pending_reply_write_failed")
            return False

    async def send_and_log(
        self,
        content: MessageContent,
        repo: Any,
        store_id: Any,
        tenant_id: Any = None,
    ) -> MessageResult:
        """Send and persist an OUTBOUND MessageLog. Mirrors the Meta transport.

        Signature-identical on purpose. The merchant hub's WhatsApp dashboard
        (sent / delivered / read counters, the daily chart, the recent-messages
        list) and the conversation inbox are all built on MessageLog and
        WhatsAppConversation. A transport that sends without writing those rows
        works perfectly and looks completely broken to the merchant: an empty
        inbox and zeroed stats on a store that is actively messaging customers.

        So callers keep calling `send_and_log` and neither they nor the hub
        need to know which transport is underneath.
        """
        result = await self.send_message(content)

        if result.success and result.message_id:
            templates = EGYPTIAN_TEMPLATES.get(content.type, {})
            template = templates.get(content.recipient.language) or templates.get("en")
            template_name = template.name if template else str(content.type)
            await self._log_outbound(
                repo,
                store_id=store_id,
                tenant_id=tenant_id,
                phone=content.recipient.phone,
                message_id=result.message_id,
                template_name=template_name,
                content=str(content.template_params),
            )
            # Keep the inbox in step with the dashboard: an outbound message
            # should surface a thread even before the customer replies.
            await self._upsert_conversation(
                store_id=store_id,
                tenant_id=tenant_id,
                phone=content.recipient.phone,
                name=content.recipient.name,
                preview=template_name,
            )

        return result

    async def _log_outbound(
        self,
        repo: Any,
        *,
        store_id: Any,
        tenant_id: Any,
        phone: str,
        message_id: str,
        template_name: str,
        content: str,
    ) -> None:
        """Persist an outbound log entry. Never breaks the send."""
        from src.core.entities.message_log import MessageDirection, MessageLog
        from src.core.entities.message_log import MessageStatus as LogStatus

        try:
            await repo.create(
                MessageLog(
                    tenant_id=tenant_id,
                    store_id=store_id,
                    phone=phone,
                    message_id=message_id,
                    direction=MessageDirection.OUTBOUND,
                    template_name=template_name,
                    content=content,
                    status=LogStatus.SENT,
                )
            )
        except Exception:
            logger.exception("gowa_outbound_log_failed")

    async def _upsert_conversation(
        self,
        *,
        store_id: Any,
        tenant_id: Any,
        phone: str,
        name: str | None,
        preview: str | None,
    ) -> None:
        """Touch the conversation thread. Best-effort — never breaks the send."""
        if self.db_session is None or store_id is None or tenant_id is None:
            return
        try:
            from src.infrastructure.repositories.whatsapp_conversation_repository import (  # noqa: E501
                WhatsAppConversationRepository,
            )

            await WhatsAppConversationRepository(self.db_session).upsert_on_message(
                store_id=store_id,
                tenant_id=tenant_id,
                phone=phone,
                name=name,
                message_preview=preview,
                direction="outbound",
            )
        except Exception:
            logger.exception("gowa_conversation_upsert_failed")

    # ── convenience wrappers (parity with the Meta transport) ───────────────

    async def send_order_confirmation(
        self,
        recipient: MessageRecipient,
        order_number: str,
        total: str,
        store_name: str,
    ) -> MessageResult:
        return await self.send_message(
            MessageContent(
                type=MessageType.ORDER_CONFIRMATION,
                recipient=recipient,
                template_params={
                    "customer_name": recipient.name or "",
                    "store_name": store_name,
                    "order_number": order_number,
                    "total": total,
                },
            )
        )

    async def send_shipping_notification(
        self,
        recipient: MessageRecipient,
        order_number: str,
        tracking_number: str,
        carrier: str = "Bosta",
    ) -> MessageResult:
        return await self.send_message(
            MessageContent(
                type=MessageType.ORDER_SHIPPED,
                recipient=recipient,
                template_params={
                    "customer_name": recipient.name or "",
                    "order_number": order_number,
                    "tracking_number": tracking_number,
                    "carrier": carrier,
                },
            )
        )

    async def send_delivery_notification(
        self,
        recipient: MessageRecipient,
        order_number: str,
        store_name: str,
    ) -> MessageResult:
        return await self.send_message(
            MessageContent(
                type=MessageType.ORDER_DELIVERED,
                recipient=recipient,
                template_params={
                    "customer_name": recipient.name or "",
                    "order_number": order_number,
                    "store_name": store_name,
                },
            )
        )

    # ── status ──────────────────────────────────────────────────────────────

    async def get_message_status(self, message_id: str) -> MessageStatus:
        """Last known status for a message.

        GOWA exposes no status endpoint — delivery arrives as ``message.ack``
        webhooks. The webhook route persists those against the outbound log, so
        the truthful answer without a DB session here is "we handed it over".
        Callers that need the live value read the message log, exactly as they
        already do for Meta's asynchronous status webhooks.
        """
        return MessageStatus.SENT

    # ── webhook ─────────────────────────────────────────────────────────────

    def verify_webhook_signature(self, payload: bytes, signature: str) -> dict | None:
        """Verify GOWA's HMAC-SHA256 webhook signature.

        Returns the parsed payload when the signature matches, else None. The
        secret is shared with the container via ``WHATSAPP_WEBHOOK_SECRET``.
        Compared with :func:`hmac.compare_digest` so a wrong signature can't be
        recovered by timing the comparison.
        """
        if not self.webhook_secret or not signature:
            return None
        expected = hmac.new(
            self.webhook_secret.encode(), payload, hashlib.sha256
        ).hexdigest()
        # Accept a "sha256=" prefix — several senders add one.
        provided = signature.split("=", 1)[-1].strip()
        if not hmac.compare_digest(expected, provided):
            logger.warning("gowa_webhook_signature_mismatch")
            return None
        try:
            return json.loads(payload)
        except ValueError:
            return None


def supported_message_types() -> list[MessageType]:
    """Message types this transport can actually render.

    The admin backoffice uses this to warn before switching a merchant over:
    anything absent here would fail to send on GOWA, so the operator sees the
    gap instead of discovering it when a customer doesn't get their message.
    """
    supported: list[MessageType] = []
    for message_type in EGYPTIAN_TEMPLATES:
        for language in ("en", "ar"):
            if render_plain_template(message_type, language, {}) is not None:
                supported.append(message_type)
                break
    return supported
