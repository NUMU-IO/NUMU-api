"""Merchant-facing order import from CSV.

Two-step flow:
1. ``suggest_mapping(csv_bytes, store)`` — parses headers + first rows, returns a
   proposed mapping keyed on the merchant's column names. Uses a synonyms table
   (EN + AR) for fuzzy matching, plus any mapping the merchant previously saved.
2. ``import_rows(csv_bytes, mapping, store, user_id)`` — creates orders, one
   per row, deduped by ``external_order_id`` (stored under ``Order.metadata``).
   Partial-success: bad rows are skipped with a reason, good rows persist.

The service is intentionally narrow — line_items aren't imported per-row, each
imported order gets a single synthetic "Imported order" line item carrying the
full total. Merchants can edit the order afterwards if they need real SKUs.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

from src.core.entities.customer import Customer
from src.core.entities.order import (
    FulfillmentStatus,
    Order,
    OrderLineItem,
    OrderShippingAddress,
    OrderStatus,
    PaymentStatus,
)
from src.core.value_objects.email import Email
from src.infrastructure.repositories import (
    CustomerRepository,
    OrderRepository,
    StoreRepository,
)

logger = logging.getLogger(__name__)

# ── Canonical field set ───────────────────────────────────────────────────

TARGET_FIELDS: tuple[str, ...] = (
    "external_order_id",
    "customer_name",
    "customer_phone",
    "customer_email",
    "shipping_address",
    "shipping_city",
    "total",
    "payment_status",
    "status",
    "notes",
    "order_date",
)

# Synonyms table — normalized (lowercase, stripped, collapsed whitespace).
# Covers the bulk of real-world merchant sheets (English + Arabic).
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "external_order_id": (
        "external id",
        "external order id",
        "order id",
        "order no",
        "order number",
        "order #",
        "ref",
        "reference",
        "ref no",
        "id",
        "رقم الطلب",
        "رقم الطلب القديم",
        "المرجع",
    ),
    "customer_name": (
        "name",
        "customer",
        "customer name",
        "full name",
        "buyer",
        "client",
        "الاسم",
        "اسم العميل",
        "العميل",
        "اسم",
        "الاسم الكامل",
    ),
    "customer_phone": (
        "phone",
        "mobile",
        "tel",
        "telephone",
        "phone number",
        "mobile number",
        "contact",
        "رقم الموبايل",
        "الموبايل",
        "الهاتف",
        "رقم الهاتف",
        "رقم",
        "موبايل",
        "هاتف",
    ),
    "customer_email": (
        "email",
        "e-mail",
        "mail",
        "email address",
        "البريد",
        "الإيميل",
        "البريد الإلكتروني",
        "إيميل",
    ),
    "shipping_address": (
        "address",
        "shipping address",
        "street",
        "delivery address",
        "العنوان",
        "عنوان الشحن",
        "عنوان التوصيل",
        "الشارع",
    ),
    "shipping_city": (
        "city",
        "governorate",
        "gov",
        "region",
        "state",
        "المدينة",
        "المحافظة",
        "المنطقة",
    ),
    "total": (
        "total",
        "amount",
        "price",
        "grand total",
        "order total",
        "value",
        "الإجمالي",
        "المبلغ",
        "السعر",
        "قيمة الطلب",
        "اجمالي",
    ),
    "payment_status": (
        "payment",
        "payment status",
        "paid",
        "paid?",
        "is paid",
        "حالة الدفع",
        "الدفع",
        "مدفوع",
    ),
    "status": (
        "status",
        "order status",
        "state",
        "الحالة",
        "حالة الطلب",
    ),
    "notes": (
        "notes",
        "note",
        "comments",
        "comment",
        "remarks",
        "ملاحظات",
        "ملاحظة",
    ),
    "order_date": (
        "date",
        "order date",
        "created",
        "created at",
        "ordered at",
        "التاريخ",
        "تاريخ الطلب",
    ),
}

SETTINGS_MAPPING_KEY = "order_import_mapping"

MAX_SUGGEST_PREVIEW_ROWS = 5
MAX_IMPORT_ROWS = 5000


# ── DTOs ──────────────────────────────────────────────────────────────────


@dataclass
class MappingSuggestion:
    columns: list[str]
    sample_rows: list[dict[str, str]]
    suggested_mapping: dict[str, str | None]
    target_fields: list[str] = field(default_factory=lambda: list(TARGET_FIELDS))


@dataclass
class ImportRowError:
    row: int
    reason: str


@dataclass
class ImportResult:
    total_rows: int
    created: int
    skipped: int
    errors: list[ImportRowError]


# ── Helpers ──────────────────────────────────────────────────────────────


def _normalize(header: str) -> str:
    return re.sub(
        r"\s+", " ", header.strip().lower().replace(".", "").replace(":", "")
    ).strip()


def _read_csv(csv_bytes: bytes) -> list[dict[str, str]]:
    text = csv_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, str]] = []
    for raw in reader:
        rows.append({(k or "").strip(): (v or "").strip() for k, v in raw.items()})
    return rows


def _columns_from_csv(csv_bytes: bytes) -> list[str]:
    text = csv_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return []
    return [h.strip() for h in header if h and h.strip()]


def _suggest_from_synonyms(columns: list[str]) -> dict[str, str | None]:
    """Rules-based fuzzy match. First column that wins each target field claims it."""
    suggestion: dict[str, str | None] = dict.fromkeys(columns)
    claimed: set[str] = set()
    normalized_columns = {c: _normalize(c) for c in columns}

    # Two passes: exact match first, then contains match — stronger signals win.
    for passes in ("exact", "contains"):
        for target, synonyms in _SYNONYMS.items():
            if target in claimed:
                continue
            for col, ncol in normalized_columns.items():
                if suggestion.get(col):
                    continue
                match = False
                if passes == "exact":
                    match = ncol in synonyms
                else:
                    for s in synonyms:
                        if s == ncol or s in ncol or ncol in s:
                            match = True
                            break
                if match:
                    suggestion[col] = target
                    claimed.add(target)
                    break
    return suggestion


def _parse_decimal(raw: str) -> Decimal:
    """Accept "1,234.50", "1234.50 EGP", "1٬234٫50" and normal ints."""
    if not raw:
        return Decimal("0")
    cleaned = raw.replace("٬", "").replace("٫", ".").replace(",", "")
    cleaned = re.sub(r"[^\d.\-]", "", cleaned)
    try:
        return Decimal(cleaned or "0")
    except InvalidOperation:
        return Decimal("0")


_PAYMENT_ALIASES = {
    "paid": PaymentStatus.PAID,
    "yes": PaymentStatus.PAID,
    "true": PaymentStatus.PAID,
    "y": PaymentStatus.PAID,
    "مدفوع": PaymentStatus.PAID,
    "pending": PaymentStatus.PENDING,
    "no": PaymentStatus.PENDING,
    "false": PaymentStatus.PENDING,
    "n": PaymentStatus.PENDING,
    "غير مدفوع": PaymentStatus.PENDING,
    "failed": PaymentStatus.FAILED,
    "refunded": PaymentStatus.REFUNDED,
}


_STATUS_ALIASES = {
    "pending": OrderStatus.PENDING,
    "new": OrderStatus.PENDING,
    "confirmed": OrderStatus.CONFIRMED,
    "processing": OrderStatus.PROCESSING,
    "shipped": OrderStatus.SHIPPED,
    "delivered": OrderStatus.DELIVERED,
    "completed": OrderStatus.DELIVERED,
    "cancelled": OrderStatus.CANCELLED,
    "canceled": OrderStatus.CANCELLED,
    "refunded": OrderStatus.REFUNDED,
    "قيد الانتظار": OrderStatus.PENDING,
    "مؤكد": OrderStatus.CONFIRMED,
    "قيد التنفيذ": OrderStatus.PROCESSING,
    "تم الشحن": OrderStatus.SHIPPED,
    "تم التوصيل": OrderStatus.DELIVERED,
    "ملغي": OrderStatus.CANCELLED,
}


def _parse_payment_status(raw: str) -> PaymentStatus:
    return _PAYMENT_ALIASES.get(raw.strip().lower(), PaymentStatus.PENDING)


def _parse_status(raw: str) -> OrderStatus:
    return _STATUS_ALIASES.get(raw.strip().lower(), OrderStatus.PENDING)


def _split_name(full: str) -> tuple[str, str]:
    parts = full.strip().split(None, 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return full.strip() or "Imported", ""


def _placeholder_email(phone: str, store_slug: str) -> str:
    digits = re.sub(r"\D", "", phone) or "unknown"
    return f"import-{digits}@{store_slug}.placeholder"


# ── Service ──────────────────────────────────────────────────────────────


class OrderImportService:
    """CSV → orders with synonym-based column mapping."""

    def __init__(
        self,
        order_repo: OrderRepository,
        customer_repo: CustomerRepository,
        store_repo: StoreRepository,
    ) -> None:
        self.order_repo = order_repo
        self.customer_repo = customer_repo
        self.store_repo = store_repo

    # ── Step 1: suggest mapping ──────────────────────────────────────────

    def suggest(
        self, csv_bytes: bytes, store_settings: dict | None
    ) -> MappingSuggestion:
        columns = _columns_from_csv(csv_bytes)
        rows = _read_csv(csv_bytes)[:MAX_SUGGEST_PREVIEW_ROWS]

        saved = (store_settings or {}).get(SETTINGS_MAPPING_KEY) or {}
        saved_mapping = saved if isinstance(saved, dict) else {}

        suggestion: dict[str, str | None] = {}
        # Start with rules-based suggestion, then overlay anything the merchant
        # previously confirmed — saved values are the stronger signal.
        rules = _suggest_from_synonyms(columns)
        for col in columns:
            suggestion[col] = rules.get(col)
            if col in saved_mapping and saved_mapping[col] in TARGET_FIELDS:
                suggestion[col] = saved_mapping[col]

        return MappingSuggestion(
            columns=columns,
            sample_rows=rows,
            suggested_mapping=suggestion,
        )

    # ── Step 2: execute import ───────────────────────────────────────────

    async def import_rows(
        self,
        csv_bytes: bytes,
        mapping: dict[str, str],
        store_id: UUID,
    ) -> ImportResult:
        store = await self.store_repo.get_by_id(store_id)
        if store is None:
            raise ValueError("store_not_found")

        rows = _read_csv(csv_bytes)
        if len(rows) > MAX_IMPORT_ROWS:
            raise ValueError(f"too_many_rows (max {MAX_IMPORT_ROWS})")

        # Reverse the mapping: target field → column name
        field_to_col: dict[str, str] = {
            tgt: col for col, tgt in mapping.items() if tgt in TARGET_FIELDS
        }

        # Required fields — without these, a row can't become an order
        for required in (
            "customer_name",
            "customer_phone",
            "shipping_address",
            "shipping_city",
            "total",
        ):
            if required not in field_to_col:
                raise ValueError(f"missing_required_field:{required}")

        errors: list[ImportRowError] = []
        created = 0

        for idx, row in enumerate(rows, start=2):  # row 1 = header
            try:
                await self._import_row(
                    row=row,
                    field_to_col=field_to_col,
                    store=store,
                )
                created += 1
            except _RowSkipped as exc:
                errors.append(ImportRowError(row=idx, reason=str(exc)))
            except Exception as exc:  # noqa: BLE001 — never fail the whole batch
                logger.exception("order_import_row_failed row=%d", idx)
                errors.append(ImportRowError(row=idx, reason=f"unexpected: {exc}"))

        return ImportResult(
            total_rows=len(rows),
            created=created,
            skipped=len(errors),
            errors=errors,
        )

    async def _import_row(
        self,
        row: dict[str, str],
        field_to_col: dict[str, str],
        store: Any,
    ) -> None:
        def val(field_name: str) -> str:
            col = field_to_col.get(field_name)
            return (row.get(col, "") if col else "").strip()

        # Required
        customer_full_name = val("customer_name")
        phone = val("customer_phone")
        address = val("shipping_address")
        city = val("shipping_city")
        total_raw = val("total")

        if not (customer_full_name and phone and address and city and total_raw):
            raise _RowSkipped("missing_required_value")

        total_decimal = _parse_decimal(total_raw)
        if total_decimal < 0:
            raise _RowSkipped("invalid_total")
        total_cents = int(total_decimal * 100)

        # Optional
        email_raw = val("customer_email") or _placeholder_email(phone, store.slug)
        try:
            email_vo = Email(value=email_raw)
        except Exception:
            email_vo = Email(value=_placeholder_email(phone, store.slug))

        external_id = val("external_order_id") or None
        notes = val("notes") or None
        payment_status = _parse_payment_status(val("payment_status"))
        order_status = _parse_status(val("status"))

        # De-dupe by (store_id, external_order_id) if merchant supplied one
        if external_id:
            already = await self.order_repo.exists_by_external_id(store.id, external_id)
            if already:
                raise _RowSkipped(f"duplicate:{external_id}")

        # Customer lookup-or-create (keyed on placeholder email when raw email missing)
        customer = await self.customer_repo.get_by_email(store.id, email_vo)
        if customer is None:
            first, last = _split_name(customer_full_name)
            customer = Customer(
                store_id=store.id,
                email=email_vo,
                first_name=first,
                last_name=last or "—",
                phone=phone or None,
                is_verified=False,
                metadata={"source": "import"},
            )
            customer = await self.customer_repo.create(
                customer,
                tenant_id=store.tenant_id,
            )

        first, last = _split_name(customer_full_name)
        shipping = OrderShippingAddress(
            first_name=first,
            last_name=last or "—",
            address_line1=address,
            city=city,
            country="Egypt",
            phone=phone or None,
        )

        # Synthetic single line item carrying the full total.
        line_item = OrderLineItem(
            product_id=uuid4(),  # placeholder — import doesn't link to catalog
            product_name="Imported order",
            quantity=1,
            unit_price=total_cents,
            total_price=total_cents,
        )

        order_number = await self.order_repo.get_next_order_number(store.id)

        metadata: dict[str, Any] = {"source": "import"}
        if external_id:
            metadata["external_order_id"] = external_id

        fulfillment = (
            FulfillmentStatus.FULFILLED
            if order_status in (OrderStatus.DELIVERED, OrderStatus.SHIPPED)
            else FulfillmentStatus.UNFULFILLED
        )

        order = Order(
            store_id=store.id,
            tenant_id=store.tenant_id,
            customer_id=customer.id,
            order_number=order_number,
            line_items=[line_item],
            shipping_address=shipping,
            status=order_status,
            payment_status=payment_status,
            fulfillment_status=fulfillment,
            subtotal=total_cents,
            total=total_cents,
            currency=store.default_currency.value
            if hasattr(store.default_currency, "value")
            else "EGP",
            customer_notes=notes,
            metadata=metadata,
        )
        await self.order_repo.create(order)


class _RowSkipped(Exception):
    """Signals a row was skipped with a user-facing reason."""
