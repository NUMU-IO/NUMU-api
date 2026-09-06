"""Tests for the manual-carrier CSV round-trip.

Encoding is the substance here. These files come out of Excel on Windows
machines in Egypt, so Arabic arrives as **Windows-1256** or **UTF-8 with
a BOM** far more often than plain UTF-8 — and mojibake in a customer's
name is a delivery that fails.

The export asymmetry is deliberate and looks like a bug to anyone who
knows the project's no-BOM rule: **exports must have a BOM** or Excel
renders Arabic as gibberish. Source files must never have one.
"""

import pytest

from src.application.services.shipment_csv import (
    EXPORT_COLUMNS,
    CsvFormatError,
    build_manifest_csv,
    decode_csv,
    parse_status_sheet,
    resolve_status,
)
from src.core.entities.shipment import ShipmentStatus

ARABIC_NAME = "سارة أحمد"


def _sheet(rows: str, *, encoding: str = "utf-8") -> bytes:
    return f"tracking_number,status,notes\n{rows}".encode(encoding)


class TestEncodingDetection:
    """The failure mode is silent mojibake, not an exception."""

    def test_plain_utf8(self):
        text, encoding = decode_csv(f"name\n{ARABIC_NAME}".encode())
        assert ARABIC_NAME in text
        assert encoding == "utf-8"

    def test_utf8_with_bom_from_excel(self):
        text, encoding = decode_csv(f"name\n{ARABIC_NAME}".encode("utf-8-sig"))
        assert ARABIC_NAME in text
        assert encoding == "utf-8-sig"
        # The BOM must not survive into the first header name.
        assert not text.startswith("﻿")

    def test_windows_1256_from_excel(self):
        """The single most common real-world case."""
        text, encoding = decode_csv(f"name\n{ARABIC_NAME}".encode("cp1256"))
        assert ARABIC_NAME in text
        assert encoding == "cp1256"

    def test_utf8_wins_over_cp1256(self):
        """cp1256 decodes almost any bytes without erroring.

        If it were tried first it would silently mojibake genuine UTF-8 —
        which is exactly how Arabic names get mangled.
        """
        _, encoding = decode_csv(ARABIC_NAME.encode())
        assert encoding != "cp1256"

    def test_empty_file_is_rejected_clearly(self):
        with pytest.raises(CsvFormatError):
            decode_csv(b"")


class TestStatusResolution:
    @pytest.mark.parametrize(
        ("word", "expected"),
        [
            ("delivered", ShipmentStatus.DELIVERED),
            ("Delivered", ShipmentStatus.DELIVERED),
            ("  DONE  ", ShipmentStatus.DELIVERED),
            ("تم التسليم", ShipmentStatus.DELIVERED),
            ("returned", ShipmentStatus.RETURNED),
            ("مرتجع", ShipmentStatus.RETURNED),
            ("out for delivery", ShipmentStatus.OUT_FOR_DELIVERY),
            ("فشل", ShipmentStatus.FAILED),
        ],
    )
    def test_accepts_what_couriers_actually_write(self, word, expected):
        assert resolve_status(word) is expected

    def test_unknown_word_returns_none_rather_than_guessing(self):
        """Coercing an unknown word would move a parcel on a typo."""
        assert resolve_status("delivrd") is None
        assert resolve_status("") is None

    def test_our_own_export_round_trips(self):
        assert resolve_status("in_transit") is ShipmentStatus.IN_TRANSIT

    def test_merchant_aliases_win(self):
        custom = {"وصلت": ShipmentStatus.DELIVERED}
        assert resolve_status("وصلت", custom) is ShipmentStatus.DELIVERED


