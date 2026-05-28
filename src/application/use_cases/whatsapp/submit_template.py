"""Submit a new WhatsApp template to Meta (FR-026/027, EDIT-C, US5).

Phase 1 rule per spec FR-026 (analyze finding HIGH-3 / EDIT-C):
custom template submission is **BYO-only**. Stores in ``platform_managed``
mode share NUMU's single Meta WABA, so allowing per-merchant submissions
would leak templates across stores and exhaust the WABA's approval-rate
budget. Platform-managed stores rely on the seeded canonical system
templates (FR-030).

Behavior:
- Store mode = ``platform_managed`` → raise ``TemplateSubmissionForbidden``
  with code ``template_submission_requires_byo``. No Meta call, no local row.
- Store mode = ``byo`` → POST to Meta. On 4xx (FR-027), raise
  ``TemplateSubmissionRejected`` carrying the sanitized Meta error.
  No local row is written until Meta returns 200.
- Local duplicate check (name + language) runs BEFORE the Meta call so
  the merchant gets a 409 instead of a Meta name-collision error.
- On Meta success, persist a local ``whatsapp_templates`` row with
  status=PENDING (or Meta's reported status), meta_template_id set.

Body / button example values:
- Meta requires an ``example.body_text`` array for every BODY with one or
  more ``{{n}}`` placeholders (error code 100 / subcode 2388023). The
  caller can supply ``body_examples`` to provide realistic preview values
  (improves Meta's review pass-rate). When not supplied we auto-derive
  placeholder strings (sample 1, sample 2, …) so the submission still
  passes validation.
- Dynamic URL buttons need an ``example`` URL field too; buttons are
  passed through verbatim — callers using URL placeholders must include
  ``example`` in each button dict.
"""

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Match Meta's positional placeholders ``{{1}}``, ``{{12}}``, etc. The
# numeric value gives us the placeholder index used to deduplicate +
# size the example list.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\d+)\s*\}\}")

from src.core.services.meta_error_whitelist import sanitize_meta_error
from src.infrastructure.database.models.tenant.configuration import (
    ServiceName,
    ServiceType,
)
from src.infrastructure.database.models.tenant.whatsapp_template import (
    WhatsAppTemplateModel,
)
from src.infrastructure.external_services.meta.whatsapp_client import WhatsAppClient
from src.infrastructure.repositories.credential_repository import CredentialRepository


