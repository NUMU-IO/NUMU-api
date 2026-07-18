"""COD Autopilot — automate shipped / delivered+paid for manual-ship merchants.

Feature 004-cod-autopilot. Three flows, all WhatsApp-driven:

1. **Daily ship digest** (US2): one message per store per local day listing
   eligible CONFIRMED COD orders; the merchant taps "All shipped"
   (``shipall:`` quick-reply) or replies with a numeric exceptions list
   ("except 2, 5"). Listed-minus-excepted orders transition
   CONFIRMED → PROCESSING → SHIPPED with ``source=merchant_digest``.
2. **Customer delivery check** (US1): N days after shipped, the customer
   gets Received / Not yet / Refused buttons (``dlvyes:/dlvnot:/dlvref:``).
   Received → DELIVERED (COD auto-marks PAID) with
   ``source=customer_confirmed`` — the only Autopilot path that feeds the
   trust network's positive delivery signal (research R-06).
3. **Assumed-delivered fallback** (US3): exhausted, unanswered checks
   close as DELIVERED with ``source=assumed_delivered`` once the
   store-configured window elapses — explicitly EXCLUDED from network
   delivery events.

The scheduling state lives in ``whatsapp_delivery_checks`` /
``whatsapp_ship_digests`` (RLS tenant tables); the Celery beat tasks in
``cod_autopilot_tasks.py`` drive the timers, cloning the
``cod_auto_rto_task`` scan pattern. Webhook button/text handlers here are
called from the WhatsApp callback route on its admin (RLS-bypass) session,
mirroring ``order_confirmation_service``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select

from src.core.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Settings (store.settings["cod_autopilot"]) — defaults + clamped reader.
# The settings route (GET/PATCH /settings/cod-autopilot) shares these.
# ─────────────────────────────────────────────────────────────────────

COD_AUTOPILOT_DEFAULTS: dict = {
    "enabled": False,
    "digest_hour": 18,
    "delivery_check_delay_days": 3,
    "delivery_check_retry_days": 2,
    "delivery_check_max_attempts": 3,
    "assumed_delivered_days": 10,
}

_BOUNDS: dict[str, tuple[int, int]] = {
    "digest_hour": (0, 23),
    "delivery_check_delay_days": (1, 7),
    "delivery_check_retry_days": (1, 7),
    "delivery_check_max_attempts": (1, 3),
    "assumed_delivered_days": (5, 30),
}

# Never create delivery checks for orders shipped before this lookback —
# enabling Autopilot on a store with months of stale SHIPPED orders must
# not blast old customers with "did you receive?" pings.
_CREATE_LOOKBACK_DAYS = 30

# Digest message caps (research R-09): Meta body parameters are limited and
# reject newlines, so the order list is ONE "; "-joined line of at most
# this many orders. Unlisted orders are reported via capped_note and can
# never be transitioned by the digest response (FR-009).
DIGEST_MAX_ORDERS = 10

# How long a digest accepts responses (taps or free-text) before replies
# are ignored (data-model §2 expires_at).
_DIGEST_TTL_HOURS = 48


@dataclass(frozen=True)
class CodAutopilotConfig:
    """Clamped per-store Autopilot configuration."""

    enabled: bool
    digest_hour: int
    delivery_check_delay_days: int
    delivery_check_retry_days: int
    delivery_check_max_attempts: int
    assumed_delivered_days: int


def get_cod_autopilot_settings(store_settings: dict | None) -> CodAutopilotConfig:
    """Read the ``cod_autopilot`` block, applying defaults + bounds clamps."""
    raw = (store_settings or {}).get("cod_autopilot") or {}
    if not isinstance(raw, dict):
        raw = {}

    def _int(key: str) -> int:
        lo, hi = _BOUNDS[key]
        try:
            value = int(raw.get(key, COD_AUTOPILOT_DEFAULTS[key]))
        except (TypeError, ValueError):
            value = int(COD_AUTOPILOT_DEFAULTS[key])
        return max(lo, min(hi, value))

    return CodAutopilotConfig(
        enabled=bool(raw.get("enabled", False)),
        digest_hour=_int("digest_hour"),
        delivery_check_delay_days=_int("delivery_check_delay_days"),
        delivery_check_retry_days=_int("delivery_check_retry_days"),
        delivery_check_max_attempts=_int("delivery_check_max_attempts"),
        assumed_delivered_days=_int("assumed_delivered_days"),
    )


def store_local_now(country: str | None, now: datetime | None = None) -> datetime:
    """Current time in the store's market timezone (research R-02).

    Stores carry no timezone column; the market registry's zone (EG →
    Africa/Cairo, SA → Asia/Riyadh) is the best available store-local
    clock.
    """
    from src.application.services.market_registry import get_market

    base = now or datetime.now(UTC)
    try:
        return base.astimezone(ZoneInfo(get_market(country).timezone))
    except Exception:  # noqa: BLE001 — bad zone data must never break a sweep
        return base


# ─────────────────────────────────────────────────────────────────────
# Exceptions-reply grammar (research R-04) — strict, no-op on ambiguity.
# ─────────────────────────────────────────────────────────────────────

# Accepted lead-in keywords before the number list. Optional — a bare
# "2, 5" also parses. Anything else in the text → unparseable → no-op.
_EXCEPT_KEYWORDS = ("except", "الا", "إلا", "ماعدا", "ما عدا", "بدون")

_ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"
_EASTERN_ARABIC = "۰۱۲۳۴۵۶۷۸۹"


def _normalize_digits(text: str) -> str:
    """Map Arabic-Indic (and Eastern Arabic) digits to ASCII."""
    for digits in (_ARABIC_INDIC, _EASTERN_ARABIC):
        for i, ch in enumerate(digits):
            text = text.replace(ch, str(i))
    return text


def parse_exceptions_reply(text: str, valid_numbers: set[int]) -> list[int] | None:
    """Parse a merchant's digest exceptions reply into item numbers.

    Grammar (FR-006/FR-007): an optional keyword (``except`` / ``الا`` /
    ``ماعدا`` / ``بدون``) followed by digits separated by ``,`` / ``،`` /
    whitespace. ASCII and Arabic-Indic digits both accepted. EVERY number
    must be a valid item number of the digest; any other text, an
    out-of-range number, or an empty list → ``None`` (caller replies with
    usage help and changes NOTHING — ambiguity is always a no-op).
    """
    if not text:
        return None
    normalized = _normalize_digits(text.strip().lower())
    for kw in _EXCEPT_KEYWORDS:
        if normalized.startswith(kw):
            normalized = normalized[len(kw) :]
            break
    normalized = normalized.strip().strip(":").strip()
    if not normalized:
        return None
    # After the keyword, ONLY digits + separators are allowed.
    if not re.fullmatch(r"[\d,،\s\.]+", normalized):
        return None
    parts = [p for p in re.split(r"[,،\s\.]+", normalized) if p]
    if not parts:
        return None
    numbers: list[int] = []
    for part in parts:
        try:
            n = int(part)
        except ValueError:
            return None
        if n not in valid_numbers:
            return None
        if n not in numbers:
            numbers.append(n)
    return numbers or None


# ─────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────


def _locator(subdomain: str | None, entity_id: UUID | str) -> str:
    """Self-describing ``<subdomain>/<id>`` payload segment (existing
    convention from the COD confirm-request flow)."""
    return f"{subdomain}/{entity_id}" if subdomain else str(entity_id)


def _parse_locator_id(payload: str) -> UUID | None:
    """Extract the UUID from an ``<action>:<subdomain>/<id>`` payload."""
    from src.application.services.order_confirmation_service import _parse_order_id

    return _parse_order_id(payload)


def _order_phone(order_model) -> str | None:
    """Customer phone for an order — shipping address first (guest
    checkout stores it there), no separate customer fetch needed."""
    addr = order_model.shipping_address or {}
    phone = addr.get("phone")
    return str(phone) if phone else None


async def _has_courier_shipment(session: AsyncSession, order_id: UUID) -> bool:
    """Orders with a carrier shipment already get automated status updates
    via webhooks — Autopilot excludes them entirely (FR-001)."""
    from src.infrastructure.database.models.tenant.shipment import ShipmentModel

    row = (
        await session.execute(
            select(ShipmentModel.id).where(ShipmentModel.order_id == order_id).limit(1)
        )
    ).scalar_one_or_none()
    return row is not None


def _resolve_language(store_settings: dict | None, default_language: str | None) -> str:
    """Store message language → Meta locale ("en_US" / "ar"), mirroring
    ``_resolve_send_context`` in whatsapp_notification_handler."""
    wa_pref = str(
        ((store_settings or {}).get("whatsapp") or {}).get("message_language") or "auto"
    ).lower()
    if wa_pref == "ar":
        raw = "ar"
    elif wa_pref == "en":
        raw = "en"
    else:
        raw = (default_language or "ar").lower()
    return "en_US" if raw.startswith("en") else "ar"


def _status_value(status) -> str:
    return getattr(status, "value", str(status))


async def _template_status(
    session: AsyncSession, store_id: UUID, template_name: str, language: str
) -> str | None:
    """Template APPROVED-check lookup, tolerant of en/en_US seed drift."""
    from src.infrastructure.database.models.tenant.whatsapp_template import (
        WhatsAppTemplateModel,
    )

    candidates = [language]
    if language == "en_US":
        candidates.append("en")
    elif language == "en":
        candidates.append("en_US")
    rows = (
        (
            await session.execute(
                select(WhatsAppTemplateModel).where(
                    WhatsAppTemplateModel.store_id == store_id,
                    WhatsAppTemplateModel.name == template_name,
                    WhatsAppTemplateModel.language.in_(candidates),
                )
            )
        )
        .scalars()
        .all()
    )
    row = (
        next((t for t in rows if t.language == language), None)
        or next((t for t in rows if _status_value(t.status) == "APPROVED"), None)
        or (rows[0] if rows else None)
    )
    return row.status if row is not None else None


async def _already_sent(
    session: AsyncSession,
    store_id: UUID,
    phone: str,
    template_name: str,
    event_tag: str,
) -> bool:
    """message_log idempotency scan (FR-015), keyed on event_tag."""
    from src.infrastructure.repositories.message_log_repository import (
        MessageLogRepository,
    )

    recent = await MessageLogRepository(session).get_by_phone(store_id, phone, limit=50)
    success = {"sent", "delivered", "read"}
    for log_row in recent:
        meta = getattr(log_row, "metadata", None) or {}
        if (
            log_row.template_name == template_name
            and meta.get("event_tag") == event_tag
            and _status_value(log_row.status) in success
        ):
            return True
    return False


async def _persist_log(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    store_id: UUID,
    phone: str,
    template_name: str,
    message_id: str | None,
    status_str: str,
    metadata: dict,
) -> None:
    from src.infrastructure.events.handlers.whatsapp_notification_handler import (
        _persist_message_log,
    )

    await _persist_message_log(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        phone=phone,
        template_name=template_name,
        message_id=message_id,
        status_str=status_str,
        metadata=metadata,
    )


def _build_status_use_case(session: AsyncSession):
    """UpdateOrderStatusUseCase wired to the given session — the single
    choke point every Autopilot transition goes through (FR-020)."""
    from src.application.use_cases.orders.update_order_status import (
        UpdateOrderStatusUseCase,
    )
    from src.infrastructure.events.setup import get_event_bus
    from src.infrastructure.repositories.customer_repository import CustomerRepository
    from src.infrastructure.repositories.order_repository import OrderRepository
    from src.infrastructure.repositories.shopify_repository import (
        NetworkReputationRepository,
    )
    from src.infrastructure.repositories.store_repository import StoreRepository

    return UpdateOrderStatusUseCase(
        order_repository=OrderRepository(session),
        store_repository=StoreRepository(session),
        customer_repository=CustomerRepository(session),
        event_bus=get_event_bus(),
        network_repository=NetworkReputationRepository(session),
    )


# ─────────────────────────────────────────────────────────────────────
# US2 — daily ship digest
# ─────────────────────────────────────────────────────────────────────


async def send_daily_digests(
    session: AsyncSession, now: datetime | None = None
) -> dict:
    """Send the daily ship digest to every store whose local hour matches.

    Called hourly by ``tasks.cod_autopilot_send_digests`` under RLS bypass;
    narrows to each store's tenant for writes. Idempotent three ways: the
    per-(store, date) UNIQUE digest row, the message_log event_tag, and the
    hour match itself.
    """
    from src.core.entities.order import OrderStatus
    from src.infrastructure.database.models.tenant.order import OrderModel
    from src.infrastructure.database.models.tenant.store import StoreModel
    from src.infrastructure.database.models.tenant.whatsapp_ship_digest import (
        WhatsAppShipDigestModel,
    )
    from src.infrastructure.repositories.whatsapp_ship_digest_repository import (
        WhatsAppShipDigestRepository,
    )
    from src.infrastructure.tenancy.rls import enable_rls_bypass, narrow_to_tenant

    stats = {"stores_scanned": 0, "digests_sent": 0, "skipped": 0, "errors": 0}
    now = now or datetime.now(UTC)

    stores = (
        (
            await session.execute(
                select(StoreModel).where(
                    StoreModel.settings.contains({"cod_autopilot": {"enabled": True}})
                )
            )
        )
        .scalars()
        .all()
    )
    stats["stores_scanned"] = len(stores)

    for store in stores:
        try:
            config = get_cod_autopilot_settings(store.settings)
            if not config.enabled:
                continue
            local_now = store_local_now(getattr(store, "country", None), now)
            if local_now.hour != config.digest_hour:
                continue
            if not store.contact_phone:
                stats["skipped"] += 1
                continue

            digest_repo = WhatsAppShipDigestRepository(session)
            digest_date: date = local_now.date()
            if await digest_repo.get_by_store_date(store.id, digest_date) is not None:
                continue  # one digest per store per day (FR-003)

            # Eligible orders: CONFIRMED COD with no courier shipment.
            orders = (
                (
                    await session.execute(
                        select(OrderModel)
                        .where(
                            OrderModel.store_id == store.id,
                            OrderModel.status == OrderStatus.CONFIRMED,
                            OrderModel.payment_method == "cod",
                        )
                        .order_by(OrderModel.created_at.asc())
                        .limit(DIGEST_MAX_ORDERS * 5)
                    )
                )
                .scalars()
                .all()
            )
            eligible = []
            for o in orders:
                if not await _has_courier_shipment(session, o.id):
                    eligible.append(o)
            if not eligible:
                continue  # zero eligible → no digest (FR-003)

            listed = eligible[:DIGEST_MAX_ORDERS]
            capped = len(eligible) - len(listed)

            language = _resolve_language(store.settings, store.default_language)
            tmpl_status = await _template_status(
                session, store.id, "cod_ship_digest_v1", language
            )
            if _status_value(tmpl_status) != "APPROVED":
                stats["skipped"] += 1
                continue
            merchant_prefs = (store.settings or {}).get("whatsapp_notifications") or {}
            if not bool(merchant_prefs.get("cod_autopilot_digest", True)):
                stats["skipped"] += 1
                continue
            event_tag = f"digest:{digest_date.isoformat()}"
            if await _already_sent(
                session, store.id, store.contact_phone, "cod_ship_digest_v1", event_tag
            ):
                continue

            await narrow_to_tenant(session, store.tenant_id)

            digest = WhatsAppShipDigestModel(
                tenant_id=store.tenant_id,
                store_id=store.id,
                digest_date=digest_date,
                sent_at=now,
                merchant_phone=store.contact_phone,
                order_items=[
                    {
                        "n": i + 1,
                        "order_id": str(o.id),
                        "order_number": o.order_number,
                    }
                    for i, o in enumerate(listed)
                ],
                capped_count=capped,
                expires_at=now + timedelta(hours=_DIGEST_TTL_HOURS),
            )
            await digest_repo.create(digest)

            # Single-line list — Meta rejects newlines in body params (R-09).
            def _line(i: int, o) -> str:
                addr = o.shipping_address or {}
                city = str(addr.get("city") or "").strip()
                name = str(
                    f"{addr.get('first_name') or ''} {addr.get('last_name') or ''}"
                ).strip()
                amount = f"{o.total / 100:.0f} {o.currency}"
                bits = [f"{i}) {o.order_number}"]
                if name:
                    bits.append(name)
                if city:
                    bits.append(city)
                bits.append(amount)
                return " - ".join(bits)

            orders_line = "; ".join(_line(i + 1, o) for i, o in enumerate(listed))
            if language == "ar":
                capped_note = (
                    f"في {capped} أوردر كمان في لوحة التحكم."
                    if capped
                    else "كل الأوردرات المتأكدة موجودة في القايمة."
                )
            else:
                capped_note = (
                    f"Plus {capped} more in your dashboard."
                    if capped
                    else "All your confirmed orders are listed."
                )

            from src.core.interfaces.services.messaging_service import MessageRecipient
            from src.infrastructure.external_services.whatsapp import (
                get_whatsapp_service,
            )

            service = await get_whatsapp_service(store.id, session, store.tenant_id)
            result = await service.send_ship_digest(
                MessageRecipient(
                    phone=store.contact_phone, name=store.name, language=language
                ),
                store_name=store.name,
                order_count=str(len(listed)),
                orders_line=orders_line,
                capped_note=capped_note,
                shipall_payload=_locator(store.subdomain, digest.id),
            )
            if result.success:
                digest.message_id = result.message_id
                await session.commit()
                await _persist_log(
                    session,
                    tenant_id=store.tenant_id,
                    store_id=store.id,
                    phone=store.contact_phone,
                    template_name="cod_ship_digest_v1",
                    message_id=result.message_id,
                    status_str=_status_value(result.status),
                    metadata={"digest_id": str(digest.id), "event_tag": event_tag},
                )
                stats["digests_sent"] += 1
                logger.info(
                    "autopilot_digest_sent",
                    store_id=str(store.id),
                    orders=len(listed),
                    capped=capped,
                )
            else:
                # Keep the digest row (one attempt per day — a failed Meta
                # send shouldn't retry-spam within the same day) but record
                # the failure loudly.
                await session.commit()
                stats["errors"] += 1
                logger.warning(
                    "autopilot_digest_send_failed",
                    store_id=str(store.id),
                    error=result.error_message,
                )
        except Exception:
            stats["errors"] += 1
            await session.rollback()
            logger.exception("autopilot_digest_store_failed", store_id=str(store.id))
        finally:
            try:
                await enable_rls_bypass(session)
            except Exception:
                logger.exception("autopilot_digest_bypass_reset_failed")

    await session.commit()
    return stats


async def _ship_digest_orders(
    session: AsyncSession,
    digest,
    excepted: list[int],
) -> tuple[int, int]:
    """Transition the digest's listed-minus-excepted orders to SHIPPED.

    CONFIRMED orders take two canonical hops (CONFIRMED → PROCESSING →
    SHIPPED) through the status use case so every side-effect (events,
    activity log, customer shipped-notification) fires exactly as a manual
    merchant flow would. Orders that already left CONFIRMED are skipped
    without error (FR-005). Returns (shipped, skipped).
    """
    from src.application.dto.order import UpdateOrderStatusDTO
    from src.core.entities.order import OrderStatus
    from src.infrastructure.repositories.order_repository import OrderRepository
    from src.infrastructure.repositories.store_repository import StoreRepository

    order_repo = OrderRepository(session)
    store = await StoreRepository(session).get_by_id(digest.store_id)
    if store is None:
        return 0, len(digest.order_items or [])

    use_case = _build_status_use_case(session)
    shipped = 0
    skipped = 0
    excepted_set = set(excepted)

    for item in digest.order_items or []:
        n = int(item.get("n", 0))
        if n in excepted_set:
            continue
        try:
            order_id = UUID(str(item.get("order_id")))
        except ValueError:
            skipped += 1
            continue
        try:
            order = await order_repo.get_by_id(order_id)
            if order is None or order.status != OrderStatus.CONFIRMED:
                skipped += 1  # already moved / cancelled — skip, no error
                continue
            await use_case.execute(
                order_id=order_id,
                dto=UpdateOrderStatusDTO(
                    status="processing", reason="autopilot_digest"
                ),
                store_id=digest.store_id,
                user_id=store.owner_id,
            )
            await use_case.execute(
                order_id=order_id,
                dto=UpdateOrderStatusDTO(
                    status="shipped",
                    reason="autopilot_digest",
                    source="merchant_digest",
                ),
                store_id=digest.store_id,
                user_id=store.owner_id,
            )
            shipped += 1
        except Exception:
            skipped += 1
            logger.exception(
                "autopilot_digest_ship_failed",
                order_id=str(order_id),
                digest_id=str(digest.id),
            )
    return shipped, skipped


async def handle_shipall(
    session: AsyncSession, *, payload: str, from_phone: str
) -> bool:
    """Handle the merchant's "All shipped" quick-reply tap (FR-005/FR-008).

    Defensive + idempotent: bad payload, unknown digest, phone mismatch,
    expired, or already-processed digests all short-circuit (the
    already-processed case still gets an ack so the merchant isn't left
    hanging on a double tap).
    """
    from src.application.services.order_confirmation_service import _phones_match
    from src.infrastructure.repositories.whatsapp_ship_digest_repository import (
        WhatsAppShipDigestRepository,
    )

    digest_id = _parse_locator_id(payload)
    if digest_id is None:
        return False
    repo = WhatsAppShipDigestRepository(session)
    digest = await repo.get_by_id(digest_id)
    if digest is None:
        return False
    if not _phones_match(digest.merchant_phone, from_phone):
        logger.warning("autopilot_shipall_phone_mismatch", digest_id=str(digest_id))
        return False

    now = datetime.now(UTC)
    if digest.processed_at is not None:
        await _reply_to_merchant(session, digest, _digest_ack_already(digest))
        return True
    if digest.expires_at and digest.expires_at <= now:
        return False

    await repo.mark_processed(
        digest, response_type="all_shipped", response_raw=None, excepted_numbers=None
    )
    await session.commit()

    shipped, skipped = await _ship_digest_orders(session, digest, excepted=[])
    await session.commit()
    logger.info(
        "autopilot_shipall_processed",
        digest_id=str(digest_id),
        shipped=shipped,
        skipped=skipped,
    )
    await _reply_to_merchant(
        session, digest, _digest_ack_summary(digest, shipped, skipped)
    )
    return True


async def handle_digest_text_reply(
    session: AsyncSession, *, text: str, from_phone: str
) -> bool:
    """Handle a merchant free-text reply against their open digest.

    Only fires when the sender matches an open, unexpired digest's
    merchant phone — everything else is left for the normal inbound
    pipeline. Parseable exceptions list → ship listed-minus-excepted;
    anything ambiguous → localized help reply, ZERO status changes
    (FR-007). Returns True when the message was consumed by Autopilot.
    """
    from src.infrastructure.repositories.whatsapp_ship_digest_repository import (
        WhatsAppShipDigestRepository,
    )

    now = datetime.now(UTC)
    repo = WhatsAppShipDigestRepository(session)
    digest = await repo.get_open_for_phone(from_phone, now)
    if digest is None:
        return False

    valid_numbers = {int(item.get("n", 0)) for item in (digest.order_items or [])}
    excepted = parse_exceptions_reply(text, valid_numbers)
    if excepted is None:
        await _reply_to_merchant(session, digest, _digest_help_text(digest))
        return True

    await repo.mark_processed(
        digest,
        response_type="exceptions",
        response_raw=text,
        excepted_numbers=excepted,
    )
    await session.commit()

    shipped, skipped = await _ship_digest_orders(session, digest, excepted=excepted)
    await session.commit()
    logger.info(
        "autopilot_digest_exceptions_processed",
        digest_id=str(digest.id),
        excepted=excepted,
        shipped=shipped,
        skipped=skipped,
    )
    await _reply_to_merchant(
        session, digest, _digest_ack_summary(digest, shipped, skipped, excepted)
    )
    return True


# ─────────────────────────────────────────────────────────────────────
# US1 — customer delivery checks
# ─────────────────────────────────────────────────────────────────────


async def create_due_checks(session: AsyncSession, now: datetime | None = None) -> dict:
    """Create delivery-check rows for newly-shipped eligible orders.

    Eligibility (FR-001): COD, Autopilot enabled, no courier shipment,
    shipped (``fulfilled_at`` — the persisted ship timestamp; the
    entity's ``shipped_at`` is in-memory only) at least
    ``delivery_check_delay_days`` ago, within the 30-day lookback, and no
    existing check row (UNIQUE order_id backstops races).
    """
    from src.core.entities.order import OrderStatus
    from src.infrastructure.database.models.tenant.order import OrderModel
    from src.infrastructure.database.models.tenant.store import StoreModel
    from src.infrastructure.database.models.tenant.whatsapp_delivery_check import (
        WhatsAppDeliveryCheckModel,
    )
    from src.infrastructure.repositories.whatsapp_delivery_check_repository import (
        WhatsAppDeliveryCheckRepository,
    )
    from src.infrastructure.tenancy.rls import enable_rls_bypass, narrow_to_tenant

    stats = {"scanned": 0, "created": 0, "errors": 0}
    now = now or datetime.now(UTC)
    check_repo = WhatsAppDeliveryCheckRepository(session)
    store_cache: dict = {}

    candidates = (
        (
            await session.execute(
                select(OrderModel)
                .where(
                    OrderModel.status == OrderStatus.SHIPPED,
                    OrderModel.payment_method == "cod",
                    OrderModel.fulfilled_at.isnot(None),
                    OrderModel.fulfilled_at
                    >= now - timedelta(days=_CREATE_LOOKBACK_DAYS),
                )
                .order_by(OrderModel.fulfilled_at.asc())
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    stats["scanned"] = len(candidates)

    for model in candidates:
        try:
            cached = store_cache.get(model.store_id)
            if cached is None:
                store_row = (
                    await session.execute(
                        select(StoreModel).where(StoreModel.id == model.store_id)
                    )
                ).scalar_one_or_none()
                if store_row is None:
                    continue
                cached = get_cod_autopilot_settings(store_row.settings)
                store_cache[model.store_id] = cached
            config: CodAutopilotConfig = cached
            if not config.enabled:
                continue
            if model.fulfilled_at > now - timedelta(
                days=config.delivery_check_delay_days
            ):
                continue  # delay not elapsed yet
            if await check_repo.get_by_order(model.id) is not None:
                continue
            phone = _order_phone(model)
            if await _has_courier_shipment(session, model.id):
                continue

            await narrow_to_tenant(session, model.tenant_id)
            check = WhatsAppDeliveryCheckModel(
                tenant_id=model.tenant_id,
                store_id=model.store_id,
                order_id=model.id,
                # A missing phone still gets a row — it can never be sent,
                # so it rides the fallback path to closure (FR-015/FR-016).
                customer_phone=phone or "",
                max_attempts=config.delivery_check_max_attempts,
                next_attempt_at=now if phone else None,
                outcome="pending" if phone else "response_exhausted",
                assumed_delivered_due_at=model.fulfilled_at
                + timedelta(days=config.assumed_delivered_days),
            )
            await check_repo.create(check)
            stats["created"] += 1
        except Exception:
            stats["errors"] += 1
            await session.rollback()
            logger.exception("autopilot_check_create_failed", order_id=str(model.id))
        finally:
            try:
                await enable_rls_bypass(session)
            except Exception:
                logger.exception("autopilot_check_bypass_reset_failed")

    await session.commit()
    return stats


async def send_due_checks(session: AsyncSession, now: datetime | None = None) -> dict:
    """Send due delivery-check messages through the full guard gate.

    Guard reuses ``_resolve_send_context`` (merchant toggle
    ``whatsapp_notifications.delivery_check``, customer opt-out, template
    APPROVED, message_log idempotency — FR-015). A hard guard block (not
    replay-idempotency) exhausts the row so it progresses to the fallback
    instead of hanging forever.
    """
    from src.core.services.whatsapp_send_guard import check as guard_check
    from src.infrastructure.database.models.tenant.order import OrderModel
    from src.infrastructure.database.models.tenant.store import StoreModel
    from src.infrastructure.events.handlers.whatsapp_notification_handler import (
        _resolve_send_context,
    )
    from src.infrastructure.repositories.whatsapp_delivery_check_repository import (
        WhatsAppDeliveryCheckRepository,
    )
    from src.infrastructure.tenancy.rls import enable_rls_bypass, narrow_to_tenant

    stats = {"due": 0, "sent": 0, "blocked": 0, "exhausted": 0, "errors": 0}
    now = now or datetime.now(UTC)
    check_repo = WhatsAppDeliveryCheckRepository(session)
    due = await check_repo.list_due_sends(now)
    stats["due"] = len(due)

    for row in due:
        try:
            order = (
                await session.execute(
                    select(OrderModel).where(OrderModel.id == row.order_id)
                )
            ).scalar_one_or_none()
            store = (
                await session.execute(
                    select(StoreModel).where(StoreModel.id == row.store_id)
                )
            ).scalar_one_or_none()
            if order is None or store is None:
                row.next_attempt_at = None
                continue
            config = get_cod_autopilot_settings(store.settings)
            if not config.enabled:
                # FR-023: disabling stops sends immediately; row stays put.
                row.next_attempt_at = None
                continue

            attempt = row.attempts + 1
            event_tag = f"dlvcheck:{row.order_id}:{attempt}"
            resolution = await _resolve_send_context(
                session,
                store_id=row.store_id,
                customer_id=order.customer_id,
                template_name="order_delivery_check_v1",
                idempotency_event_tag=event_tag,
                order_id=row.order_id,
                notification_pref_key="delivery_check",
            )
            await narrow_to_tenant(session, row.tenant_id)
            if resolution is None:
                _exhaust(row, now)
                stats["exhausted"] += 1
                continue
            ctx, extras = resolution
            decision = guard_check(ctx)
            if not decision.allowed:
                reason = decision.reason.value if decision.reason else "unknown"
                if reason == "already_sent":
                    # Replay — bookkeeping as if this attempt was sent.
                    _record_attempt(row, now, config)
                else:
                    # Opt-out / no template / toggle off — will never send;
                    # progress to fallback (FR-015).
                    _exhaust(row, now)
                    stats["exhausted"] += 1
                stats["blocked"] += 1
                continue

            from src.core.interfaces.services.messaging_service import (
                MessageRecipient,
            )
            from src.infrastructure.external_services.whatsapp import (
                get_whatsapp_service,
            )

            service = await get_whatsapp_service(
                row.store_id, session, extras["tenant_id"]
            )
            result = await service.send_delivery_check(
                MessageRecipient(
                    phone=extras["customer_phone"],
                    name=extras["customer_name"],
                    language=extras["language"],
                ),
                order_number=order.order_number,
                store_name=extras["store_name"],
                check_payload=_locator(extras.get("store_subdomain"), row.order_id),
            )
            if result.success:
                _record_attempt(row, now, config)
                await session.commit()
                await _persist_log(
                    session,
                    tenant_id=extras["tenant_id"],
                    store_id=row.store_id,
                    phone=extras["customer_phone"],
                    template_name="order_delivery_check_v1",
                    message_id=result.message_id,
                    status_str=_status_value(result.status),
                    metadata={
                        "order_id": str(row.order_id),
                        "event_tag": event_tag,
                    },
                )
                stats["sent"] += 1
            else:
                # Transient send failure — retry on the retry cadence
                # without burning an attempt, bounded by the fallback due
                # date so a permanently-failing send still closes (FR-016).
                row.next_attempt_at = now + timedelta(
                    days=config.delivery_check_retry_days
                )
                stats["errors"] += 1
                logger.warning(
                    "autopilot_check_send_failed",
                    order_id=str(row.order_id),
                    error=result.error_message,
                )
        except Exception:
            stats["errors"] += 1
            await session.rollback()
            logger.exception("autopilot_check_send_error", order_id=str(row.order_id))
        finally:
            try:
                await enable_rls_bypass(session)
            except Exception:
                logger.exception("autopilot_send_bypass_reset_failed")

    await session.commit()
    return stats


def _record_attempt(row, now: datetime, config: CodAutopilotConfig) -> None:
    row.attempts += 1
    row.first_sent_at = row.first_sent_at or now
    row.last_sent_at = now
    if row.attempts >= row.max_attempts:
        row.next_attempt_at = None
    else:
        row.next_attempt_at = now + timedelta(days=config.delivery_check_retry_days)


def _exhaust(row, now: datetime) -> None:
    row.outcome = "response_exhausted"
    row.next_attempt_at = None


async def mark_exhausted(session: AsyncSession, now: datetime | None = None) -> int:
    """Flip pending rows whose final attempt got no response within the
    retry window to ``response_exhausted`` (data-model §1)."""
    from src.infrastructure.database.models.tenant.whatsapp_delivery_check import (
        WhatsAppDeliveryCheckModel,
    )

    now = now or datetime.now(UTC)
    rows = (
        (
            await session.execute(
                select(WhatsAppDeliveryCheckModel)
                .where(
                    WhatsAppDeliveryCheckModel.outcome == "pending",
                    WhatsAppDeliveryCheckModel.next_attempt_at.is_(None),
                    WhatsAppDeliveryCheckModel.attempts
                    >= WhatsAppDeliveryCheckModel.max_attempts,
                    WhatsAppDeliveryCheckModel.last_sent_at.isnot(None),
                    WhatsAppDeliveryCheckModel.last_sent_at <= now - timedelta(days=2),
                )
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        _exhaust(row, now)
    await session.commit()
    return len(rows)


async def handle_delivery_response(
    session: AsyncSession, *, action: str, payload: str, from_phone: str
) -> bool:
    """Handle a customer's delivery-check button tap (FR-011..FR-014, FR-017).

    ``action`` ∈ {dlvyes, dlvnot, dlvref}. Defensive + idempotent per the
    order_confirmation_service contract: bad payload / unknown order /
    phone mismatch / duplicate taps never double-apply. Late taps after
    the order closed: Received → ack only; Refused → ``late_contradiction``
    flag for the exception queue (it contradicts the recorded outcome).
    """
    from src.application.dto.order import UpdateOrderStatusDTO
    from src.application.services.order_confirmation_service import _phones_match
    from src.core.entities.order import OrderStatus
    from src.infrastructure.database.models.tenant.store import StoreModel
    from src.infrastructure.repositories.order_repository import OrderRepository
    from src.infrastructure.repositories.whatsapp_delivery_check_repository import (
        TERMINAL_OUTCOMES,
        WhatsAppDeliveryCheckRepository,
    )

    order_id = _parse_locator_id(payload)
    if order_id is None:
        return False
    check_repo = WhatsAppDeliveryCheckRepository(session)
    row = await check_repo.get_by_order(order_id)
    if row is None:
        return False
    if row.customer_phone and not _phones_match(row.customer_phone, from_phone):
        logger.warning("autopilot_response_phone_mismatch", order_id=str(order_id))
        return False

    order_repo = OrderRepository(session)
    order = await order_repo.get_by_id(order_id)
    if order is None:
        return False
    store = (
        await session.execute(select(StoreModel).where(StoreModel.id == row.store_id))
    ).scalar_one_or_none()
    if store is None:
        return False

    now = datetime.now(UTC)
    language = _resolve_language(store.settings, store.default_language)

    if action == "dlvyes":
        # Idempotent replay.
        if row.outcome == "delivered_confirmed":
            await _ack_customer(session, row, store, "received", language, order)
            return True
        if row.outcome in TERMINAL_OUTCOMES or order.status != OrderStatus.SHIPPED:
            # Late tap after another closure (assumed/RTO/manual) — the
            # human agrees or it's moot; ack without touching the order.
            await _ack_customer(session, row, store, "received", language, order)
            return True
        # Terminal-outcome-first so the use case's supersede hook no-ops.
        row.response = "received"
        row.responded_at = now
        row.outcome = "delivered_confirmed"
        row.next_attempt_at = None
        await session.commit()
        try:
            await _build_status_use_case(session).execute(
                order_id=order_id,
                dto=UpdateOrderStatusDTO(
                    status="delivered",
                    reason="customer_confirmed_via_whatsapp",
                    source="customer_confirmed",
                ),
                store_id=row.store_id,
                user_id=store.owner_id,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            row.outcome = (
                "pending" if row.attempts < row.max_attempts else ("response_exhausted")
            )
            await session.commit()
            logger.exception("autopilot_deliver_failed", order_id=str(order_id))
            return False
        logger.info(
            "autopilot_delivery_confirmed",
            order_id=str(order_id),
            store_id=str(row.store_id),
        )
        await _ack_customer(session, row, store, "received", language, order)
        return True

    if action == "dlvnot":
        if row.outcome in TERMINAL_OUTCOMES or row.outcome == "exception":
            await _ack_customer(session, row, store, "not_yet", language, order)
            return True
        row.response = "not_yet"
        row.responded_at = now
        config = get_cod_autopilot_settings(store.settings)
        if row.attempts < row.max_attempts:
            row.outcome = "pending"
            row.next_attempt_at = now + timedelta(days=config.delivery_check_retry_days)
        else:
            _exhaust(row, now)
        await session.commit()
        await _ack_customer(session, row, store, "not_yet", language, order)
        return True

    if action == "dlvref":
        if row.response == "refused":
            await _ack_customer(session, row, store, "refused", language, order)
            return True
        row.response = "refused"
        row.responded_at = now
        if row.outcome in TERMINAL_OUTCOMES:
            # Contradicts a recorded closure — flag for the merchant, do
            # NOT touch the order (edge case, spec Edge Cases).
            row.exception_reason = "late_contradiction"
            row.exception_resolved_at = None
        else:
            row.outcome = "exception"
            row.exception_reason = "refused"
            row.next_attempt_at = None
        await session.commit()
        logger.info("autopilot_delivery_refused", order_id=str(order_id))
        await _ack_customer(session, row, store, "refused", language, order)
        return True

    return False


# ─────────────────────────────────────────────────────────────────────
# US3 — assumed-delivered fallback
# ─────────────────────────────────────────────────────────────────────


async def close_assumed_delivered(
    session: AsyncSession, now: datetime | None = None
) -> dict:
    """Close exhausted, unanswered checks as assumed-delivered (FR-016).

    Runs daily AFTER the auto-RTO sweep so RTO takes precedence (FR-018).
    Re-checks per row: Autopilot still enabled (FR-023) and the order is
    still SHIPPED. The metadata stamp goes on BEFORE the use case (clone
    of the auto-RTO idempotency pattern), and the row's terminal outcome
    is set first so the use case's supersede hook no-ops.
    """
    from src.application.dto.order import UpdateOrderStatusDTO
    from src.core.entities.order import OrderStatus
    from src.infrastructure.database.models.tenant.store import StoreModel
    from src.infrastructure.repositories.order_repository import OrderRepository
    from src.infrastructure.repositories.whatsapp_delivery_check_repository import (
        WhatsAppDeliveryCheckRepository,
    )
    from src.infrastructure.tenancy.rls import enable_rls_bypass, narrow_to_tenant

    stats = {"due": 0, "closed": 0, "skipped": 0, "errors": 0}
    now = now or datetime.now(UTC)
    check_repo = WhatsAppDeliveryCheckRepository(session)
    due = await check_repo.list_fallback_due(now)
    stats["due"] = len(due)

    for row in due:
        try:
            store = (
                await session.execute(
                    select(StoreModel).where(StoreModel.id == row.store_id)
                )
            ).scalar_one_or_none()
            if store is None:
                continue
            config = get_cod_autopilot_settings(store.settings)
            if not config.enabled:
                stats["skipped"] += 1  # FR-023 — no automated closures
                continue

            await narrow_to_tenant(session, row.tenant_id)
            order_repo = OrderRepository(session)
            order = await order_repo.get_by_id(row.order_id)
            if order is None or order.status != OrderStatus.SHIPPED:
                # Closed by another path (RTO sweep / manual / refund) —
                # that outcome wins (FR-018).
                row.outcome = "superseded"
                stats["skipped"] += 1
                continue

            # Idempotency stamp BEFORE the transition (auto-RTO pattern).
            order.metadata = {
                **(order.metadata or {}),
                "autopilot_assumed_delivered_at": now.isoformat(),
            }
            await order_repo.update(order)

            row.outcome = "assumed_delivered"
            row.next_attempt_at = None
            await session.commit()

            try:
                await _build_status_use_case(session).execute(
                    order_id=row.order_id,
                    dto=UpdateOrderStatusDTO(
                        status="delivered",
                        reason="autopilot_assumed_delivered",
                        source="assumed_delivered",
                    ),
                    store_id=row.store_id,
                    user_id=store.owner_id,
                )
                await session.commit()
                stats["closed"] += 1
                logger.info(
                    "autopilot_assumed_delivered",
                    order_id=str(row.order_id),
                    store_id=str(row.store_id),
                )
            except Exception:
                await session.rollback()
                row.outcome = "response_exhausted"  # retry next sweep
                await session.commit()
                raise
        except Exception:
            stats["errors"] += 1
            logger.exception(
                "autopilot_assumed_close_failed", order_id=str(row.order_id)
            )
        finally:
            try:
                await enable_rls_bypass(session)
            except Exception:
                logger.exception("autopilot_fallback_bypass_reset_failed")

    await session.commit()
    return stats


# ─────────────────────────────────────────────────────────────────────
# Localized replies (free-form; sent inside the open 24h service window
# for inbound-triggered replies, best-effort otherwise).
# ─────────────────────────────────────────────────────────────────────

_CUSTOMER_ACK: dict[str, dict[str, str]] = {
    "received": {
        "en": "Thanks for confirming! Enjoy your order {n} 💚",
        "ar": "شكراً لتأكيدك! بالهنا أوردرك {n} 💚",
    },
    "not_yet": {
        "en": "Got it — we'll check back with you about order {n} in a couple of days.",
        "ar": "تمام — هنطمن عليك تاني بخصوص الأوردر {n} بعد يومين.",
    },
    "refused": {
        "en": "Sorry to hear that. The store team has been notified about order {n}.",
        "ar": "نأسف لسماع ذلك. تم إبلاغ فريق المتجر بخصوص الأوردر {n}.",
    },
}


async def _ack_customer(
    session: AsyncSession, row, store, kind: str, language: str, order
) -> None:
    """Best-effort free-form ack of a delivery-check tap."""
    try:
        from src.infrastructure.external_services.whatsapp import get_whatsapp_service

        lang = "en" if language.startswith("en") else "ar"
        template = _CUSTOMER_ACK.get(kind, {}).get(lang)
        if not template or not row.customer_phone:
            return
        service = await get_whatsapp_service(row.store_id, session, row.tenant_id)
        await service.send_text_message(
            row.customer_phone, template.format(n=order.order_number)
        )
    except Exception:
        logger.exception("autopilot_customer_ack_failed", order_id=str(row.order_id))


def _digest_ack_summary(
    digest, shipped: int, skipped: int, excepted: list[int] | None = None
) -> dict[str, str]:
    held = f" ({len(excepted)} held back)" if excepted else ""
    held_ar = f" (تم استثناء {len(excepted)})" if excepted else ""
    skipped_en = f", {skipped} skipped" if skipped else ""
    skipped_ar = f"، وتم تخطي {skipped}" if skipped else ""
    return {
        "en": f"Done — {shipped} orders marked shipped{skipped_en}.{held}",
        "ar": f"تمام — تم تعليم {shipped} أوردر كمشحون{skipped_ar}.{held_ar}",
    }


def _digest_ack_already(digest) -> dict[str, str]:
    return {
        "en": "This digest was already handled — nothing changed.",
        "ar": "تم التعامل مع القايمة دي قبل كده — مفيش حاجة اتغيرت.",
    }


def _digest_help_text(digest) -> dict[str, str]:
    count = len(digest.order_items or [])
    return {
        "en": (
            "Sorry, I couldn't understand that. Tap the All shipped button, "
            f"or reply with the numbers you did NOT ship (1-{count}), for "
            "example: except 2, 5. You can also manage orders from your "
            "dashboard: https://numueg.app"
        ),
        "ar": (
            "معلش، الرسالة مش واضحة. اضغط زرار تم شحن الكل، أو رد بأرقام "
            f"الأوردرات اللي ماتشحنتش (1-{count})، مثال: ماعدا 2، 5. وتقدر "
            "كمان تدير الأوردرات من لوحة التحكم: https://numueg.app"
        ),
    }


async def _reply_to_merchant(
    session: AsyncSession, digest, texts: dict[str, str]
) -> None:
    """Best-effort free-form reply to the merchant's digest response
    (their inbound message opened the 24h service window)."""
    try:
        from src.infrastructure.database.models.tenant.store import StoreModel
        from src.infrastructure.external_services.whatsapp import get_whatsapp_service

        store = (
            await session.execute(
                select(StoreModel).where(StoreModel.id == digest.store_id)
            )
        ).scalar_one_or_none()
        if store is None:
            return
        language = _resolve_language(store.settings, store.default_language)
        text = texts["en"] if language.startswith("en") else texts["ar"]
        service = await get_whatsapp_service(digest.store_id, session, digest.tenant_id)
        await service.send_text_message(digest.merchant_phone, text)
    except Exception:
        logger.exception("autopilot_merchant_reply_failed", digest_id=str(digest.id))
