"""Shopper-facing "can you get me this?" form.

URL: ``POST /storefront/store/{store_id}/product-requests``

A bookshop cannot list every edition that exists, so the storefront offers a
form: who you are, what you are after (title, ISBN-13, edition), and photos of
the cover you found elsewhere. This route is where that lands. The merchant
reads it in the hub and gets an email so a request does not sit unseen.

Public and unauthenticated, so it is written defensively: a honeypot field that
silently swallows bots, a per-IP rate limit, the same image validation product
uploads use, and a hard cap on how many photos one request may carry.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Path, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import get_store_repository
from src.api.dependencies.services import get_email_service, get_storage_service
from src.api.responses import SuccessResponse
from src.api.utils.upload_validation import validate_image_upload
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.core.interfaces.services.email_service import EmailMessage
from src.core.interfaces.services.storage_service import IStorageService, StorageBucket
from src.core.logging import get_logger
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.database.models.tenant.product_request import (
    ProductRequestModel,
)

logger = get_logger(__name__)

router = APIRouter()

#: One shopper should not be able to file a hundred requests in a minute.
RATE_LIMIT_PER_HOUR = 5
MAX_IMAGES = 5


class ProductRequestAck(BaseModel):
    """What the storefront needs back: that it landed, and under which id."""

    id: str | None = None
    received: bool = True


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _over_rate_limit(store_id: UUID, ip: str) -> bool:
    """True when this IP has already filed its hourly allowance.

    Best-effort: if Redis is unavailable the form still works. A form that
    stops accepting requests because a cache is down is worse than one that
    briefly accepts too many.
    """
    try:
        cache = RedisCacheService()
        key = f"product_request_rate:{store_id}:{ip}"
        count = await cache.increment(key)
        if count == 1:
            # INCR leaves a fresh key without a TTL; SETEX the same value back
            # so the window expires. Later increments keep that TTL.
            await cache.set(key, 1, expire=3600)
        return count > RATE_LIMIT_PER_HOUR
    except Exception:
        logger.warning("product_request_rate_limit_unavailable", exc_info=True)
        return False


def _merchant_email_html(
    *,
    store_name: str,
    name: str,
    email: str,
    phone: str | None,
    details: str,
    images: list[str],
    source_url: str | None,
) -> str:
    photos = "".join(
        f'<a href="{url}" style="margin-inline-end:8px">'
        f'<img src="{url}" alt="" width="96" height="96" '
        f'style="object-fit:cover;border-radius:8px;border:1px solid #e5e7eb"></a>'
        for url in images
    )
    contact = f'<a href="mailto:{email}">{email}</a>'
    if phone:
        contact += f' · <a href="tel:{phone}">{phone}</a>'
    return f"""
    <div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#111827">
      <h2 style="margin:0 0 4px">A customer is asking for a book</h2>
      <p style="margin:0 0 16px;color:#6b7280">{store_name}</p>
      <p style="margin:0 0 4px"><strong>{name}</strong></p>
      <p style="margin:0 0 16px;color:#374151">{contact}</p>
      <p style="white-space:pre-wrap;margin:0 0 16px">{details}</p>
      {f'<div style="margin-bottom:16px">{photos}</div>' if photos else ""}
      {f'<p style="color:#6b7280;font-size:12px">From {source_url}</p>' if source_url else ""}
      <p style="color:#6b7280;font-size:12px">
        Reply to this request from your dashboard under Customers → Product requests.
      </p>
    </div>
    """


@router.post(
    "/product-requests",
    response_model=SuccessResponse[ProductRequestAck],
    status_code=status.HTTP_201_CREATED,
    summary="Submit a product request",
    operation_id="storefront_create_product_request",
)
async def create_product_request(
    request: Request,
    store_id: Annotated[UUID, Path(description="Store the request is for")],
    db: Annotated[AsyncSession, Depends(get_db)],
    store_repo: Annotated[IStoreRepository, Depends(get_store_repository)],
    storage: Annotated[IStorageService, Depends(get_storage_service)],
    email_service: Annotated[object, Depends(get_email_service)],
    name: Annotated[str, Form(max_length=120)],
    email: Annotated[str, Form(max_length=255)],
    details: Annotated[str, Form(max_length=4000)],
    phone: Annotated[str | None, Form(max_length=32)] = None,
    locale: Annotated[str | None, Form(max_length=8)] = None,
    source_url: Annotated[str | None, Form(max_length=1024)] = None,
    website: Annotated[str | None, Form()] = None,
    images: Annotated[list[UploadFile] | None, File()] = None,
) -> SuccessResponse[ProductRequestAck]:
    """Record a shopper's request and tell the merchant about it.

    The honeypot (`website`) and the rate limit both answer 201 rather than an
    error: a bot that learns which submissions were rejected learns how to get
    past the check, and a shopper who hit the limit is better served by a
    "thanks" than by a wall.
    """
    if website:
        logger.info("product_request_honeypot", store_id=str(store_id))
        return SuccessResponse(data=ProductRequestAck(), message="Request received")

    if await _over_rate_limit(store_id, _client_ip(request)):
        logger.info("product_request_rate_limited", store_id=str(store_id))
        return SuccessResponse(data=ProductRequestAck(), message="Request received")

    store = await store_repo.get_by_id(store_id)
    if not store:
        # Same answer as success: an unknown store id is either a typo in a
        # theme or someone probing which stores exist.
        return SuccessResponse(data=ProductRequestAck(), message="Request received")

    urls: list[str] = []
    for upload in (images or [])[:MAX_IMAGES]:
        content = await validate_image_upload(upload)
        uploaded = await storage.upload_file(
            file_content=content,
            filename=upload.filename or "request.jpg",
            content_type=upload.content_type or "image/jpeg",
            bucket=StorageBucket.PRODUCTS,
        )
        urls.append(uploaded.url)

    row = ProductRequestModel(
        store_id=store_id,
        name=name.strip()[:120],
        email=email.strip()[:255],
        phone=(phone or "").strip()[:32] or None,
        details=details.strip(),
        images=urls,
        locale=(locale or "").strip()[:8] or None,
        source_url=(source_url or "").strip()[:1024] or None,
        status="new",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    # Best-effort: the request is saved either way, and a mail outage must not
    # turn into a 500 on a shopper's form.
    recipient = getattr(store, "contact_email", None) or getattr(store, "email", None)
    if recipient:
        try:
            await email_service.send_email(
                EmailMessage(
                    to=recipient,
                    subject=f"Book request from {row.name}",
                    html_content=_merchant_email_html(
                        store_name=store.name,
                        name=row.name,
                        email=row.email,
                        phone=row.phone,
                        details=row.details,
                        images=urls,
                        source_url=row.source_url,
                    ),
                )
            )
        except Exception:
            logger.warning("product_request_email_failed", request_id=str(row.id))

    logger.info(
        "product_request_created",
        store_id=str(store_id),
        request_id=str(row.id),
        images=len(urls),
    )
    return SuccessResponse(
        data=ProductRequestAck(id=str(row.id)), message="Request received"
    )
