"""Marketing: reach a lead, keep the copy, run the referral programme.

URL: /api/v1/admin/marketing — requires SUPER_ADMIN.

`merchant_leads` has recorded everyone who reached for NUMU since the
acquisition funnel shipped, and there was no way to send any of them anything.
Follow-up happened in someone's personal WhatsApp, which meant it was neither
repeatable nor recorded — nobody could answer "did we ever contact this
merchant, and what did we say".

Three surfaces, one page:

  * SEND     — pick leads, pick a template. Email goes out through Resend.
               WhatsApp does NOT: it produces a `wa.me` link per lead with the
               message already typed, and the operator sends it from their own
               WhatsApp. See `_whatsapp_link` for why. Every send is written to
               `marketing_outreach` with the RENDERED body, because a template
               edited next month must not rewrite what we actually said.
  * TEMPLATES — the copy, editable here rather than pasted from a document.
  * REFERRALS — the reward ledger and the amounts behind it.

Email goes out as hello@numueg.app rather than the platform's transactional
sender: a promotion and a password reset should not share a reputation, and a
merchant replying to a follow-up should reach a person.
"""

import json
import logging
import re
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.application.services import referral_service
from src.application.services.marketing_links import new_token, rewrite_links
from src.config import settings
from src.infrastructure.external_services.resend.email_templates.marketing import (
    render_marketing_email,
)

logger = logging.getLogger(__name__)

router = APIRouter()

Channel = Literal["email", "whatsapp"]

#: The sender for everything on this page. Marketing must not borrow the
#: transactional domain's reputation, and a merchant who hits reply should
#: reach a mailbox someone reads.
MARKETING_FROM_EMAIL = "hello@numueg.app"
MARKETING_FROM_NAME = "NUMU"

#: Where a shared referral link points. Public signup, with the code carried
#: in the query so the landing page can attribute it.
REFERRAL_LINK_BASE = "https://numueg.app/signup?ref="

#: Base for the click-tracking redirect. On the sending domain deliberately —
#: a tracking host that differs from the From domain is the mismatch Resend's
#: own deliverability report flags. `/api/v1/public/r` rather than a bare `/r`
#: because that route exists today; shortening it is one nginx alias, not a
#: reason to hold the feature.
CLICK_BASE_URL = "https://numueg.app/api/v1/public/r"

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


def render(body: str, variables: dict[str, str]) -> str:
    """Substitute `{{name}}`-style placeholders.

    An UNKNOWN placeholder is left exactly as written rather than blanked.
    Blanking is how a merchant receives "Hi ," — leaving it visible means the
    operator sees their own typo in the preview and fixes the template.
    """
    return _PLACEHOLDER.sub(lambda m: variables.get(m.group(1), m.group(0)), body)


# ── Templates ────────────────────────────────────────────────────────────────


class Template(BaseModel):
    id: str
    key: str
    channel: Channel
    language: str
    name: str
    subject: str | None
    body: str
    is_active: bool
    updated_at: datetime


class TemplateWrite(BaseModel):
    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    channel: Channel
    language: str = Field(default="en", max_length=5)
    name: str = Field(min_length=1, max_length=160)
    subject: str | None = Field(default=None, max_length=300)
    body: str = Field(min_length=1)
    is_active: bool = True


class TemplatePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    subject: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, min_length=1)
    is_active: bool | None = None


def _template(row) -> Template:
    return Template(
        id=str(row["id"]),
        key=row["key"],
        channel=row["channel"],
        language=row["language"],
        name=row["name"],
        subject=row["subject"],
        body=row["body"],
        is_active=row["is_active"],
        updated_at=row["updated_at"],
    )


@router.get(
    "/templates",
    response_model=SuccessResponse[list[Template]],
    summary="List marketing templates",
    operation_id="admin_list_marketing_templates",
)
async def list_templates(
    _admin: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    channel: Channel | None = None,
):
    clause = "WHERE channel = :channel" if channel else ""
    rows = (
        await db.execute(
            text(
                "SELECT id, key, channel, language, name, subject, body, is_active, "
                "updated_at FROM public.marketing_templates "
                f"{clause} ORDER BY key, channel"  # nosec B608 - literal clause; no caller value interpolated
            ),
            {"channel": channel} if channel else {},
        )
    ).mappings()
    return SuccessResponse(data=[_template(r) for r in rows])


