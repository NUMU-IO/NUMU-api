"""Tests for the manual-carrier waybill (بوليصة).

This is a physical document a courier reads and acts on. The failures
that matter are not exceptions — they are a label that renders fine and
tells the courier the wrong thing: collect nothing on a COD parcel, or a
phone number whose digits the bidi algorithm reordered.

The PDF path needs WeasyPrint (cairo/pango), which local Windows lacks,
so these assert on the rendered HTML. That is where every one of these
mistakes actually lives.
"""

import pytest

from src.infrastructure.external_services.waybill.generator import (
    LABEL_SIZE,
    MAX_ITEMS,
    WaybillRenderError,
    build_context,
    generate_waybill_batch_pdf,
    render_html,
)

ARABIC_ADDRESS = "١٢ شارع جامعة الدول العربية، المهندسين"
ARABIC_NAME = "سارة أحمد"


def _ctx(**overrides):
    base = {
        "tracking_number": "NM7NA2ZQKG2C",
        "store_name": "Vionne",
        "recipient_name": ARABIC_NAME,
        "address_line": ARABIC_ADDRESS,
        "cod_amount_cents": 65000,
        "recipient_phone": "+201001234567",
        "order_number": "VN-1042",
    }
    base.update(overrides)
    return build_context(**base)


def _body(html: str) -> str:
    """Everything after the stylesheet.

    Class names appear in the CSS whether or not they are used, so
    asserting on the whole document silently passes.
    """
    return html.split("</style>", 1)[1]


class TestCodIsUnambiguous:
    """The most expensive mistake a manual shipment can make."""

    def test_cod_parcel_shows_the_amount_to_collect(self):
        body = _body(render_html(_ctx(cod_amount_cents=65000)))
        assert "650.00 EGP" in body
        assert 'class="cod"' in body
        assert "cod-none" not in body

    def test_prepaid_parcel_says_paid_not_collect_zero(self):
        """A courier shown "0.00 EGP" asks the customer for money."""
        body = _body(render_html(_ctx(cod_amount_cents=0)))
        assert "cod-none" in body
        assert "0.00 EGP" not in body

    def test_missing_cod_is_treated_as_prepaid(self):
        assert (
            build_context(
                tracking_number="NM1",
                store_name="S",
                recipient_name="R",
                address_line="A",
            )["is_cod"]
            is False
        )

    def test_amount_is_grouped_and_two_decimals(self):
        assert (
            build_context(
                tracking_number="NM1",
                store_name="S",
                recipient_name="R",
                address_line="A",
                cod_amount_cents=123456789,
            )["cod_display"]
            == "1,234,567.89 EGP"
        )

    def test_currency_is_respected(self):
        assert (
            "SAR"
            in build_context(
                tracking_number="NM1",
                store_name="S",
                recipient_name="R",
                address_line="A",
                cod_amount_cents=1000,
                currency="SAR",
            )["cod_display"]
        )

    def test_amount_uses_latin_digits(self):
        """Not Arabic-Indic: this gets read aloud and copied onto a
        courier's own paperwork, which uses Latin digits."""
        display = build_context(
            tracking_number="NM1",
            store_name="S",
            recipient_name="R",
            address_line="A",
            cod_amount_cents=65000,
        )["cod_display"]
        assert "650" in display
        assert not any("٠" <= ch <= "٩" for ch in display)


class TestDirectionality:
    """Arabic page, but identifiers must not be reordered."""

    def test_page_is_rtl(self):
        assert 'dir="rtl"' in render_html(_ctx())

    def test_arabic_content_survives(self):
        html = render_html(_ctx())
        assert ARABIC_NAME in html
        assert ARABIC_ADDRESS in html

    @pytest.mark.parametrize(
        "css_class",
        ["to-phone ltr", "tracking-number ltr", "cod-amount"],
    )
    def test_identifiers_are_ltr_isolated(self, css_class):
        """A phone number reordered by bidi is worse than useless on a
        label a courier has to dial."""
        assert css_class in _body(render_html(_ctx()))

    def test_stylesheet_isolates_rather_than_only_aligning(self):
        """`direction` alone still lets neighbouring text reorder it;
        `unicode-bidi: isolate` is what actually pins the run."""
        html = render_html(_ctx())
        assert "unicode-bidi: isolate" in html


class TestLabelContents:
    def test_carries_what_a_courier_needs(self):
        body = _body(render_html(_ctx(governorate="الجيزة", courier_name="Egypt Post")))
        for required in ("NM7NA2ZQKG2C", "+201001234567", "VN-1042", "Egypt Post"):
            assert required in body, required

    def test_qr_is_embedded_not_linked(self):
        """A print job must not depend on fetching anything."""
        html = render_html(_ctx())
        assert "data:image/png;base64," in html
        assert "http://" not in html.split("</style>", 1)[1]

    def test_qr_is_derived_from_the_tracking_number(self):
        """Decoding would need a native zbar build, which isn't worth a
        dependency here. The real risk is the QR encoding the *wrong*
        field — an order number, or a constant — so this pins that it is
        a deterministic function of the tracking number and nothing else.
        """
        first = _ctx(tracking_number="NMAAAAAAAAAA")["qr_data_uri"]
        again = _ctx(tracking_number="NMAAAAAAAAAA")["qr_data_uri"]
        other = _ctx(tracking_number="NMBBBBBBBBBB")["qr_data_uri"]

        assert first == again, "same parcel must produce the same QR"
        assert first != other, "different parcels must not share a QR"

        # Changing anything else must not change the code.
        moved = _ctx(tracking_number="NMAAAAAAAAAA", order_number="DIFFERENT")
        assert moved["qr_data_uri"] == first

    def test_long_item_lists_are_truncated_with_a_count(self):
        """A label has finite room; silently dropping items is worse than
        saying how many were dropped."""
        ctx = _ctx(items=[{"name": f"item{i}"} for i in range(10)])
        assert len(ctx["items"]) == MAX_ITEMS
        assert ctx["extra_items"] == 10 - MAX_ITEMS

    def test_short_item_lists_show_no_overflow_note(self):
        assert _ctx(items=[{"name": "one"}])["extra_items"] == 0

    def test_optional_fields_are_omitted_cleanly(self):
        """A merchant with no logo, order number or notes still gets a
        valid label, not one with holes labelled None."""
        html = render_html(
            build_context(
                tracking_number="NM1",
                store_name="S",
                recipient_name="R",
                address_line="A",
            )
        )
        assert "None" not in _body(html)


class TestSafety:
    def test_page_size_comes_from_the_constant(self):
        assert LABEL_SIZE in render_html(_ctx())

    def test_customer_content_is_escaped(self):
        """Addresses and notes are user-supplied and go through a renderer."""
        html = render_html(_ctx(notes="<script>alert(1)</script>"))
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_batch_refuses_an_empty_run(self):
        with pytest.raises(WaybillRenderError):
            generate_waybill_batch_pdf([])