class TestImportPreview:
    """Nothing is applied until the merchant sees what would happen."""

    def test_parses_applicable_rows(self):
        preview = parse_status_sheet(_sheet("NM1,delivered,\nNM2,returned,damaged\n"))
        assert len(preview.applicable) == 2
        assert preview.applicable[0].status is ShipmentStatus.DELIVERED
        assert preview.applicable[1].note == "damaged"

    def test_reports_rejected_rows_instead_of_dropping_them(self):
        """A silent row count is how a merchant loses ten parcels."""
        preview = parse_status_sheet(
            _sheet("NM1,delivered,\n,delivered,\nNM3,delivrd,\n")
        )
        assert len(preview.applicable) == 1
        assert len(preview.rejected) == 2
        assert all(r.error for r in preview.rejected)

    def test_line_numbers_match_the_spreadsheet(self):
        """A merchant fixing the file needs the row number Excel shows."""
        preview = parse_status_sheet(_sheet("NM1,delivered,\n,delivered,\n"))
        assert preview.rejected[0].line == 3

    def test_duplicate_rows_are_rejected(self):
        """Couriers re-send yesterday's sheet; applying twice would append
        a second identical history entry."""
        preview = parse_status_sheet(_sheet("NM1,delivered,\nNM1,delivered,\n"))
        assert len(preview.applicable) == 1
        assert "Duplicate" in preview.rejected[0].error

    def test_unknown_status_is_rejected_with_the_word_quoted(self):
        preview = parse_status_sheet(_sheet("NM1,teleported,\n"))
        assert "teleported" in preview.rejected[0].error

    def test_arabic_sheet_from_excel_parses(self):
        raw = "رقم الشحنة,الحالة,ملاحظات\nNM1,تم التسليم,اتصل بالعميل\n".encode(
            "cp1256"
        )
        preview = parse_status_sheet(raw)
        assert preview.encoding == "cp1256"
        assert preview.applicable[0].status is ShipmentStatus.DELIVERED
        assert preview.applicable[0].note == "اتصل بالعميل"

    def test_alternative_header_spellings(self):
        raw = b"AWB,State\nNM1,Delivered\n"
        assert parse_status_sheet(raw).applicable[0].tracking_number == "NM1"

    def test_merchant_column_mapping_wins(self):
        raw = b"Parcel Ref,Result\nNM1,delivered\n"
        preview = parse_status_sheet(
            raw,
            column_aliases={"Parcel Ref": "tracking_number", "Result": "status"},
        )
        assert preview.applicable[0].tracking_number == "NM1"

    def test_missing_tracking_column_is_a_clear_error(self):
        with pytest.raises(CsvFormatError, match="tracking-number"):
            parse_status_sheet(b"foo,bar\n1,2\n")

    def test_missing_status_column_rejects_rows_not_the_file(self):
        """The file is readable; it just can't update anything."""
        preview = parse_status_sheet(b"tracking_number\nNM1\n")
        assert preview.applicable == []
        assert "status" in preview.rejected[0].error

    def test_cod_amount_is_parsed_leniently(self):
        preview = parse_status_sheet(
            b'tracking_number,status,cod\nNM1,delivered,"1,250.00 EGP"\n'
        )
        assert preview.applicable[0].cod_amount == 1250.0

    def test_preview_serialises_for_the_hub(self):
        payload = parse_status_sheet(_sheet("NM1,delivered,\n")).as_dict()
        assert payload["total"] == 1
        assert payload["applicable"] == 1
        assert payload["rows"][0]["status"] == "delivered"


class TestManifestExport:
    def test_has_a_bom_so_excel_reads_arabic(self):
        """🔴 Deliberately the opposite of the source-file rule.

        Without the BOM Excel shows Arabic names as gibberish, and the
        merchant hands the courier an unreadable manifest.
        """
        out = build_manifest_csv([{"recipient_name": ARABIC_NAME}])
        assert out.startswith(b"\xef\xbb\xbf")

    def test_arabic_survives_the_round_trip(self):
        out = build_manifest_csv([
            {"tracking_number": "NM1", "recipient_name": ARABIC_NAME}
        ])
        text, encoding = decode_csv(out)
        assert ARABIC_NAME in text
        assert encoding == "utf-8-sig"

    def test_writes_the_expected_columns(self):
        text, _ = decode_csv(build_manifest_csv([]))
        assert text.strip().split(",") == list(EXPORT_COLUMNS)

    def test_missing_fields_become_blanks_not_none(self):
        text, _ = decode_csv(build_manifest_csv([{"tracking_number": "NM1"}]))
        assert "None" not in text

    def test_courier_specific_headers(self):
        """Every courier's sheet is different; a merchant maps once."""
        text, _ = decode_csv(
            build_manifest_csv(
                [{"tracking_number": "NM1"}],
                columns=("tracking_number",),
                headers={"tracking_number": "رقم البوليصة"},
            )
        )
        assert "رقم البوليصة" in text

    def test_commas_in_addresses_are_quoted(self):
        """Egyptian addresses are full of commas."""
        out = build_manifest_csv([{"address": "١٢ شارع, المهندسين"}])
        text, _ = decode_csv(out)
        assert '"١٢ شارع, المهندسين"' in text
        # And it survives being read back as one field.
        assert (
            len(
                parse_status_sheet(
                    b'tracking_number,status,address\nNM1,delivered,"a, b"\n'
                ).applicable
            )
            == 1
        )