@router.post(
    "/templates",
    response_model=SuccessResponse[Template],
    status_code=http_status.HTTP_201_CREATED,
    summary="Create a marketing template",
    operation_id="admin_create_marketing_template",
)
async def create_template(
    payload: TemplateWrite,
    admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if payload.channel == "email" and not (payload.subject or "").strip():
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="An email template needs a subject line.",
        )

    row = (
        (
            await db.execute(
                text(
                    "INSERT INTO public.marketing_templates "
                    "(key, channel, language, name, subject, body, is_active, updated_by) "
                    "VALUES (:key, :channel, :language, :name, :subject, :body, "
                    "        :is_active, :admin) "
                    "ON CONFLICT (key, channel, language) DO NOTHING "
                    "RETURNING id, key, channel, language, name, subject, body, "
                    "          is_active, updated_at"
                ),
                {**payload.model_dump(), "admin": str(admin_id)},
            )
        )
        .mappings()
        .first()
    )

    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                f"A {payload.channel} template already exists for "
                f"'{payload.key}' in {payload.language}."
            ),
        )
    await db.commit()
    return SuccessResponse(data=_template(row))


@router.patch(
    "/templates/{template_id}",
    response_model=SuccessResponse[Template],
    summary="Edit a marketing template",
    operation_id="admin_update_marketing_template",
)
async def update_template(
    template_id: UUID,
    payload: TemplatePatch,
    admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST, detail="Nothing to update."
        )

    # Column names come from the model's own field names, never from the
    # request body; values are all bound.
    assignments = ", ".join(f"{name} = :{name}" for name in fields)
    row = (
        (
            await db.execute(
                text(
                    f"UPDATE public.marketing_templates SET {assignments}, "  # nosec B608 - assignment names come from the schema, not the request
                    "updated_by = :admin, updated_at = now() WHERE id = :id "
                    "RETURNING id, key, channel, language, name, subject, body, "
                    "          is_active, updated_at"
                ),
                {**fields, "admin": str(admin_id), "id": str(template_id)},
            )
        )
        .mappings()
        .first()
    )

    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="Template not found."
        )
    await db.commit()
    return SuccessResponse(data=_template(row))


@router.delete(
    "/templates/{template_id}",
    response_model=SuccessResponse[dict],
    summary="Delete a marketing template",
    operation_id="admin_delete_marketing_template",
)
async def delete_template(
    template_id: UUID,
    _admin: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        text("DELETE FROM public.marketing_templates WHERE id = :id"),
        {"id": str(template_id)},
    )
    if not result.rowcount:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="Template not found."
        )
    await db.commit()
    return SuccessResponse(data={"deleted": True})


# ── Sending ──────────────────────────────────────────────────────────────────


class SendRequest(BaseModel):
    lead_ids: list[UUID] = Field(min_length=1, max_length=200)
    channel: Channel
    #: "en", "ar", or None for per-lead. None is the default and the right
    #: answer: `merchant_leads.language` records which language the merchant
    #: actually used, and writing to an Egyptian merchant in English because
    #: the operator's own UI is English is how outreach gets ignored.
    language: Literal["en", "ar"] | None = None
    #: One of the two must be given. `template_key` is the normal path;
    #: `subject`/`body` let an operator send a one-off without saving copy
    #: they will never use again.
    template_key: str | None = Field(default=None, max_length=64)
    subject: str | None = Field(default=None, max_length=300)
    body: str | None = None


class SendResult(BaseModel):
    sent: int
    failed: int
    skipped: int
    #: Per-lead outcome, so the operator sees WHICH merchant was missed and
    #: why rather than a bare "3 of 40 failed".
    details: list[dict]


class Preview(BaseModel):
    recipient: str
    subject: str | None
    body: str
    #: WhatsApp only: the click-to-chat link, with the message already typed.
    whatsapp_url: str | None = None