class TemplateSubmissionForbidden(Exception):
    """Raised when a platform-managed store tries to submit a template
    (EDIT-C / FR-026). Maps to HTTP 403 + ``template_submission_requires_byo``.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "template_submission_requires_byo"
        self.message = message


class TemplateSubmissionRejected(Exception):
    """Raised when Meta rejects the template (FR-027). Maps to HTTP 422
    with the sanitized Meta error body (TASK-SEC-009 — no fbtrace_id).
    """

    def __init__(
        self,
        *,
        meta_error: dict[str, Any] | None,
        http_status: int | None = None,
    ) -> None:
        super().__init__("Meta rejected the template submission.")
        self.meta_error = meta_error
        self.http_status = http_status


class TemplateDuplicateLocal(Exception):
    """Raised when a local template with the same (name, language) already
    exists. Maps to HTTP 409.
    """


class SubmitTemplateUseCase:
    """BYO-only template submission to Meta + local persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def execute(
        self,
        *,
        store_id: UUID,
        tenant_id: UUID,
        name: str,
        language: str,
        category: str,
        body_text: str,
        header_type: str | None = None,
        header_content: str | None = None,
        footer_text: str | None = None,
        buttons: list[dict[str, Any]] | None = None,
        body_examples: list[str] | None = None,
    ) -> WhatsAppTemplateModel:
        # 1. Local duplicate guard. Raise before any external call so the
        #    merchant doesn't burn a Meta approval-rate-budget slot.
        existing_local = (
            await self.session.execute(
                select(WhatsAppTemplateModel).where(
                    WhatsAppTemplateModel.store_id == store_id,
                    WhatsAppTemplateModel.name == name,
                    WhatsAppTemplateModel.language == language,
                )
            )
        ).scalar_one_or_none()
        if existing_local is not None:
            raise TemplateDuplicateLocal(
                f"Template '{name}' ({language}) already exists locally."
            )

        # 2. Mode gate (EDIT-C). Platform-managed stores cannot submit
        #    custom templates in Phase 1.
        cred_repo = CredentialRepository(self.session)
        creds = await cred_repo.get_decrypted_credentials(
            tenant_id=tenant_id,
            service_type=ServiceType.WHATSAPP,
            service_name=ServiceName.WHATSAPP_BUSINESS,
        )
        if not creds:
            raise TemplateSubmissionForbidden(
                "Custom template submission requires BYO mode. Connect a "
                "Meta WhatsApp Business Account first, or use one of the "
                "seeded system templates."
            )

        # 3. Build Meta payload (matches the contract in
        #    contracts/whatsapp-templates.openapi.yaml).
        components = _build_components(
            header_type=header_type,
            header_content=header_content,
            body_text=body_text,
            footer_text=footer_text,
            buttons=buttons,
            body_examples=body_examples,
        )
        meta_payload = {
            "name": name,
            "language": language,
            "category": category,
            "components": components,
        }

        # 4. Submit to Meta. On 4xx, surface sanitized error + DO NOT
        #    persist a local row (FR-027).
        meta_template_id: str | None = None
        meta_status: str = "PENDING"
        client = WhatsAppClient(
            phone_number_id=creds.get("phone_number_id", ""),
            access_token=creds["access_token"],
            waba_id=creds["waba_id"],
        )
        try:
            try:
                response = await client.submit_template(meta_payload)
            except httpx.HTTPStatusError as exc:
                # Meta synchronous rejection (FR-027).
                try:
                    body = exc.response.json()
                except Exception:
                    body = None
                raise TemplateSubmissionRejected(
                    meta_error=sanitize_meta_error(body) if body else None,
                    http_status=exc.response.status_code,
                ) from exc
            meta_template_id = response.get("id")
            # Meta sometimes returns an initial status; default to PENDING
            # for safety. Status updates flow via the webhook + polling
            # sync (FR-028).
            meta_status = response.get("status", "PENDING")
        finally:
            await client.close()

        # 5. Persist the local row only after Meta accepts.
        local = WhatsAppTemplateModel(
            store_id=store_id,
            tenant_id=tenant_id,
            meta_template_id=meta_template_id,
            name=name,
            language=language,
            category=category,
            status=meta_status,
            header_type=header_type,
            header_content=header_content,
            body_text=body_text,
            footer_text=footer_text,
            buttons=buttons,
            is_system=False,
            submitted_at=datetime.now(UTC),
        )
        self.session.add(local)
        await self.session.flush()
        await self.session.refresh(local)
        return local


def _placeholder_count(text: str) -> int:
    """Return the number of distinct ``{{n}}`` placeholders in ``text``.
    Counts by max index rather than match count so duplicated indices
    (``"{{1}} and {{1}}"``) collapse to a single example slot.
    """
    indices = {int(m.group(1)) for m in _PLACEHOLDER_RE.finditer(text)}
    return max(indices) if indices else 0


def _build_components(
    *,
    header_type: str | None,
    header_content: str | None,
    body_text: str,
    footer_text: str | None,
    buttons: list[dict[str, Any]] | None,
    body_examples: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Build Meta's ``components`` array from our flat schema.

    BODY components with placeholders MUST include an ``example`` block —
    Meta rejects the submission otherwise (error 100 subcode 2388023:
    ``"Body parameters must include example body_text"``). When the
    caller doesn't supply ``body_examples`` we synthesize placeholder
    values so the call still passes validation.
    """
    components: list[dict[str, Any]] = []

    if header_type and header_content:
        ht = header_type.upper()
        if ht == "TEXT":
            components.append({
                "type": "HEADER",
                "format": "TEXT",
                "text": header_content,
            })
        elif ht in ("IMAGE", "VIDEO", "DOCUMENT"):
            components.append({"type": "HEADER", "format": ht})

    body_component: dict[str, Any] = {"type": "BODY", "text": body_text}
    n_placeholders = _placeholder_count(body_text)
    if n_placeholders > 0:
        examples = list(body_examples or [])
        # Top up with synthetic values when the caller supplies fewer
        # examples than placeholders — keep Meta's validator happy.
        while len(examples) < n_placeholders:
            examples.append(f"sample {len(examples) + 1}")
        # Trim any extras so we don't accidentally encode an
        # off-by-one in Meta's preview.
        examples = examples[:n_placeholders]
        body_component["example"] = {"body_text": [examples]}
    components.append(body_component)

    if footer_text:
        components.append({"type": "FOOTER", "text": footer_text})

    if buttons:
        components.append({"type": "BUTTONS", "buttons": buttons})

    return components
