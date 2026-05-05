"""Email template renderer.

Single entry point used by the email-send pipeline. For a given
``(store_id, event_type, language)`` triple it returns a fully rendered
:class:`RenderedEmailDTO` — subject + HTML — ready to hand to
:class:`IEmailService`.

Behavior summary:

1. If ``store_id`` is given, look up the merchant's custom template
   (enabled-only) via ``IEmailTemplateRepository.get_for_send``.
2. If a custom template is found, render its subject / body **raw**.
   Merchants own 100% of the visual — no platform header / footer is
   forced on top of their HTML.
3. Otherwise fall back to the registry default (which is the body-only
   HTML from ``EMAIL_EVENT_REGISTRY``) and wrap it with the brand
   chrome from
   :mod:`src.infrastructure.external_services.resend.email_templates._base`
   so customers of merchants who haven't customized still get a
   styled email.
4. All Jinja rendering happens inside a sandboxed environment with
   autoescape and ``ChainableUndefined`` so missing nested attributes
   render empty rather than crashing the entire pipeline.
5. Variables are filtered to the registry-declared whitelist plus the
   global ``store_name`` so a merchant's typo or rogue passthrough
   doesn't leak unrelated context.
6. Any rendering failure falls back to the registry default; if even
   that fails (shouldn't — startup validates the registry) we return a
   static safe message so customers always get *something*.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from jinja2 import ChainableUndefined, select_autoescape
from jinja2.exceptions import TemplateError
from jinja2.sandbox import SandboxedEnvironment

from src.application.dto.email_template import RenderedEmailDTO
from src.application.services.email_template_registry import (
    EMAIL_EVENT_REGISTRY,
    allowed_variables,
    get_event_spec,
)
from src.application.services.email_template_sanitizer import strip_markdown_fences
from src.config.logging_config import get_logger
from src.core.entities.email_template import EmailTemplate
from src.core.interfaces.repositories.email_template_repository import (
    IEmailTemplateRepository,
)
from src.infrastructure.external_services.resend.email_templates._base import (
    header as _email_header,
)
from src.infrastructure.external_services.resend.email_templates._base import (
    wrap,
)

logger = get_logger(__name__)

# Variables every event implicitly allows on top of its declared list.
# ``store_name`` is referenced by virtually every template subject/body
# and is treated as a global by both the registry samples and the
# customer-facing notification pipeline.
_GLOBAL_VARS: set[str] = {"store_name"}

# Static last-resort fallback used only if the registry default itself
# fails to render. Should never happen — ``validate_registry()`` at
# startup catches malformed defaults — but we belt-and-brace it so the
# customer always gets *something*.
_STATIC_FALLBACK_SUBJECT = "An update from {{ store_name }}"
_STATIC_FALLBACK_HTML = "<p>You have a new update.</p>"


def _build_env() -> SandboxedEnvironment:
    """Build a fresh sandboxed Jinja env per render call.

    ``SandboxedEnvironment`` blocks dangerous attribute access (e.g.
    ``__class__`` traversal) — exactly the threat model when rendering
    merchant-supplied templates. ``autoescape=select_autoescape(...)``
    HTML-escapes variable substitutions so customer data with a
    ``<script>`` payload renders as text. ``ChainableUndefined`` means
    ``{{ user.name }}`` renders to empty when ``user`` is missing,
    instead of raising — important when sample data is incomplete.
    """
    return SandboxedEnvironment(
        autoescape=select_autoescape(["html", "htm"]),
        undefined=ChainableUndefined,
    )


class EmailTemplateRenderer:
    """Render an email template (custom or default) into final HTML."""

    def __init__(self, email_template_repo: IEmailTemplateRepository) -> None:
        self.repo = email_template_repo

    async def render(
        self,
        *,
        store_id: UUID | None,
        event_type: str,
        language: str,
        variables: dict[str, Any],
    ) -> RenderedEmailDTO:
        """Render the template for ``(store_id, event_type, language)``.

        Args:
            store_id: Owning store, or ``None`` for platform-level events
                (no merchant override possible — uses registry default).
            event_type: Stable event identifier (registry key).
            language: ``"ar"`` or ``"en"``. Falls through to registry
                default for that language if no custom template exists.
            variables: Render context. Filtered against the event's
                allowed-variables set before being passed to Jinja.

        Returns:
            :class:`RenderedEmailDTO` — fully rendered, safe to send.
        """
        # ── Step 1: lookup custom template ──────────────────────────
        template: EmailTemplate | None = None
        if store_id is not None:
            try:
                template = await self.repo.get_for_send(store_id, event_type, language)
            except Exception as exc:
                # Repo failure must NOT block customer notifications —
                # log and fall through to the registry default.
                logger.warning(
                    "email_template_lookup_failed",
                    event_type=event_type,
                    language=language,
                    store_id=str(store_id),
                    error=str(exc),
                )
                template = None

        # ── Step 2: resolve event spec (fail open) ──────────────────
        spec = None
        if event_type in EMAIL_EVENT_REGISTRY:
            spec = get_event_spec(event_type)
        else:
            logger.warning(
                "email_template_unknown_event",
                event_type=event_type,
                language=language,
                store_id=str(store_id) if store_id else None,
            )

        # ── Step 3: filter variables to whitelist ───────────────────
        if spec is not None:
            allowed = allowed_variables(event_type) | _GLOBAL_VARS
            filtered_vars: dict[str, Any] = {
                k: v for k, v in variables.items() if k in allowed
            }
        else:
            # Unknown event — accept only the global vars + a generic
            # ``message`` field so the static fallback can still render.
            allowed = _GLOBAL_VARS | {"message"}
            filtered_vars = {k: v for k, v in variables.items() if k in allowed}

        # ── Step 4: pick subject + body source ──────────────────────
        env = _build_env()

        if template is not None:
            subject_src = template.subject
            # Defensive: strip wrapping markdown code fences if a legacy
            # row predates the sanitizer fix or a merchant-side import
            # accidentally included them.
            body_src = strip_markdown_fences(template.html_body)
            used_custom = True
            template_id: UUID | None = template.id
            # Merchant owns 100% of the visual — render raw, no NUMU
            # header / footer forced on top.
            wrap_with_chrome = False
        elif spec is not None:
            subject_src = spec.default_subject.get(
                language, spec.default_subject.get("en", _STATIC_FALLBACK_SUBJECT)
            )
            body_src = spec.default_html.get(
                language, spec.default_html.get("en", _STATIC_FALLBACK_HTML)
            )
            used_custom = False
            template_id = None
            wrap_with_chrome = True
        else:
            subject_src = _STATIC_FALLBACK_SUBJECT
            body_src = _STATIC_FALLBACK_HTML
            used_custom = False
            template_id = None
            wrap_with_chrome = True

        # ── Step 5: render with fall-through on error ───────────────
        rendered_subject = self._safe_render(
            env=env,
            source=subject_src,
            variables=filtered_vars,
            kind="subject",
            event_type=event_type,
            language=language,
            template_id=template_id,
            store_id=store_id,
            spec=spec,
            fallback=_STATIC_FALLBACK_SUBJECT,
        )
        rendered_body = self._safe_render(
            env=env,
            source=body_src,
            variables=filtered_vars,
            kind="body",
            event_type=event_type,
            language=language,
            template_id=template_id,
            store_id=store_id,
            spec=spec,
            fallback=_STATIC_FALLBACK_HTML,
        )

        # ── Step 6: wrap with brand chrome ──────────────────────────
        if wrap_with_chrome:
            try:
                # Prepend the branded dark header bar with the NUMU
                # wordmark and the event's human-readable title — keeps
                # every email visually consistent regardless of whether
                # the body comes from the registry or a merchant edit.
                title = (
                    spec.label_ar
                    if (spec is not None and language == "ar")
                    else spec.label_en
                    if spec is not None
                    else "NUMU"
                )
                header_html = _email_header(title=title, language=language)
                rendered_body = wrap(header_html + rendered_body, language=language)
            except Exception as exc:
                # Wrap failure shouldn't happen but should never block.
                logger.warning(
                    "email_template_wrap_failed",
                    event_type=event_type,
                    language=language,
                    store_id=str(store_id) if store_id else None,
                    error=str(exc),
                )

        return RenderedEmailDTO(
            subject=rendered_subject,
            html=rendered_body,
            from_name=template.from_name if template else None,
            reply_to=template.reply_to if template else None,
            used_custom=used_custom,
            template_id=template_id,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _safe_render(
        self,
        *,
        env: SandboxedEnvironment,
        source: str,
        variables: dict[str, Any],
        kind: str,
        event_type: str,
        language: str,
        template_id: UUID | None,
        store_id: UUID | None,
        spec: Any,
        fallback: str,
    ) -> str:
        """Render ``source`` against ``variables`` with cascading fallback.

        On error: log → fall back to the registry default for this
        ``(event_type, language)`` → if that also fails, fall back to a
        static safe string. The renderer NEVER raises.
        """
        try:
            return env.from_string(source).render(**variables)
        except (TemplateError, Exception) as exc:  # noqa: BLE001
            logger.warning(
                "email_template_render_failed",
                event_type=event_type,
                language=language,
                template_id=str(template_id) if template_id else None,
                store_id=str(store_id) if store_id else None,
                kind=kind,
                error=str(exc),
            )

        # First-tier fallback: registry default for this language
        if spec is not None:
            try:
                if kind == "subject":
                    default_src = spec.default_subject.get(
                        language, spec.default_subject.get("en", fallback)
                    )
                else:
                    default_src = spec.default_html.get(
                        language, spec.default_html.get("en", fallback)
                    )
                return env.from_string(default_src).render(**variables)
            except Exception as exc:  # pragma: no cover — startup validates
                logger.error(
                    "email_template_default_render_failed",
                    event_type=event_type,
                    language=language,
                    kind=kind,
                    error=str(exc),
                )

        # Second-tier fallback: static safe string
        try:
            return env.from_string(fallback).render(**variables)
        except Exception:  # pragma: no cover
            return fallback


__all__ = ["EmailTemplateRenderer"]