@router.post(
    "/preview",
    response_model=SuccessResponse[Preview],
    summary="Render a template against one lead without sending",
    operation_id="admin_preview_marketing_message",
)
async def preview(
    payload: SendRequest,
    _admin: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """What the first selected lead would receive.

    Separate from send so an operator can read their own copy with real
    substitutions in it before it reaches forty merchants. Placeholders are
    where marketing copy goes wrong, and they are invisible in the editor.
    """
    lead = await _load_lead(db, payload.lead_ids[0])
    if lead is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="Lead not found."
        )
    subject, body = await _resolve_copy(
        db, payload, payload.language or _language_of(lead)
    )

    variables = await _variables(db, lead)
    recipient = _recipient(lead, payload.channel)
    rendered_body = render(body, variables)
    # Email preview is the branded email, not the bare copy: an operator
    # approving text that then ships inside a shell has not seen what they
    # approved. Links are NOT rewritten here — a preview must not consume a
    # tracking token, and a click from the operator is not a merchant's.
    preview_body = (
        render_marketing_email(
            body=rendered_body, language=payload.language or _language_of(lead)
        )
        if payload.channel == "email"
        else rendered_body
    )
    return SuccessResponse(
        data=Preview(
            recipient=recipient or "(no address on this lead)",
            subject=render(subject, variables) if subject else None,
            body=preview_body,
            whatsapp_url=(
                _whatsapp_link(recipient, rendered_body)
                if payload.channel == "whatsapp" and recipient
                else None
            ),
        )
    )


