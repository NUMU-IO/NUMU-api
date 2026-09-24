"""Support threads between merchants, partners and NUMU.

- A merchant contacts an app's partner from the app page (``kind='app'``):
  ``/stores/{store_id}/app-support``; the partner answers from the portal.
- A partner opens a ticket to NUMU (``kind='partner'``) from the portal:
  ``/partners/me/support``; staff answer from ``/admin/partner-support``.

A reply from the answering side marks the ticket ``answered``, one from the
asking side ``open``; either side can close it and a new message reopens it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.auth import get_current_user_id, require_admin
from src.api.dependencies.database import get_db
from src.api.dependencies.partners import (
    PartnerContext,
    partner_context,
    require_partner_program,
)
from src.api.dependencies.services import get_email_service, get_storage_service
from src.api.middleware.rate_limit import _check_per_user_limit
from src.api.responses import SuccessResponse
from src.api.utils.upload_validation import (
    _ALLOWED_IMAGE_MIMES,
    MAX_IMAGE_SIZE,
    _detect_image_mime,
)
from src.application.services import admin_notifications
from src.config import settings
from src.core.entities.app import AppStatus
from src.core.entities.store import Store
from src.core.interfaces.services.email_service import EmailMessage
from src.core.interfaces.services.storage_service import StorageBucket
from src.core.logging import get_logger
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppModel,
)
from src.infrastructure.database.models.public.partner_account import (
    PartnerAccountModel,
)
from src.infrastructure.database.models.public.support_ticket import (
    SupportMessageModel,
    SupportTicketModel,
)
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.tenant.store import StoreModel

logger = get_logger(__name__)

merchant_router = APIRouter(
    prefix="/{store_id}/app-support",
    tags=["App Support"],
    dependencies=[Depends(verify_store_ownership)],
)
partner_router = APIRouter(
    prefix="/partners/me/support",
    tags=["Partner Portal"],
    dependencies=[Depends(require_partner_program)],
)
admin_router = APIRouter()

MAX_ATTACHMENTS = 3
WRITES_PER_HOUR = 30
ANSWERING_ROLE = {"app": "partner", "partner": "staff"}
TicketStatus = Literal["open", "answered", "closed"]


def partners_url(path: str = "") -> str:
    return settings.merchant_hub_url.replace("://merchant.", "://partners.", 1) + path


async def write_budget(user_id: UUID, tier: str, limit: int) -> None:
    allowed, _, retry_after = await _check_per_user_limit(str(user_id), tier, limit)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please slow down.",
            headers={"Retry-After": str(retry_after)},
        )


async def send_bilingual(
    email_service: Any,
    to: str | None,
    *,
    subject: tuple[str, str],
    lines: tuple[str, str],
    url: str,
    quote: str | None = None,
) -> None:
    """One email carrying Arabic then English. Best-effort: the write it
    announces is already saved."""
    if not to:
        return
    quoted = (
        f'<p style="white-space:pre-wrap;border-inline-start:3px solid #e5e7eb;'
        f'padding-inline-start:12px;color:#374151">{escape(quote[:1000])}</p>'
        if quote
        else ""
    )

    def block(lang: str, title: str, line: str, cta: str) -> str:
        return (
            f'<div dir="{"rtl" if lang == "ar" else "ltr"}" lang="{lang}" '
            'style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;'
            'color:#111827;max-width:560px">'
            f'<h2 style="margin:0 0 8px;font-size:18px">{escape(title)}</h2>'
            f'<p style="margin:0 0 12px">{escape(line)}</p>{quoted}'
            f'<a href="{escape(url)}" style="display:inline-block;padding:10px 16px;'
            'background:#111827;color:#fff;border-radius:6px;text-decoration:none">'
            f"{cta}</a></div>"
        )

    html = (
        block("ar", subject[1], lines[1], "فتح")
        + '<hr style="margin:24px 0;border:none;border-top:1px solid #e5e7eb">'
        + block("en", subject[0], lines[0], "Open")
    )
    try:
        await email_service.send_email(
            EmailMessage(
                to=to, subject=f"{subject[1]} | {subject[0]}", html_content=html
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("support_email_failed", error=str(exc))


async def partner_email(db: AsyncSession, developer_id: UUID | None) -> str | None:
    if developer_id is None:
        return None
    return await db.scalar(
        select(PartnerAccountModel.support_email).where(
            PartnerAccountModel.user_id == developer_id
        )
    )


async def _store_owner_email(db: AsyncSession, store_id: UUID) -> str | None:
    return await db.scalar(
        select(UserModel.email)
        .join(StoreModel, StoreModel.owner_id == UserModel.id)
        .where(StoreModel.id == store_id)
    )


async def _attachments(files: list[UploadFile] | None, storage: Any) -> list[dict]:
    files = [f for f in (files or []) if f and f.filename]
    if len(files) > MAX_ATTACHMENTS:
        raise HTTPException(
            status_code=422, detail=f"Attach at most {MAX_ATTACHMENTS} files."
        )
    out = []
    for f in files:
        content = await f.read(MAX_IMAGE_SIZE + 1)
        if len(content) > MAX_IMAGE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Each attachment must be 5 MB or less.",
            )
        mime = (
            "application/pdf"
            if content.startswith(b"%PDF-")
            else _detect_image_mime(content[:16])
        )
        if mime != "application/pdf" and mime not in _ALLOWED_IMAGE_MIMES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Attach images (JPEG, PNG, WebP, GIF) or PDF files only.",
            )
        name = (f.filename or "file")[:120]
        uploaded = await storage.upload_file(
            file_content=content,
            filename=name,
            content_type=mime,
            bucket=StorageBucket.DOCUMENTS,
        )
        out.append({
            "url": uploaded.url,
            "name": name,
            "content_type": mime,
            "size": len(content),
        })
    return out


# ─── Schemas ──────────────────────────────────────────────────────


class TicketOut(BaseModel):
    id: UUID
    kind: str
    subject: str
    status: str
    app_id: UUID | None
    app_name: str | None
    app_slug: str | None
    store_id: UUID | None
    store_name: str | None
    partner_name: str | None
    last_message_at: datetime | None
    created_at: datetime


class MessageOut(BaseModel):
    id: UUID
    author_role: str
    body: str
    attachments: list[dict[str, Any]]
    created_at: datetime


class ThreadOut(BaseModel):
    ticket: TicketOut
    messages: list[MessageOut]


class TicketPage(BaseModel):
    items: list[TicketOut]
    total: int
    page: int
    page_size: int


def _tickets():
    return (
        select(
            SupportTicketModel,
            AppModel.name,
            AppModel.slug,
            StoreModel.name,
            PartnerAccountModel.display_name,
        )
        .outerjoin(AppModel, AppModel.id == SupportTicketModel.app_id)
        .outerjoin(StoreModel, StoreModel.id == SupportTicketModel.store_id)
        .outerjoin(
            PartnerAccountModel,
            PartnerAccountModel.id == SupportTicketModel.partner_id,
        )
    )


def _ticket_out(row) -> TicketOut:
    t, app_name, app_slug, store_name, partner_name = row
    return TicketOut(
        id=t.id,
        kind=t.kind,
        subject=t.subject,
        status=t.status,
        app_id=t.app_id,
        app_name=app_name,
        app_slug=app_slug,
        store_id=t.store_id,
        store_name=store_name,
        partner_name=partner_name,
        last_message_at=t.last_message_at,
        created_at=t.created_at,
    )


def _merchant_scope(store_id: UUID):
    return and_(
        SupportTicketModel.kind == "app", SupportTicketModel.store_id == store_id
    )


def _partner_scope(ctx: PartnerContext):
    mine = and_(SupportTicketModel.kind == "app", AppModel.developer_id == ctx.owner_id)
    if ctx.account is None:
        return mine
    return or_(
        mine,
        and_(
            SupportTicketModel.kind == "partner",
            SupportTicketModel.partner_id == ctx.account.id,
        ),
    )


_ADMIN_SCOPE = SupportTicketModel.kind == "partner"


async def _page(
    db: AsyncSession,
    scope,
    *,
    ticket_status: str | None,
    kind: str | None = None,
    app_id: UUID | None = None,
    page: int,
    page_size: int,
) -> TicketPage:
    q = _tickets().where(scope)
    if ticket_status:
        q = q.where(SupportTicketModel.status == ticket_status)
    if kind:
        q = q.where(SupportTicketModel.kind == kind)
    if app_id:
        q = q.where(SupportTicketModel.app_id == app_id)
    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    rows = (
        await db.execute(
            q.order_by(
                func.coalesce(
                    SupportTicketModel.last_message_at, SupportTicketModel.created_at
                ).desc()
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return TicketPage(
        items=[_ticket_out(r) for r in rows],
        total=total or 0,
        page=page,
        page_size=page_size,
    )


async def _row(db: AsyncSession, scope, ticket_id: UUID):
    row = (
        await db.execute(_tickets().where(scope, SupportTicketModel.id == ticket_id))
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return row


async def _thread(db: AsyncSession, row) -> ThreadOut:
    messages = (
        (
            await db.execute(
                select(SupportMessageModel)
                .where(SupportMessageModel.ticket_id == row[0].id)
                .order_by(SupportMessageModel.created_at, SupportMessageModel.id)
            )
        )
        .scalars()
        .all()
    )
    return ThreadOut(
        ticket=_ticket_out(row),
        messages=[
            MessageOut(
                id=m.id,
                author_role=m.author_role,
                body=m.body,
                attachments=list(m.attachments or []),
                created_at=m.created_at,
            )
            for m in messages
        ],
    )


async def _add_message(
    db: AsyncSession,
    ticket: SupportTicketModel,
    *,
    role: str,
    user_id: UUID,
    body: str,
    attachments: list[dict],
) -> None:
    now = datetime.now(UTC)
    db.add(
        SupportMessageModel(
            ticket_id=ticket.id,
            author_id=user_id,
            author_role=role,
            body=body.strip(),
            attachments=attachments,
        )
    )
    ticket.status = "answered" if ANSWERING_ROLE[ticket.kind] == role else "open"
    ticket.last_message_at = now
    await db.flush()


async def _notify(
    db: AsyncSession, email_service: Any, row, *, role: str, body: str, new: bool
) -> None:
    """Tell the other side of the thread."""
    t, app_name, app_slug, store_name, partner_name = row
    subject = t.subject
    if t.kind == "partner" and role == "partner":
        admin_notifications.notify(
            db,
            title=f"Partner support: {partner_name or 'partner'}",
            body=subject,
            url="/partner-support",
            tag="partner_support",
        )
        return
    if t.kind == "partner":
        await send_bilingual(
            email_service,
            await db.scalar(
                select(PartnerAccountModel.support_email).where(
                    PartnerAccountModel.id == t.partner_id
                )
            ),
            subject=(f"NUMU replied: {subject}", f"رد فريق نُمو: {subject}"),
            lines=(
                "NUMU support answered your ticket.",
                "رد فريق دعم نُمو على تذكرتك.",
            ),
            url=partners_url("/support"),
            quote=body,
        )
        return
    if role == "merchant":
        app = await db.get(AppModel, t.app_id)
        await send_bilingual(
            email_service,
            await partner_email(db, app.developer_id if app else None),
            subject=(
                f"{'New' if new else 'New reply on'} support ticket: {subject}",
                f"{'تذكرة دعم جديدة' if new else 'رد جديد على تذكرة'}: {subject}",
            ),
            lines=(
                f"{store_name or 'A merchant'} wrote about {app_name}.",
                f"كتب {store_name or 'تاجر'} بخصوص {app_name}.",
            ),
            url=partners_url("/support"),
            quote=body,
        )
        return
    await send_bilingual(
        email_service,
        await _store_owner_email(db, t.store_id),
        subject=(
            f"{app_name} replied: {subject}",
            f"رد مطوّر {app_name}: {subject}",
        ),
        lines=(
            f"The developer of {app_name} answered your support ticket.",
            f"رد مطوّر تطبيق {app_name} على تذكرة الدعم الخاصة بك.",
        ),
        url=f"{settings.merchant_hub_url}/apps/{app_slug}?support={t.id}",
        quote=body,
    )


async def _reply(
    db: AsyncSession,
    email_service: Any,
    storage: Any,
    row,
    *,
    role: str,
    user_id: UUID,
    body: str,
    files: list[UploadFile] | None,
) -> ThreadOut:
    await write_budget(user_id, "support_write", WRITES_PER_HOUR)
    attachments = await _attachments(files, storage)
    await _add_message(
        db, row[0], role=role, user_id=user_id, body=body, attachments=attachments
    )
    await _notify(db, email_service, row, role=role, body=body, new=False)
    return await _thread(db, row)


async def _close(db: AsyncSession, row) -> ThreadOut:
    row[0].status = "closed"
    await db.flush()
    return await _thread(db, row)


Body = Annotated[str, Form(min_length=1, max_length=5000)]
Subject = Annotated[str, Form(min_length=3, max_length=200)]
Files = Annotated[list[UploadFile] | None, File()]
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


# ─── Merchant ─────────────────────────────────────────────────────


@merchant_router.get(
    "",
    response_model=SuccessResponse[TicketPage],
    operation_id="list_app_support_tickets",
)
async def merchant_list(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
    app_id: UUID | None = None,
    ticket_status: Annotated[TicketStatus | None, Query(alias="status")] = None,
    page: Page = 1,
    page_size: PageSize = 25,
):
    return SuccessResponse(
        data=await _page(
            db,
            _merchant_scope(store.id),
            ticket_status=ticket_status,
            app_id=app_id,
            page=page,
            page_size=page_size,
        )
    )


@merchant_router.post(
    "",
    response_model=SuccessResponse[ThreadOut],
    status_code=status.HTTP_201_CREATED,
    operation_id="create_app_support_ticket",
)
async def merchant_create(
    store: Annotated[Store, Depends(verify_store_ownership)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    email_service: Annotated[object, Depends(get_email_service)],
    storage: Annotated[object, Depends(get_storage_service)],
    app_slug: Annotated[str, Form(max_length=64)],
    subject: Subject,
    body: Body,
    files: Files = None,
):
    """Contact an app's developer. Partner Apps only, and only one the store
    has installed or can find in the catalog."""
    app = await db.scalar(select(AppModel).where(AppModel.slug == app_slug))
    installed = app is not None and await db.scalar(
        select(AppInstallationModel.id).where(
            AppInstallationModel.app_id == app.id,
            AppInstallationModel.store_id == store.id,
        )
    )
    if (
        app is None
        or app.developer_id is None
        or not (installed or app.status == AppStatus.PUBLISHED)
    ):
        raise HTTPException(status_code=404, detail="App not found")
    await write_budget(user_id, "support_write", WRITES_PER_HOUR)
    attachments = await _attachments(files, storage)
    ticket = SupportTicketModel(
        kind="app",
        app_id=app.id,
        store_id=store.id,
        partner_id=await db.scalar(
            select(PartnerAccountModel.id).where(
                PartnerAccountModel.user_id == app.developer_id
            )
        ),
        opened_by=user_id,
        subject=subject.strip(),
        status="open",
    )
    db.add(ticket)
    await db.flush()
    await _add_message(
        db,
        ticket,
        role="merchant",
        user_id=user_id,
        body=body,
        attachments=attachments,
    )
    row = await _row(db, _merchant_scope(store.id), ticket.id)
    await _notify(db, email_service, row, role="merchant", body=body, new=True)
    logger.info("app_support_ticket_opened", app=app.slug, store_id=str(store.id))
    return SuccessResponse(data=await _thread(db, row), message="Ticket opened")


@merchant_router.get(
    "/{ticket_id}",
    response_model=SuccessResponse[ThreadOut],
    operation_id="get_app_support_ticket",
)
async def merchant_get(
    ticket_id: UUID,
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    row = await _row(db, _merchant_scope(store.id), ticket_id)
    return SuccessResponse(data=await _thread(db, row))


@merchant_router.post(
    "/{ticket_id}/messages",
    response_model=SuccessResponse[ThreadOut],
    operation_id="reply_app_support_ticket",
)
async def merchant_reply(
    ticket_id: UUID,
    store: Annotated[Store, Depends(verify_store_ownership)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    email_service: Annotated[object, Depends(get_email_service)],
    storage: Annotated[object, Depends(get_storage_service)],
    body: Body,
    files: Files = None,
):
    row = await _row(db, _merchant_scope(store.id), ticket_id)
    return SuccessResponse(
        data=await _reply(
            db,
            email_service,
            storage,
            row,
            role="merchant",
            user_id=user_id,
            body=body,
            files=files,
        )
    )


@merchant_router.post(
    "/{ticket_id}/close",
    response_model=SuccessResponse[ThreadOut],
    operation_id="close_app_support_ticket",
)
async def merchant_close(
    ticket_id: UUID,
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    row = await _row(db, _merchant_scope(store.id), ticket_id)
    return SuccessResponse(data=await _close(db, row))


# ─── Partner ──────────────────────────────────────────────────────


@partner_router.get(
    "",
    response_model=SuccessResponse[TicketPage],
    operation_id="list_partner_support_tickets",
)
async def partner_list(
    ctx: Annotated[PartnerContext, Depends(partner_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
    kind: Literal["app", "partner"] | None = None,
    app_id: UUID | None = None,
    ticket_status: Annotated[TicketStatus | None, Query(alias="status")] = None,
    page: Page = 1,
    page_size: PageSize = 25,
):
    return SuccessResponse(
        data=await _page(
            db,
            _partner_scope(ctx),
            ticket_status=ticket_status,
            kind=kind,
            app_id=app_id,
            page=page,
            page_size=page_size,
        )
    )


@partner_router.post(
    "",
    response_model=SuccessResponse[ThreadOut],
    status_code=status.HTTP_201_CREATED,
    operation_id="create_partner_support_ticket",
)
async def partner_create(
    ctx: Annotated[PartnerContext, Depends(partner_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
    email_service: Annotated[object, Depends(get_email_service)],
    storage: Annotated[object, Depends(get_storage_service)],
    subject: Subject,
    body: Body,
    files: Files = None,
):
    """Ask NUMU. Needs a partner account (a super admin without one has
    nobody to ask)."""
    if ctx.account is None:
        raise HTTPException(status_code=409, detail="No partner account.")
    await write_budget(ctx.user_id, "support_write", WRITES_PER_HOUR)
    attachments = await _attachments(files, storage)
    ticket = SupportTicketModel(
        kind="partner",
        partner_id=ctx.account.id,
        opened_by=ctx.user_id,
        subject=subject.strip(),
        status="open",
    )
    db.add(ticket)
    await db.flush()
    await _add_message(
        db,
        ticket,
        role="partner",
        user_id=ctx.user_id,
        body=body,
        attachments=attachments,
    )
    row = await _row(db, _partner_scope(ctx), ticket.id)
    await _notify(db, email_service, row, role="partner", body=body, new=True)
    return SuccessResponse(data=await _thread(db, row), message="Ticket opened")


@partner_router.get(
    "/{ticket_id}",
    response_model=SuccessResponse[ThreadOut],
    operation_id="get_partner_support_ticket",
)
async def partner_get(
    ticket_id: UUID,
    ctx: Annotated[PartnerContext, Depends(partner_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    row = await _row(db, _partner_scope(ctx), ticket_id)
    return SuccessResponse(data=await _thread(db, row))


@partner_router.post(
    "/{ticket_id}/messages",
    response_model=SuccessResponse[ThreadOut],
    operation_id="reply_partner_support_ticket",
)
async def partner_reply(
    ticket_id: UUID,
    ctx: Annotated[PartnerContext, Depends(partner_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
    email_service: Annotated[object, Depends(get_email_service)],
    storage: Annotated[object, Depends(get_storage_service)],
    body: Body,
    files: Files = None,
):
    row = await _row(db, _partner_scope(ctx), ticket_id)
    return SuccessResponse(
        data=await _reply(
            db,
            email_service,
            storage,
            row,
            role="partner",
            user_id=ctx.user_id,
            body=body,
            files=files,
        )
    )


@partner_router.post(
    "/{ticket_id}/close",
    response_model=SuccessResponse[ThreadOut],
    operation_id="close_partner_support_ticket",
)
async def partner_close(
    ticket_id: UUID,
    ctx: Annotated[PartnerContext, Depends(partner_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    row = await _row(db, _partner_scope(ctx), ticket_id)
    return SuccessResponse(data=await _close(db, row))


# ─── Admin (partner → NUMU inbox) ─────────────────────────────────


@admin_router.get("", response_model=SuccessResponse[TicketPage])
async def admin_list(
    _: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    ticket_status: Annotated[TicketStatus | None, Query(alias="status")] = None,
    page: Page = 1,
    page_size: PageSize = 25,
):
    return SuccessResponse(
        data=await _page(
            db,
            _ADMIN_SCOPE,
            ticket_status=ticket_status,
            page=page,
            page_size=page_size,
        )
    )


@admin_router.get("/{ticket_id}", response_model=SuccessResponse[ThreadOut])
async def admin_get(
    ticket_id: UUID,
    _: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    row = await _row(db, _ADMIN_SCOPE, ticket_id)
    return SuccessResponse(data=await _thread(db, row))


@admin_router.post("/{ticket_id}/messages", response_model=SuccessResponse[ThreadOut])
async def admin_reply(
    ticket_id: UUID,
    admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    email_service: Annotated[object, Depends(get_email_service)],
    storage: Annotated[object, Depends(get_storage_service)],
    body: Body,
    files: Files = None,
):
    row = await _row(db, _ADMIN_SCOPE, ticket_id)
    return SuccessResponse(
        data=await _reply(
            db,
            email_service,
            storage,
            row,
            role="staff",
            user_id=admin_id,
            body=body,
            files=files,
        )
    )


@admin_router.post("/{ticket_id}/close", response_model=SuccessResponse[ThreadOut])
async def admin_close(
    ticket_id: UUID,
    _: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    row = await _row(db, _ADMIN_SCOPE, ticket_id)
    return SuccessResponse(data=await _close(db, row))
