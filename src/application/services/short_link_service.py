"""Short-link service — create, validate, resolve.

The redirector at ``GET /r/{short_code}`` is a hot read path that does
ONE lookup and returns a 302; the trust boundary is at create time.
This service enforces three rules:

* **No open redirector** — destination URLs must point to a host the
  caller's store actually owns. We compare the destination's host
  against the store's canonical origin (custom domain or
  ``<subdomain>.numueg.app``). Anything else is rejected.
* **Globally unique short_code** — 8-char Crockford base32 generated
  via ``secrets``, retry on collision against the UNIQUE index. 32^8
  ≈ 1.1T entries: collisions are astronomically rare even at
  NUMU-wide scale.
* **Active + non-expired only at resolve time** — soft-disable
  (``is_active=false``) and ``expires_at`` are both honored. The
  redirector treats either as 404 so the merchant can kill a leaked
  link without deleting the click history.

Click-count bumps happen in a background task off the response path
to keep the redirect under ~20ms p95. The race between "bump counter"
and "redirect" is intentional: undercount is better than a slow
redirect.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.store import Store
from src.infrastructure.database.models.tenant.short_link import ShortLinkModel

# Crockford base32 — exclude I, L, O, U (look-alike + profanity).
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CODE_LENGTH = 8
_MAX_RETRIES = 5


class ShortLinkCreationError(RuntimeError):
    """Raised when the short_code retry loop exhausts itself.

    32^8 ≈ 1.1T possible codes — five consecutive collisions is a sign
    the RNG is broken or the table is implausibly large, not a normal
    race condition. Surface the failure loudly.
    """


class OpenRedirectorError(ValueError):
    """Raised when the destination_url's host doesn't match the store.

    Critical defence: without this, an attacker who can call the
    trackable-link endpoint could mint short_codes pointing at
    phishing sites and ride NUMU's brand reputation. The host check
    keeps the redirector closed.
    """


def _random_code() -> str:
    """Cryptographically random 8-char Crockford base32 code."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))


def _normalize_host(host: str | None) -> str:
    """Lowercase + strip leading ``www.`` for fair comparison.

    URL parsers preserve case for host; the WHATWG spec says hosts are
    case-insensitive. Normalize both sides before comparing so a
    merchant typing ``Acme.numueg.app`` doesn't get a false positive.
    """
    if not host:
        return ""
    host = host.lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host


def _origin_host(origin: str) -> str:
    """Extract + normalize the host from a canonical-origin URL."""
    parsed = urlparse(origin)
    return _normalize_host(parsed.netloc or parsed.path)


def validate_destination_host(destination_url: str, store: Store) -> None:
    """Raise ``OpenRedirectorError`` if the destination host doesn't match.

    Accepts:
    * the store's exact canonical host (custom_domain or
      ``<subdomain>.numueg.app``)
    * ``*.numueg.app`` for the store's subdomain (defence against
      future canonical-origin changes — if the store ever switches
      from custom_domain back to subdomain, links keep working)

    The full URL must be HTTP/HTTPS — no ``javascript:``, no ``data:``,
    no ``file:``. The parse rejects those at the scheme check.
    """
    if not destination_url:
        raise OpenRedirectorError("destination_url is required")

    parsed = urlparse(destination_url)
    if parsed.scheme not in ("http", "https"):
        raise OpenRedirectorError(
            f"destination scheme {parsed.scheme!r} is not allowed; "
            "only http(s) URLs may be shortened"
        )

    dest_host = _normalize_host(parsed.netloc)
    if not dest_host:
        raise OpenRedirectorError("destination_url has no host")

    canonical_host = _origin_host(store.store_url)
    if dest_host == canonical_host:
        return

    # Also accept any *.numueg.app subdomain belonging to this store,
    # in case the store currently has a custom_domain but the link was
    # composed against the subdomain. The subdomain is stable; the
    # custom_domain may come and go.
    subdomain = (store.subdomain or "").lower().strip()
    if subdomain:
        allowed_subdomain_host = f"{subdomain}.numueg.app"
        if dest_host == allowed_subdomain_host:
            return

    raise OpenRedirectorError(
        f"destination host {dest_host!r} does not belong to store "
        f"{store.id} (allowed: {canonical_host!r}"
        + (f", {subdomain}.numueg.app" if subdomain else "")
        + ")"
    )


async def _generate_unique_short_code(session: AsyncSession) -> str:
    """Generate a fresh short_code, retrying on collision."""
    for _ in range(_MAX_RETRIES):
        code = _random_code()
        result = await session.execute(
            select(exists().where(ShortLinkModel.short_code == code))
        )
        if not result.scalar():
            return code
    raise ShortLinkCreationError(
        f"failed to find a free short_code after {_MAX_RETRIES} attempts"
    )


async def create_short_link(
    *,
    session: AsyncSession,
    store: Store,
    destination_url: str,
    campaign_id: UUID | None = None,
    expires_at: datetime | None = None,
    created_by: UUID | None = None,
) -> ShortLinkModel:
    """Create a short_link row after validating the destination.

    Caller is responsible for committing the surrounding transaction.
    The DB's UNIQUE constraint on ``short_code`` backstops the
    generator's collision retry; on the rare race where two parallel
    callers pick the same code, one ``IntegrityError`` fires and the
    caller should retry by calling this function again.
    """
    validate_destination_host(destination_url, store)
    short_code = await _generate_unique_short_code(session)

    row = ShortLinkModel(
        store_id=store.id,
        tenant_id=store.tenant_id,
        short_code=short_code,
        destination_url=destination_url,
        campaign_id=campaign_id,
        expires_at=expires_at,
        created_by=created_by,
    )
    session.add(row)
    # Flush so the autoincrement-style server_default for click_count
    # / is_active comes back populated; the caller still owns the
    # commit.
    await session.flush()
    return row


async def resolve_short_code(
    *,
    session: AsyncSession,
    short_code: str,
) -> ShortLinkModel | None:
    """Look up an active, non-expired short_link by code.

    Returns ``None`` when the code is unknown, the row is soft-disabled
    (``is_active=false``), or it's past its ``expires_at``. The route
    layer maps ``None`` to a 404.

    Does NOT bump the click counter — that's deferred to a background
    task so the redirect stays fast.
    """
    if not short_code:
        return None

    result = await session.execute(
        select(ShortLinkModel).where(ShortLinkModel.short_code == short_code)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    if not row.is_active:
        return None
    if row.expires_at is not None and row.expires_at <= datetime.now(UTC):
        return None
    return row


async def bump_click_count(
    *,
    session: AsyncSession,
    short_link_id: UUID,
    at: datetime | None = None,
) -> None:
    """Increment ``click_count`` and set ``last_clicked_at``.

    Intended to run inside a background task off the redirect's
    critical path. The update is idempotent enough — a click that
    isn't counted because the task failed isn't a correctness bug, it's
    a small undercount. The merchant's dashboard will still tell the
    right story.

    Single-statement update so we don't read-modify-write under
    concurrent clicks; Postgres handles the increment atomically.
    """
    timestamp = at or datetime.now(UTC)
    await session.execute(
        update(ShortLinkModel)
        .where(ShortLinkModel.id == short_link_id)
        .values(
            click_count=ShortLinkModel.click_count + 1,
            last_clicked_at=timestamp,
        )
    )
