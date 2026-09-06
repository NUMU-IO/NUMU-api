"""CSV round-trip with a manual courier.

A Tier 3 courier has no API, so the handover is a file: NUMU exports the
day's manifest in the courier's own column layout, and the courier's
status sheet comes back the same way.

**Encoding is the whole problem.** These files come out of Excel on
Windows machines in Egypt, which means Arabic arrives as **Windows-1256**
or **UTF-8 with a BOM** far more often than plain UTF-8. Reading one as
UTF-8 either raises or silently produces mojibake, and mojibake in a
customer's name is a delivery that fails.

The asymmetry is deliberate and easy to "fix" wrongly:

* **Import** sniffs the encoding.
* **Export** writes UTF-8 **with** a BOM, because Excel needs the BOM to
  read Arabic — the opposite of the project rule for source files, which
  must never have one.

Column names are per-courier, because every courier's sheet is different
and a merchant should map them once rather than re-type them every day.

See ``docs/Plans/Shipping/SHIPPING-UNIFIED-LAYER.md`` § P2.3.
"""

import csv
import io
from dataclasses import dataclass, field
from typing import Any

from src.core.entities.shipment import ShipmentStatus

#: Excel needs a BOM to recognise UTF-8 Arabic. Source files must never
#: have one; exports must. Don't "fix" this to match.
EXPORT_ENCODING = "utf-8-sig"

#: The manifest NUMU hands the courier.
EXPORT_COLUMNS = (
    "tracking_number",
    "order_number",
    "recipient_name",
    "recipient_phone",
    "address",
    "governorate",
    "cod_amount",
    "currency",
    "notes",
)

#: Header spellings seen on real courier sheets, mapped to our fields.
#: A merchant can override these per courier profile.
DEFAULT_COLUMN_ALIASES: dict[str, str] = {
    "tracking_number": "tracking_number",
    "tracking": "tracking_number",
    "awb": "tracking_number",
    "waybill": "tracking_number",
    "barcode": "tracking_number",
    "رقم الشحنة": "tracking_number",
    "رقم البوليصة": "tracking_number",
    "status": "status",
    "state": "status",
    "الحالة": "status",
    "notes": "note",
    "note": "note",
    "reason": "note",
    "ملاحظات": "note",
    "cod_amount": "cod_amount",
    "cod": "cod_amount",
    "collected": "cod_amount",
    "المبلغ": "cod_amount",
}

#: Status words couriers actually write, in both languages.
DEFAULT_STATUS_ALIASES: dict[str, ShipmentStatus] = {
    "delivered": ShipmentStatus.DELIVERED,
    "done": ShipmentStatus.DELIVERED,
    "success": ShipmentStatus.DELIVERED,
    "تم التسليم": ShipmentStatus.DELIVERED,
    "تم": ShipmentStatus.DELIVERED,
    "out_for_delivery": ShipmentStatus.OUT_FOR_DELIVERY,
    "out for delivery": ShipmentStatus.OUT_FOR_DELIVERY,
    "جاري التوصيل": ShipmentStatus.OUT_FOR_DELIVERY,
    "in_transit": ShipmentStatus.IN_TRANSIT,
    "in transit": ShipmentStatus.IN_TRANSIT,
    "في الطريق": ShipmentStatus.IN_TRANSIT,
    "picked_up": ShipmentStatus.PICKED_UP,
    "picked up": ShipmentStatus.PICKED_UP,
    "تم الاستلام": ShipmentStatus.PICKED_UP,
    "returned": ShipmentStatus.RETURNED,
    "return": ShipmentStatus.RETURNED,
    "مرتجع": ShipmentStatus.RETURNED,
    "failed": ShipmentStatus.FAILED,
    "failure": ShipmentStatus.FAILED,
    "فشل": ShipmentStatus.FAILED,
    "لم يتم التسليم": ShipmentStatus.FAILED,
    "cancelled": ShipmentStatus.CANCELLED,
    "canceled": ShipmentStatus.CANCELLED,
    "ملغي": ShipmentStatus.CANCELLED,
}


class CsvFormatError(ValueError):
    """Raised when a file cannot be read as a status sheet at all."""


@dataclass
class ImportRow:
    """One parsed line, whether or not it can be applied."""

    line: int
    tracking_number: str | None = None
    raw_status: str = ""
    status: ShipmentStatus | None = None
    note: str = ""
    cod_amount: float | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.error is None
            and bool(self.tracking_number)
            and self.status is not None
        )


@dataclass
class ImportPreview:
    """What an import *would* do. Always produced before anything is applied."""

    rows: list[ImportRow] = field(default_factory=list)
    encoding: str = ""

    @property
    def applicable(self) -> list[ImportRow]:
        return [r for r in self.rows if r.ok]

    @property
    def rejected(self) -> list[ImportRow]:
        return [r for r in self.rows if not r.ok]

    def as_dict(self) -> dict[str, Any]:
        return {
            "encoding": self.encoding,
            "total": len(self.rows),
            "applicable": len(self.applicable),
            "rejected": len(self.rejected),
            "rows": [
                {
                    "line": r.line,
                    "tracking_number": r.tracking_number,
                    "raw_status": r.raw_status,
                    "status": r.status.value if r.status else None,
                    "note": r.note,
                    "cod_amount": r.cod_amount,
                    "error": r.error,
                }
                for r in self.rows
            ],
        }


