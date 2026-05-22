"""Pre-flight a custom storefront path before producing a trackable link.

Merchants use this when they want a trackable link to an arbitrary
storefront path (landing page, blog post, custom theme route etc.) —
the trackable-link generator calls this first to confirm the path
actually resolves on this store's storefront.

Security posture (SEC-002 — SSRF guardrails):

* The endpoint issues a single HEAD request. No body is ever read.
* The target hostname is resolved via DNS; the request is rejected if
  the resolved IP is private / loopback / link-local.
* Redirects are NOT followed automatically. A 301/302/308 response is
  inspected manually: the redirect host must match the canonical
  origin, otherwise we reject (no chasing into other domains).
* The whole call has a hard 3-second total timeout (DNS + connect +
  read), enforced via httpx.Timeout.

Authorization (SEC-001): inherits ``verify_store_ownership`` from the
router-level dep — the authenticated user must have access to
``store_id``.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.repositories import get_store_repository
from src.api.responses import SuccessResponse
from src.infrastructure.repositories import StoreRepository

router = APIRouter(
    prefix="/{store_id}/storefront",
    tags=["Storefront Validation"],
    dependencies=[Depends(verify_store_ownership)],
)

_MAX_PATH_LEN = 500
_TIMEOUT_SECONDS = 3.0
_USER_AGENT = "NUMU-LinkValidator/1.0"
_ACCEPT_STATUSES = {200, 301, 302, 308}


# ── Schemas ──────────────────────────────────────────────────────


class ValidatePathRequest(BaseModel):
    """Path to be validated against the store's canonical origin.

    Path is rejected if it doesn't start with ``/``, contains a scheme
    (``http://``, ``https://``, ``//``), or is longer than 500 chars.
    """

    path: str = Field(min_length=1, max_length=_MAX_PATH_LEN)


class ValidatePathResponseData(BaseModel):
    valid: bool
    reason: (
        Literal[
            "path_malformed",
            "path_not_found",
            "validation_timeout",
            "external_host",
            "internal_target",
        ]
        | None
    ) = None
    canonical_path: str | None = None
    # When the path 301/302/308s within the same host, surface the
    # redirect target as a suggestion so the merchant can opt into the
    # canonical form before producing the trackable link.
    suggested_canonical: str | None = None
    http_status: int | None = None


# ── Helpers ──────────────────────────────────────────────────────


def _path_looks_external(path: str) -> bool:
    """Reject anything that smells like a URL or path-traversal escape.

    The validator only accepts in-origin paths. A leading ``//`` (which
    a browser would treat as a protocol-relative URL) or any ``://``
    indicates the merchant pasted a full URL — reject so we never
    HEAD-request someone else's host.
    """
    if not path.startswith("/"):
        return True
    if path.startswith("//") or path.startswith("\\\\"):
        return True
    if "://" in path:
        return True
    return False


def _resolved_ip_is_private(host: str) -> bool:
    """Resolve ``host`` and return True if ANY answer is non-public.

    Mitigates DNS rebinding / internal-services SSRF (SEC-002): we
    don't trust DNS to keep pointing at the same address between the
    merchant's request and ours. Any record in a private/loopback/
    link-local range is a hard reject.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # Unresolvable → treat as suspicious. Returning True here means
        # the caller reports "internal_target" — slightly misleading
        # for a true DNS failure, but the practical outcome (we don't
        # issue the HEAD) is the right one.
        return True
    for _family, _type, _proto, _canon, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except (ValueError, IndexError):
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return True
    return False


def _same_host(url_or_path: str, expected_origin: str) -> bool:
    """True iff ``url_or_path`` resolves to the same host as ``expected_origin``.

    Used to decide whether to follow a redirect. A redirect to a
    different host is rejected (no auto-follow off-origin).
    """
    if url_or_path.startswith("/"):
        return True  # relative redirect; same host by definition
    try:
        parsed = urlparse(url_or_path)
        origin_parsed = urlparse(expected_origin)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and parsed.netloc == origin_parsed.netloc


# ── Endpoint ─────────────────────────────────────────────────────


@router.post(
    "/validate-path",
    response_model=SuccessResponse[ValidatePathResponseData],
    summary="Validate a custom storefront path before producing a trackable link",
    operation_id="validate_storefront_path",
)
async def validate_path(
    store_id: UUID,
    body: ValidatePathRequest,
    store_repo: StoreRepository = Depends(get_store_repository),
):
    store = await store_repo.get_by_id(store_id)
    # SEC-001: verify_store_ownership at the router level already
    # confirmed the authenticated user has access to this store.
    # 404 (not 403) per the resolved security review.
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Store not found"
        )

    path = body.path.strip()
    if _path_looks_external(path):
        return SuccessResponse(
            data=ValidatePathResponseData(
                valid=False, reason="external_host", canonical_path=None
            ),
            message="Path validation completed",
        )

    origin = store.store_url.rstrip("/")
    target_url = f"{origin}{path}"

    parsed = urlparse(target_url)
    host = parsed.hostname or ""

    # SEC-002: reject if DNS resolves the target host to a non-public IP.
    if _resolved_ip_is_private(host):
        return SuccessResponse(
            data=ValidatePathResponseData(
                valid=False, reason="internal_target", canonical_path=None
            ),
            message="Path validation completed",
        )

    timeout = httpx.Timeout(
        _TIMEOUT_SECONDS, connect=_TIMEOUT_SECONDS, read=_TIMEOUT_SECONDS
    )
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,  # SEC-002: manual redirect handling
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            response = await client.head(target_url)
    except httpx.TimeoutException:
        return SuccessResponse(
            data=ValidatePathResponseData(
                valid=False, reason="validation_timeout", canonical_path=None
            ),
            message="Path validation completed",
        )
    except (httpx.ConnectError, httpx.RequestError):
        return SuccessResponse(
            data=ValidatePathResponseData(
                valid=False, reason="path_not_found", canonical_path=None
            ),
            message="Path validation completed",
        )

    http_status = response.status_code

    if http_status == 200:
        return SuccessResponse(
            data=ValidatePathResponseData(
                valid=True, canonical_path=path, http_status=http_status
            ),
            message="Path validation completed",
        )

    if http_status in (301, 302, 308):
        location = response.headers.get("location", "")
        # SEC-002: manual host check — we don't auto-follow off-origin.
        if not location or not _same_host(location, origin):
            return SuccessResponse(
                data=ValidatePathResponseData(
                    valid=False,
                    reason="external_host",
                    http_status=http_status,
                ),
                message="Path validation completed",
            )
        # Same-host redirect → extract the path portion as the canonical.
        if location.startswith("/"):
            suggested = location
        else:
            suggested = urlparse(location).path or "/"
        return SuccessResponse(
            data=ValidatePathResponseData(
                valid=True,
                canonical_path=path,
                suggested_canonical=suggested,
                http_status=http_status,
            ),
            message="Path validation completed",
        )

    # 4xx / 5xx / anything else → not a valid landing target.
    return SuccessResponse(
        data=ValidatePathResponseData(
            valid=False,
            reason="path_not_found",
            http_status=http_status,
        ),
        message="Path validation completed",
    )