@router.post(
    "/send",
    response_model=SuccessResponse[SendResult],
    summary="Send a message to selected leads",
    operation_id="admin_send_marketing_message",
)
async def send(
    payload: SendRequest,
    admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Send to each selected lead, one at a time, logging every outcome.

    Sequential rather than a fan-out task: an operator picks a handful of
    leads and needs to know NOW which ones went. A background job would give
    them a 202 and no answer.

    One lead's failure never stops the rest — a merchant with no WhatsApp
    number must not cost the other thirty-nine their message.
    """
    # Resolved per language and cached: a batch of forty leads is at most two
    # template lookups, not forty.
    copy_cache: dict[str, tuple[str | None, str]] = {}

    async def copy_for(language: str) -> tuple[str | None, str]:
        if language not in copy_cache:
            copy_cache[language] = await _resolve_copy(db, payload, language)
        return copy_cache[language]

    # Fail before sending anything if the template is missing or off.
    await copy_for(payload.language or "en")

    sent = failed = skipped = 0
    details: list[dict] = []

    for lead_id in payload.lead_ids:
        lead = await _load_lead(db, lead_id)
        if lead is None:
            skipped += 1
            details.append({
                "lead_id": str(lead_id),
                "status": "skipped",
                "reason": "not found",
            })
            continue

        recipient = _recipient(lead, payload.channel)
        if not recipient:
            skipped += 1
            details.append({
                "lead_id": str(lead_id),
                "email": lead["email"],
                "status": "skipped",
                "reason": f"no {payload.channel} address on this lead",
            })
            await _log(
                db,
                lead_id=lead_id,
                recipient=lead["email"] or "",
                channel=payload.channel,
                template_key=payload.template_key,
                subject=None,
                body="",
                status="skipped",
                error=f"no {payload.channel} address",
                sent_by=admin_id,
            )
            continue

        variables = await _variables(db, lead)
        subject_template, body_template = await copy_for(
            payload.language or _language_of(lead)
        )
        rendered_subject = (
            render(subject_template, variables) if subject_template else None
        )
        rendered_body = render(body_template, variables)

        if payload.channel == "whatsapp":
            # Nothing is transmitted here. The operator gets a link with the
            # message already typed and sends it from their own WhatsApp — see
            # `_whatsapp_link`. The row records the copy that was prepared for
            # this merchant, which is what makes "have we contacted them, and
            # what did we say" answerable either way.
            link = _whatsapp_link(recipient, rendered_body)
            await _log(
                db,
                lead_id=lead_id,
                recipient=recipient,
                channel="whatsapp",
                template_key=payload.template_key,
                subject=None,
                body=rendered_body,
                status="sent",
                error=None,
                sent_by=admin_id,
            )
            sent += 1
            details.append({
                "lead_id": str(lead_id),
                "email": lead["email"],
                "name": lead["name"],
                "phone": recipient,
                "status": "sent",
                "whatsapp_url": link,
            })
            continue

        # The operator writes copy; the brand comes from here. Sending the raw
        # body was not a neutral default — an unstyled promotional email reads
        # as spam, and for many merchants this is the first thing NUMU ever
        # sends them.
        language = payload.language or _language_of(lead)
        branded = render_marketing_email(
            body=rendered_body,
            language=language,
            unsubscribe_url=None,
        )
        # One token per recipient, so a click is attributable to a person
        # rather than to a campaign.
        token = new_token()
        tracked_html, links = rewrite_links(
            branded, token=token, base_url=CLICK_BASE_URL
        )

        error = await _deliver(
            channel=payload.channel,
            recipient=recipient,
            subject=rendered_subject,
            body=tracked_html,
            # Plain text from the copy, not from the tracked HTML: the text
            # part should show a merchant the real URL, not a redirect with a
            # token in it. Clicks there go uncounted, which is the honest
            # trade for a readable fallback.
            text_body=_plain_text(rendered_body),
        )

        await _log(
            db,
            lead_id=lead_id,
            recipient=recipient,
            channel=payload.channel,
            template_key=payload.template_key,
            subject=rendered_subject,
            # The COPY, not the shell. "What did we say to this merchant" is
            # answered by the words; the shell is regenerated from them.
            body=rendered_body,
            status="failed" if error else "sent",
            error=error,
            sent_by=admin_id,
            click_token=None if error else token,
            links=[] if error else links,
        )

        if error:
            failed += 1
            details.append({
                "lead_id": str(lead_id),
                "email": lead["email"],
                "status": "failed",
                "reason": error,
            })
        else:
            sent += 1
            details.append({
                "lead_id": str(lead_id),
                "email": lead["email"],
                "status": "sent",
            })

    await db.commit()
    return SuccessResponse(
        data=SendResult(sent=sent, failed=failed, skipped=skipped, details=details)
    )


async def _resolve_copy(
    db: AsyncSession, payload: SendRequest, language: str = "en"
) -> tuple[str | None, str]:
    """The subject and body to send: a saved template, or a one-off.

    Falls back to English when the requested language has no version of this
    template. Sending the English copy is a worse fit than the Arabic; sending
    nothing is worse than both.
    """
    if payload.template_key:
        row = (
            (
                await db.execute(
                    text(
                        "SELECT subject, body, is_active FROM public.marketing_templates "
                        "WHERE key = :key AND channel = :channel "
                        "ORDER BY language = :language DESC, language = 'en' DESC "
                        "LIMIT 1"
                    ),
                    {
                        "key": payload.template_key,
                        "channel": payload.channel,
                        "language": language,
                    },
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"No {payload.channel} template named '{payload.template_key}'.",
            )
        if not row["is_active"]:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="That template is switched off.",
            )
        return row["subject"], row["body"]

    if not payload.body:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Give either a template_key or a body.",
        )
    if payload.channel == "email" and not (payload.subject or "").strip():
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="An email needs a subject line.",
        )
    return payload.subject, payload.body


async def _load_lead(db: AsyncSession, lead_id: UUID):
    return (
        (
            await db.execute(
                text(
                    "SELECT id, email, name, phone, whatsapp_phone, language, "
                    "referral_code, status FROM public.merchant_leads WHERE id = :id"
                ),
                {"id": str(lead_id)},
            )
        )
        .mappings()
        .first()
    )


def _language_of(lead) -> str:
    """Which language this merchant actually used. English when unknown.

    `merchant_leads.language` is whatever the landing page or dashboard was
    set to, so it can be "ar-EG" or "AR" as well as "ar".
    """
    return "ar" if (lead["language"] or "").lower().startswith("ar") else "en"


def _recipient(lead, channel: Channel) -> str | None:
    if channel == "email":
        return lead["email"]
    # WhatsApp first, then the ordinary phone — a merchant who gave one
    # number gave it for reaching them.
    return lead["whatsapp_phone"] or lead["phone"]


async def _variables(db: AsyncSession, lead) -> dict[str, str]:
    """Substitutions available to every template.

    `referral_link` mints the lead's code on demand, because the referral
    templates are the only place it is needed and generating one for every
    lead up front would fill the column for people who never see it.
    """
    code = lead["referral_code"]
    if not code:
        code = await referral_service.ensure_referral_code(db, lead["id"]) or ""

    return {
        # A first name reads as a person wrote it; the full string reads as a
        # database did. Falls back to a greeting that works with no name at
        # all rather than to "there".
        "name": (lead["name"] or "").split(" ")[0] or "there",
        "full_name": lead["name"] or "",
        "email": lead["email"] or "",
        "referral_code": code,
        "referral_link": f"{REFERRAL_LINK_BASE}{code}" if code else "",
    }


async def _deliver(
    *,
    channel: Channel,
    recipient: str,
    subject: str | None,
    body: str,
    text_body: str | None = None,
) -> str | None:
    """Send one message. Returns an error string, or None on success.

    Email only. WhatsApp never reaches here — it is handed to the operator as
    a link rather than sent by the platform.
    """
    return await _deliver_email(recipient, subject or "", body, text_body)


async def _deliver_email(
    recipient: str, subject: str, body: str, text_body: str | None = None
) -> str | None:
    from src.core.interfaces.services.email_service import EmailMessage
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )

    try:
        service = ResendEmailService()
        ok = await service.send_email(
            EmailMessage(
                to=recipient,
                subject=subject,
                html_content=body,
                # A plain-text alternative is not optional for marketing mail:
                # a multipart message without one is a spam signal, and the
                # templates are simple enough that stripping the tags gives a
                # readable fallback rather than a wall of markup.
                text_content=text_body or _plain_text(body),
                from_email=MARKETING_FROM_EMAIL,
                from_name=MARKETING_FROM_NAME,
                # Replies go to the mailbox the message came from, so a
                # merchant answering a follow-up reaches a person.
                reply_to=MARKETING_FROM_EMAIL,
            )
        )
        return None if ok else "the email provider rejected the message"
    except Exception as exc:  # noqa: BLE001 — reported per recipient, not raised
        logger.warning("marketing_email_failed recipient=%s error=%s", recipient, exc)
        return str(exc)[:300]


_TAG = re.compile(r"<[^>]+>")
_BLOCK_END = re.compile(r"</(p|div|li|h[1-6]|tr)>", re.IGNORECASE)
_LINE_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _plain_text(html: str) -> str:
    """A readable text/plain alternative to an HTML body.

    Not a general HTML-to-text converter — the input is our own templates,
    which are paragraphs and lists. Block ends become newlines first so the
    text does not collapse into one run-on line.
    """
    text = _BLOCK_END.sub("\n", html)
    text = _LINE_BREAK.sub("\n", text)
    text = _TAG.sub("", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def _whatsapp_link(phone: str, body: str) -> str:
    """A click-to-chat link with the message already typed.

    The platform does NOT send this itself, deliberately. Sending marketing
    through the Business API needs a Meta-approved marketing template per
    message, is subject to the 24-hour session window and the per-merchant
    frequency cap, and none of that fits ad-hoc outreach to a prospect who has
    never messaged us. The unofficial transport would send it, from a logged-in
    account, and cold-messaging strangers from it is the fastest way to lose
    the platform's number.

    So the operator sends it, from their own WhatsApp, with the copy already
    written. It also means the merchant replies to a person who can answer.
    """
    return f"https://wa.me/{_wa_number(phone)}?text={quote(body)}"


def _wa_number(phone: str) -> str:
    """wa.me wants digits only — no +, no spaces, no dashes."""
    return re.sub(r"\D", "", phone or "")


async def _log(
    db: AsyncSession,
    *,
    lead_id: UUID,
    recipient: str,
    channel: Channel,
    template_key: str | None,
    subject: str | None,
    body: str,
    status: str,
    error: str | None,
    sent_by: UUID,
    click_token: str | None = None,
    links: list[str] | None = None,
) -> None:
    await db.execute(
        text(
            "INSERT INTO public.marketing_outreach "
            "(lead_id, recipient, channel, template_key, subject, body, status, "
            " error, sent_by, click_token, links) "
            "VALUES (:lead_id, :recipient, :channel, :template_key, :subject, "
            "        :body, :status, :error, :sent_by, :click_token, "
            "        CAST(:links AS jsonb))"
        ),
        {
            "lead_id": str(lead_id),
            "recipient": recipient,
            "channel": channel,
            "template_key": template_key,
            "subject": subject,
            "body": body,
            "status": status,
            "error": error,
            "sent_by": str(sent_by),
            "click_token": click_token,
            "links": json.dumps(links or []),
        },
    )


# ── Outreach history ─────────────────────────────────────────────────────────


class OutreachEntry(BaseModel):
    id: str
    lead_id: str | None
    lead_email: str | None
    recipient: str
    channel: Channel
    template_key: str | None
    subject: str | None
    body: str
    status: str
    error: str | None
    created_at: datetime
    #: None until the merchant follows a link. The first click, not the last:
    #: "did this land" is answered by the first one.
    clicked_at: datetime | None = None
    click_count: int = 0


@router.get(
    "/outreach",
    response_model=SuccessResponse[list[OutreachEntry]],
    summary="What we have sent, and to whom",
    operation_id="admin_list_marketing_outreach",
)
async def list_outreach(
    _admin: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    lead_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    clause = "WHERE o.lead_id = :lead_id" if lead_id else ""
    rows = (
        await db.execute(
            text(
                "SELECT o.id, o.lead_id, l.email AS lead_email, o.recipient, "
                "       o.channel, o.template_key, o.subject, o.body, o.status, "
                "       o.error, o.created_at, o.clicked_at, o.click_count "
                "FROM public.marketing_outreach o "
                "LEFT JOIN public.merchant_leads l ON l.id = o.lead_id "
                f"{clause} ORDER BY o.created_at DESC LIMIT :limit"  # nosec B608 - literal clause; no caller value interpolated
            ),
            {"limit": limit, **({"lead_id": str(lead_id)} if lead_id else {})},
        )
    ).mappings()

    return SuccessResponse(
        data=[
            OutreachEntry(
                id=str(r["id"]),
                lead_id=str(r["lead_id"]) if r["lead_id"] else None,
                lead_email=r["lead_email"],
                recipient=r["recipient"],
                channel=r["channel"],
                template_key=r["template_key"],
                subject=r["subject"],
                body=r["body"],
                status=r["status"],
                error=r["error"],
                created_at=r["created_at"],
                clicked_at=r["clicked_at"],
                click_count=r["click_count"] or 0,
            )
            for r in rows
        ]
    )


# ── Referral programme ───────────────────────────────────────────────────────


class MilestoneSetting(BaseModel):
    milestone: str
    label: str
    description: str
    amount_cents: int
    is_active: bool


class MilestonePatch(BaseModel):
    amount_cents: int = Field(ge=0, le=1_000_000)
    is_active: bool


class ReferralReward(BaseModel):
    id: str
    referrer_email: str | None
    referred_email: str | None
    milestone: str
    milestone_label: str
    amount_cents: int
    currency: str
    status: str
    earned_at: datetime
    paid_at: datetime | None


class ReferralSummary(BaseModel):
    pending_count: int
    pending_cents: int
    approved_count: int
    approved_cents: int
    paid_count: int
    paid_cents: int
    referred_leads: int
    activated_referrals: int


@router.get(
    "/referrals/milestones",
    response_model=SuccessResponse[list[MilestoneSetting]],
    summary="The milestones a referrer can earn on, and what each pays",
    operation_id="admin_list_referral_milestones",
)
async def list_milestones(
    _admin: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Declared in code, priced in the database.

    The list comes from `referral_service.MILESTONES` because each one is a
    condition something has to evaluate; only the amounts are editable. A
    milestone with no row yet reads as 0 and inactive.
    """
    stored = {
        r["milestone"]: r
        for r in (
            await db.execute(
                text(
                    "SELECT milestone, amount_cents, is_active "
                    "FROM public.referral_milestone_settings"
                )
            )
        )
        .mappings()
        .all()
    }
    return SuccessResponse(
        data=[
            MilestoneSetting(
                milestone=m.key,
                label=m.label,
                description=m.description,
                amount_cents=int(stored.get(m.key, {}).get("amount_cents", 0)),
                is_active=bool(stored.get(m.key, {}).get("is_active", False)),
            )
            for m in referral_service.MILESTONES
        ]
    )


@router.put(
    "/referrals/milestones/{milestone}",
    response_model=SuccessResponse[MilestoneSetting],
    summary="Set what a milestone pays",
    operation_id="admin_update_referral_milestone",
)
async def update_milestone(
    milestone: str,
    payload: MilestonePatch,
    admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Change a reward. Applies to milestones reached from now on.

    Rewards already in the ledger keep the amount that was promised when they
    were earned — repricing history would silently change what the platform
    owes a merchant who already did the work.
    """
    known = referral_service.MILESTONES_BY_KEY.get(milestone)
    if known is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=(
                f"'{milestone}' is not a milestone this platform evaluates. "
                "Adding one is a code change, so a reward can never be promised "
                "with nothing to pay it."
            ),
        )

    await db.execute(
        text(
            "INSERT INTO public.referral_milestone_settings "
            "(milestone, amount_cents, is_active, updated_by, updated_at) "
            "VALUES (:m, :amount, :active, :admin, now()) "
            "ON CONFLICT (milestone) DO UPDATE SET "
            "  amount_cents = EXCLUDED.amount_cents, "
            "  is_active = EXCLUDED.is_active, "
            "  updated_by = EXCLUDED.updated_by, "
            "  updated_at = now()"
        ),
        {
            "m": milestone,
            "amount": payload.amount_cents,
            "active": payload.is_active,
            "admin": str(admin_id),
        },
    )
    await db.commit()
    return SuccessResponse(
        data=MilestoneSetting(
            milestone=milestone,
            label=known.label,
            description=known.description,
            amount_cents=payload.amount_cents,
            is_active=payload.is_active,
        )
    )


@router.get(
    "/referrals",
    response_model=SuccessResponse[list[ReferralReward]],
    summary="The referral reward ledger",
    operation_id="admin_list_referral_rewards",
)
async def list_rewards(
    _admin: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: Literal["pending", "approved", "paid", "void", "all"] = Query(
        default="pending", alias="status"
    ),
    limit: int = Query(default=100, ge=1, le=500),
):
    clause = "" if status_filter == "all" else "WHERE r.status = :status"
    rows = (
        await db.execute(
            text(
                "SELECT r.id, r.milestone, r.amount_cents, r.currency, r.status, "
                "       r.earned_at, r.paid_at, "
                "       referrer.email AS referrer_email, "
                "       referred.email AS referred_email "
                "FROM public.referral_rewards r "
                "LEFT JOIN public.merchant_leads referrer "
                "       ON referrer.id = r.referrer_lead_id "
                "LEFT JOIN public.merchant_leads referred "
                "       ON referred.id = r.referred_lead_id "
                # Oldest first: a payout someone earned three weeks ago is the
                # one that is overdue.
                f"{clause} ORDER BY r.earned_at ASC LIMIT :limit"  # nosec B608 - literal clause; no caller value interpolated
            ),
            {
                "limit": limit,
                **({} if status_filter == "all" else {"status": status_filter}),
            },
        )
    ).mappings()

    return SuccessResponse(
        data=[
            ReferralReward(
                id=str(r["id"]),
                referrer_email=r["referrer_email"],
                referred_email=r["referred_email"],
                milestone=r["milestone"],
                milestone_label=(
                    referral_service.MILESTONES_BY_KEY[r["milestone"]].label
                    if r["milestone"] in referral_service.MILESTONES_BY_KEY
                    else r["milestone"]
                ),
                amount_cents=r["amount_cents"],
                currency=r["currency"],
                status=r["status"],
                earned_at=r["earned_at"],
                paid_at=r["paid_at"],
            )
            for r in rows
        ]
    )


@router.get(
    "/referrals/summary",
    response_model=SuccessResponse[ReferralSummary],
    summary="What the referral programme owes and has produced",
    operation_id="admin_referral_summary",
)
async def referral_summary(
    _admin: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    row = (
        (
            await db.execute(
                text(
                    "SELECT "
                    "  count(*) FILTER (WHERE status = 'pending') AS pending_count, "
                    "  coalesce(sum(amount_cents) FILTER (WHERE status = 'pending'), 0) "
                    "    AS pending_cents, "
                    "  count(*) FILTER (WHERE status = 'approved') AS approved_count, "
                    "  coalesce(sum(amount_cents) FILTER (WHERE status = 'approved'), 0) "
                    "    AS approved_cents, "
                    "  count(*) FILTER (WHERE status = 'paid') AS paid_count, "
                    "  coalesce(sum(amount_cents) FILTER (WHERE status = 'paid'), 0) "
                    "    AS paid_cents "
                    "FROM public.referral_rewards"
                )
            )
        )
        .mappings()
        .one()
    )

    leads = (
        (
            await db.execute(
                text(
                    "SELECT count(*) AS referred, "
                    "       count(*) FILTER (WHERE first_order_at IS NOT NULL) "
                    "         AS activated "
                    "FROM public.merchant_leads WHERE referred_by_lead_id IS NOT NULL"
                )
            )
        )
        .mappings()
        .one()
    )

    return SuccessResponse(
        data=ReferralSummary(
            pending_count=row["pending_count"],
            pending_cents=int(row["pending_cents"]),
            approved_count=row["approved_count"],
            approved_cents=int(row["approved_cents"]),
            paid_count=row["paid_count"],
            paid_cents=int(row["paid_cents"]),
            referred_leads=leads["referred"],
            activated_referrals=leads["activated"],
        )
    )


class RewardDecision(BaseModel):
    action: Literal["approve", "pay", "void"]
    note: str | None = Field(default=None, max_length=500)


@router.post(
    "/referrals/{reward_id}/decision",
    response_model=SuccessResponse[dict],
    summary="Approve, pay out, or void a referral reward",
    operation_id="admin_decide_referral_reward",
)
async def decide_reward(
    reward_id: UUID,
    payload: RewardDecision,
    _admin: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Move one reward along pending → approved → paid, or void it.

    `paid` records that money left NUMU; it does not move any. Payouts happen
    outside the platform today (a transfer, a wallet credit keyed by hand),
    and a status that claimed otherwise would be a lie in the ledger.

    Voiding requires a note. A reward a merchant earned and did not receive is
    exactly the decision that needs a reason attached to it.
    """
    if payload.action == "void" and not (payload.note or "").strip():
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Say why this reward is being voided.",
        )

    now = datetime.now(UTC)
    if payload.action == "approve":
        assignment = "status = 'approved', approved_at = :now"
        allowed = "('pending',)"
    elif payload.action == "pay":
        assignment = (
            "status = 'paid', paid_at = :now, approved_at = coalesce(approved_at, :now)"
        )
        allowed = "('pending', 'approved')"
    else:
        assignment = "status = 'void'"
        allowed = "('pending', 'approved')"

    row = (
        await db.execute(
            text(
                f"UPDATE public.referral_rewards SET {assignment}, "  # nosec B608 - assignment and states are module literals chosen by a validated Literal
                "note = coalesce(:note, note), updated_at = now() "
                f"WHERE id = :id AND status IN {allowed} "  # nosec B608 - module literal
                "RETURNING status"
            ),
            {"id": str(reward_id), "now": now, "note": payload.note},
        )
    ).first()

    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="That reward is not in a state this action can be applied to.",
        )
    await db.commit()
    return SuccessResponse(data={"status": row[0]})