def decode_csv(data: bytes) -> tuple[str, str]:
    """Decode a courier's sheet, returning (text, encoding used).

    Tries UTF-8 with and without a BOM, then Windows-1256. cp1256 is last
    on purpose: it decodes nearly any byte sequence without complaining,
    so trying it first would silently mojibake a genuine UTF-8 file.
    """
    if not data:
        raise CsvFormatError("The file is empty.")

    # The BOM is checked explicitly rather than by codec order: the
    # ``utf-8-sig`` codec also decodes plain UTF-8 quite happily, so
    # trying it first would report every UTF-8 file as BOM'd. The
    # reported encoding is shown to the merchant, so it should be true.
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"

    for encoding in ("utf-8", "cp1256"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue

    raise CsvFormatError(
        "Could not read this file. Save it from Excel as CSV UTF-8 and try again."
    )


def _normalise_header(name: str) -> str:
    return (name or "").strip().lstrip("﻿").lower()


def _resolve_columns(
    fieldnames: list[str], aliases: dict[str, str] | None
) -> dict[str, str]:
    """Map this sheet's headers onto our fields."""
    lookup = {
        **DEFAULT_COLUMN_ALIASES,
        **{_normalise_header(k): v for k, v in (aliases or {}).items()},
    }
    resolved: dict[str, str] = {}
    for raw in fieldnames or []:
        field_name = lookup.get(_normalise_header(raw))
        if field_name and field_name not in resolved:
            resolved[field_name] = raw
    return resolved


def resolve_status(
    raw: str, aliases: dict[str, ShipmentStatus] | None = None
) -> ShipmentStatus | None:
    """Courier's word → NUMU status, or None if we don't recognise it.

    Returns None rather than guessing. Coercing an unknown word would
    move a parcel on a courier's typo.
    """
    if not raw:
        return None
    key = " ".join(raw.strip().lower().split())
    table = {**DEFAULT_STATUS_ALIASES, **(aliases or {})}
    if key in table:
        return table[key]
    # A sheet exported from our own manifest round-trips.
    try:
        return ShipmentStatus(key)
    except ValueError:
        return None


def _parse_money(raw: str) -> float | None:
    if not raw:
        return None
    cleaned = raw.strip().replace(",", "").replace("EGP", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_status_sheet(
    data: bytes,
    *,
    column_aliases: dict[str, str] | None = None,
    status_aliases: dict[str, ShipmentStatus] | None = None,
) -> ImportPreview:
    """Parse a courier's status sheet into a preview.

    **Never applies anything** — the caller decides. Every row comes back,
    including the ones that failed, with a reason. A silent row count is
    how a merchant loses ten parcels without noticing.
    """
    text, encoding = decode_csv(data)
    reader = csv.DictReader(io.StringIO(text))
    columns = _resolve_columns(reader.fieldnames or [], column_aliases)

    if "tracking_number" not in columns:
        raise CsvFormatError(
            "No tracking-number column found. Expected one of: "
            + ", ".join(
                sorted({
                    k
                    for k, v in DEFAULT_COLUMN_ALIASES.items()
                    if v == "tracking_number"
                })
            )
        )

    preview = ImportPreview(encoding=encoding)
    seen: set[str] = set()

    for offset, raw_row in enumerate(reader, start=2):  # line 1 is the header
        tracking = (raw_row.get(columns["tracking_number"]) or "").strip()
        row = ImportRow(line=offset, tracking_number=tracking or None)

        if not tracking:
            row.error = "No tracking number on this line."
            preview.rows.append(row)
            continue

        if tracking in seen:
            # Couriers re-send yesterday's rows. Applying a duplicate
            # would append a second identical history entry.
            row.error = "Duplicate of an earlier line in this file."
            preview.rows.append(row)
            continue
        seen.add(tracking)

        if "status" in columns:
            row.raw_status = (raw_row.get(columns["status"]) or "").strip()
            row.status = resolve_status(row.raw_status, status_aliases)
            if row.raw_status and row.status is None:
                row.error = f"Unrecognised status '{row.raw_status}'."
        else:
            row.error = "No status column found."

        if "note" in columns:
            row.note = (raw_row.get(columns["note"]) or "").strip()
        if "cod_amount" in columns:
            row.cod_amount = _parse_money(raw_row.get(columns["cod_amount"]) or "")

        preview.rows.append(row)

    return preview


def build_manifest_csv(
    shipments: list[dict[str, Any]],
    *,
    columns: tuple[str, ...] = EXPORT_COLUMNS,
    headers: dict[str, str] | None = None,
) -> bytes:
    """The manifest handed to the courier, as bytes ready to download.

    Written UTF-8 **with a BOM** so Excel opens Arabic correctly. The
    project rule against BOMs is about source files; a spreadsheet needs
    the opposite.
    """
    header_labels = headers or {}
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL)
    writer.writerow([header_labels.get(c, c) for c in columns])

    for shipment in shipments:
        writer.writerow([_export_cell(shipment.get(c)) for c in columns])

    return buffer.getvalue().encode(EXPORT_ENCODING)


def _export_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


__all__ = [
    "DEFAULT_COLUMN_ALIASES",
    "DEFAULT_STATUS_ALIASES",
    "EXPORT_COLUMNS",
    "EXPORT_ENCODING",
    "CsvFormatError",
    "ImportPreview",
    "ImportRow",
    "build_manifest_csv",
    "decode_csv",
    "parse_status_sheet",
    "resolve_status",
]
