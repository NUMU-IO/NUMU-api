"""Merchant-facing customer import from CSV.

Two-step flow, mirroring ``order_import_service``:
1. ``suggest(csv_bytes, store_settings)`` — parses headers + first rows,
   returns a proposed column→field mapping using a bilingual (EN + AR)
   synonyms table, overlaid with any mapping the merchant previously saved.
2. ``import_rows(csv_bytes, mapping, store_id)`` — creates customers, one
   per row, deduped by email within the store. Partial-success: bad rows
   are skipped with a reason, good rows persist.

Rows without an email get a deterministic placeholder derived from the
phone number (same convention as order import), so re-uploading the same
sheet dedupes phone-only customers too.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from src.core.entities.customer import Customer
from src.core.value_objects.email import Email
from src.core.value_objects.phone import PhoneNumber
from src.infrastructure.repositories import CustomerRepository, StoreRepository

logger = logging.getLogger(__name__)

# ── Canonical field set ───────────────────────────────────────────────────

TARGET_FIELDS: tuple[str, ...] = (
    "name",
    "first_name",
    "last_name",
    "email",
    "phone",
    "accepts_marketing",
    "notes",
    "tags",
)

# Synonyms table — normalized (lowercase, stripped, collapsed whitespace).
# ORDER MATTERS for the "contains" pass in _suggest_from_synonyms: specific
# targets (email, phone, first/last name) must claim their columns before the
# generic "name" target, whose bare "customer"/"client"/"العميل" synonyms
# would otherwise steal headers like "Customer Email" / "Customer Phone".
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "first_name": (
        "first name",
        "firstname",
        "given name",
        "الاسم الأول",
        "الاسم الاول",
    ),
    "last_name": (
        "last name",
        "lastname",
        "surname",
        "family name",
        "اسم العائلة",
        "الاسم الأخير",
        "اللقب",
    ),
    "email": (
        "email",
        "e-mail",
        "mail",
        "email address",
        "البريد",
        "الإيميل",
        "البريد الإلكتروني",
        "إيميل",
    ),
    "phone": (
        "phone",
        "mobile",
        "tel",
        "telephone",
        "phone number",
        "mobile number",
        "contact",
        "whatsapp",
        "رقم الموبايل",
        "الموبايل",
        "الهاتف",
        "رقم الهاتف",
        "رقم",
        "موبايل",
        "هاتف",
        "واتساب",
    ),
    "accepts_marketing": (
        "accepts marketing",
        "marketing",
        "newsletter",
        "subscribed",
        "opt in",
        "opt-in",
        "يقبل التسويق",
        "التسويق",
        "النشرة البريدية",
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
    "tags": (
        "tags",
        "tag",
        "labels",
        "segments",
        "الوسوم",
        "تصنيفات",
        "تصنيف",
    ),
    # Generic name synonyms LAST — see ordering note above.
    "name": (
        "name",
        "customer",
        "customer name",
        "full name",
        "client",
        "الاسم",
        "اسم العميل",
        "العميل",
        "اسم",
        "الاسم الكامل",
    ),
}

SETTINGS_MAPPING_KEY = "customer_import_mapping"

MAX_SUGGEST_PREVIEW_ROWS = 5
MAX_IMPORT_ROWS = 5000

_TRUTHY = {"yes", "true", "1", "y", "subscribed", "نعم", "ايوه", "أيوه", "مشترك"}


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


def _split_name(full: str) -> tuple[str, str]:
    parts = full.strip().split(None, 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return full.strip(), ""


def _placeholder_email(phone: str, store_slug: str) -> str:
    """Deterministic placeholder for phone-only rows.

    MUST stay byte-identical to order_import_service._placeholder_email —
    the two importers dedupe against each other through this format.
    The slug is sanitized so a legacy/unicode slug can't produce a string
    the Email VO rejects (which would fail every phone-only row).
    """
    digits = re.sub(r"\D", "", phone) or "unknown"
    domain = re.sub(r"[^a-z0-9-]", "", store_slug.lower()) or "store"
    return f"import-{digits}@{domain}.placeholder"


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in _TRUTHY


def _parse_tags(raw: str) -> list[str]:
    # Same normalization as MerchantCreateCustomerRequest._normalize_tags
    # (strip, lowercase, dedupe, 50-char cap) so imported and hand-typed
    # tags can never diverge for segmentation.
    parts = re.split(r"[,;،]", raw)
    seen: list[str] = []
    for part in parts:
        tag = part.strip().lower()[:50]
        if tag and tag not in seen:
            seen.append(tag)
    return seen


# ── Service ──────────────────────────────────────────────────────────────


class CustomerImportService:
    """CSV → customers with synonym-based column mapping."""

    def __init__(
        self,
        customer_repo: CustomerRepository,
        store_repo: StoreRepository,
    ) -> None:
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

        # Merchant-confirmed mappings claim their targets first, then the
        # rules-based matcher fills the remaining columns. Claiming per
        # target guarantees no two columns are ever suggested for the same
        # field — execute's mapping reversal would silently drop one.
        suggestion: dict[str, str | None] = dict.fromkeys(columns)
        claimed: set[str] = set()
        for col in columns:
            tgt = saved_mapping.get(col)
            if tgt in TARGET_FIELDS and tgt not in claimed:
                suggestion[col] = tgt
                claimed.add(tgt)

        rules = _suggest_from_synonyms(columns)
        for col in columns:
            if suggestion[col]:
                continue
            tgt = rules.get(col)
            if tgt and tgt not in claimed:
                suggestion[col] = tgt
                claimed.add(tgt)

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
        if store.tenant_id is None:
            # Repo create() would raise the same per row, drowning the
            # merchant in N opaque "unexpected" errors — fail fast instead.
            raise ValueError("store_missing_tenant")

        rows = _read_csv(csv_bytes)
        if len(rows) > MAX_IMPORT_ROWS:
            raise ValueError(f"too_many_rows (max {MAX_IMPORT_ROWS})")

        # Reverse the mapping: target field → column name
        field_to_col: dict[str, str] = {
            tgt: col for col, tgt in mapping.items() if tgt in TARGET_FIELDS
        }

        # A row needs a name (full or first) and a way to reach the customer.
        if "name" not in field_to_col and "first_name" not in field_to_col:
            raise ValueError("missing_required_field:name")
        if "email" not in field_to_col and "phone" not in field_to_col:
            raise ValueError("missing_required_field:email_or_phone")

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
                logger.exception("customer_import_row_failed row=%d", idx)
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
        store: Any,  # Store entity, duck-typed like order import
    ) -> None:
        def val(field_name: str) -> str:
            col = field_to_col.get(field_name)
            return (row.get(col, "") if col else "").strip()

        # Name — prefer explicit first/last columns, else split the full name.
        first = val("first_name")
        last = val("last_name")
        if not first:
            first, split_last = _split_name(val("name"))
            last = last or split_last
        if not first:
            raise _RowSkipped("missing_name")

        phone = val("phone")
        email_raw = val("email")
        if not email_raw and not phone:
            raise _RowSkipped("missing_contact")

        # Email — fall back to a phone-derived placeholder so phone-only
        # sheets still import and dedupe on re-upload.
        try:
            email_vo = Email(value=email_raw) if email_raw else None
        except Exception:
            email_vo = None
        if email_vo is None:
            if not phone:
                raise _RowSkipped("invalid_email")
            # Phone-only row: the phone IS the identity — dedupe against
            # customers who already exist with that number under a real
            # email (e.g. created by an earlier row or by order import).
            e164 = PhoneNumber(value=phone).value
            if await self.customer_repo.get_by_phone(store.id, e164):
                raise _RowSkipped(f"duplicate:{phone}")
            email_vo = Email(value=_placeholder_email(phone, store.slug))

        if await self.customer_repo.email_exists(store.id, email_vo):
            raise _RowSkipped(f"duplicate:{email_vo}")

        notes = val("notes") or None
        tags = _parse_tags(val("tags"))
        accepts_marketing = _parse_bool(val("accepts_marketing"))

        customer = Customer(
            store_id=store.id,
            email=email_vo,
            first_name=first,
            last_name=last or "—",
            phone=phone or None,
            accepts_marketing=accepts_marketing,
            is_verified=False,
            notes=notes,
            tags=tags,
            metadata={"source": "import"},
        )
        await self.customer_repo.create(customer, tenant_id=store.tenant_id)


class _RowSkipped(Exception):
    """Signals a row was skipped with a user-facing reason."""