@router.post(
    "/referrals/recalculate",
    response_model=SuccessResponse[dict],
    summary="Re-scan every referred lead for milestones it has reached",
    operation_id="admin_recalculate_referrals",
)
async def recalculate(
    _admin: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """The safety net behind live accrual.

    Rewards are created as milestones happen. This catches the rest: a lead
    referred before an amount was configured, a handler that threw, a
    backfill. Idempotent — running it twice changes nothing.
    """
    result = await referral_service.recalculate_all(db)
    await db.commit()
    return SuccessResponse(data=result)


@router.get(
    "/settings",
    response_model=SuccessResponse[dict],
    summary="What this environment can actually send",
    operation_id="admin_marketing_settings",
)
async def marketing_settings(
    _admin: Annotated[UUID, Depends(require_admin)],
):
    """So the page can disable email instead of failing on send.

    An operator who writes a promotion, selects forty merchants and only then
    learns the environment has no Resend key has wasted their afternoon.
    """
    return SuccessResponse(
        data={
            "email_from": MARKETING_FROM_EMAIL,
            "email_enabled": bool(settings.resend_api_key),
            # Always true: WhatsApp here is a link the operator opens, so there
            # is no provider to configure and nothing that can be down.
            "whatsapp_enabled": True,
            "referral_link_base": REFERRAL_LINK_BASE,
        }
    )
