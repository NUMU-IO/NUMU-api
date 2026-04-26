"""Send test email use case.

Lets a merchant validate a template by emailing themselves a rendered
preview. The recipient is locked to the requesting merchant's own email
to prevent abuse (e.g. a hijacked session blasting tests at arbitrary
addresses). Subject is prefixed with ``[TEST]`` so the merchant always
recognises the message in their inbox.

Rate limiting is per-process (``cachetools.TTLCache``) keyed by
``(user_id, template_id)`` — 5 sends per 60s. Process-local is fine for
MVP since the surface is small and even 5×worker fleet sends per minute
won't actually hit upstream Resend rate limits.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from cachetools import TTLCache

from src.application.dto.email_template import RenderedEmailDTO
from src.application.services.email_template_registry import get_event_spec
from src.application.services.email_template_renderer import EmailTemplateRenderer
from src.core.exceptions import (
    AuthorizationError,
    EntityNotFoundError,
    ValidationError,
)
from src.core.interfaces.repositories.email_template_repository import (
    IEmailTemplateRepository,
)
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.core.interfaces.services.email_service import EmailMessage, IEmailService

# Process-local rate limiter. Each entry tracks the count of test sends
# for ``(user_id, template_id)`` over the last 60 seconds. The TTL on
# the cache itself bounds growth — entries auto-expire.
_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_MAX = 5
_rate_limit_counter: TTLCache[tuple[str, str], int] = TTLCache(
    maxsize=4096, ttl=_RATE_LIMIT_WINDOW_SECONDS
)


def _check_and_increment_rate_limit(user_id: UUID, template_id: UUID) -> None:
    """Raise :class:`ValidationError` once the per-window budget is spent.

    Note: per-process. With multiple workers a determined caller can
    multiply the limit by worker count, but the upstream provider has
    its own rate limiting and the abuse surface here is small.
    """
    key = (str(user_id), str(template_id))
    current = _rate_limit_counter.get(key, 0)
    if current >= _RATE_LIMIT_MAX:
        raise ValidationError(
            f"Rate limit exceeded: max {_RATE_LIMIT_MAX} test sends per "
            f"{_RATE_LIMIT_WINDOW_SECONDS}s for this template"
        )
    _rate_limit_counter[key] = current + 1


class SendTestEmailUseCase:
    """Use case for sending a test email rendered from a saved template."""

    def __init__(
        self,
        email_template_repository: IEmailTemplateRepository,
        store_repository: IStoreRepository,
        email_service: IEmailService,
        renderer: EmailTemplateRenderer,
    ) -> None:
        self.email_template_repository = email_template_repository
        self.store_repository = store_repository
        self.email_service = email_service
        self.renderer = renderer

    async def execute(
        self,
        store_id: UUID,
        template_id: UUID,
        recipient: str,
        variables: dict[str, Any] | None,
        user_id: UUID,
        user_email: str,
    ) -> dict[str, Any]:
        # ── Auth ────────────────────────────────────────────────────
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))
        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to send test emails for this store"
            )

        template = await self.email_template_repository.get_by_id(template_id)
        if not template or template.store_id != store_id:
            raise EntityNotFoundError("EmailTemplate", str(template_id))

        # ── Recipient must be the requesting merchant ───────────────
        if (recipient or "").strip().lower() != (user_email or "").strip().lower():
            raise ValidationError(
                "Test emails can only be sent to your own account email address"
            )

        # ── Rate limit ──────────────────────────────────────────────
        _check_and_increment_rate_limit(user_id, template_id)

        # ── Render via the runtime renderer ─────────────────────────
        spec = get_event_spec(template.event_type)
        render_vars = dict(variables) if variables else dict(spec.sample_data)
        # Always override store_name so previews carry the merchant's
        # actual store name, not the registry's "Cairo Threads" sample.
        render_vars.setdefault("store_name", store.name)

        rendered: RenderedEmailDTO = await self.renderer.render(
            store_id=store_id,
            event_type=template.event_type,
            language=template.language,
            variables=render_vars,
        )

        test_subject = f"[TEST] {rendered.subject}"

        # ── Dispatch via the configured email service ───────────────
        # ``EmailMessage.html_content`` (not ``html``) is the actual
        # field name on the dataclass — we adapt the renderer's output
        # to match.
        message = EmailMessage(
            to=recipient,
            subject=test_subject,
            html_content=rendered.html,
            from_name=rendered.from_name,
            reply_to=rendered.reply_to,
        )
        sent = await self.email_service.send_email(message)

        return {
            "sent": bool(sent),
            "message_id": None,  # Resend client doesn't surface ID today
            "subject": test_subject,
            "used_custom": rendered.used_custom,
            "template_id": str(template.id),
        }
