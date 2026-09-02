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
    SHEET_PER_PAGE,
    SHEET_SCALE,
    TEMPLATE_DIR,
    WaybillRenderError,
    build_context,
    generate_waybill_batch_pdf,
    generate_waybill_sheet_pdf,
    render_html,
    render_sheet_html,
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
        `unicode-bidi: isolate` is what actually pins the run.

        Asserted against the stylesheet itself, since it is linked rather
        than inlined — and it is shared by both outputs, so this covers
        the A4 sheet too.
        """
        css = (TEMPLATE_DIR / "label.css").read_text(encoding="utf-8")
        assert "unicode-bidi: isolate" in css


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
        assert len(ctx["line_items"]) == MAX_ITEMS
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
    def test_roll_page_size_comes_from_the_constant(self):
        assert LABEL_SIZE in render_html(_ctx())

    def test_customer_content_is_escaped(self):
        """Addresses and notes are user-supplied and go through a renderer."""
        html = render_html(_ctx(notes="<script>alert(1)</script>"))
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_batch_refuses_an_empty_run(self):
        with pytest.raises(WaybillRenderError):
            generate_waybill_batch_pdf([])


class TestTwoOutputsOneLayout:
    """The roll and the A4 sheet must stay the same label.

    They are separate wrappers around one partial and one stylesheet, so
    the failure to guard against is someone reintroducing a second set of
    markup that slowly drifts.
    """

    def test_roll_is_the_thermal_size(self):
        assert LABEL_SIZE == "100mm 150mm"
        assert LABEL_SIZE in render_html(_ctx())

    def test_roll_is_never_scaled(self):
        """A thermal printer feeds fixed-width stock; scaling walks the
        label off its own roll."""
        assert "transform: scale" not in render_html(_ctx())

    def test_sheet_is_a4_and_scaled_to_fit(self):
        """Four 100x150 labels tile to 200x300; A4 is 297mm tall."""
        html = render_sheet_html([_ctx()])
        assert "A4" in html
        assert f"scale({SHEET_SCALE})" in html
        assert 200 * SHEET_SCALE <= 210  # width fits
        assert 300 * SHEET_SCALE <= 297.0001  # height fits

    def test_both_outputs_share_one_stylesheet(self):
        for html in (render_html(_ctx()), render_sheet_html([_ctx()])):
            assert 'href="label.css"' in html

    def test_sheet_tiles_four_per_page(self):
        html = render_sheet_html([_ctx() for _ in range(SHEET_PER_PAGE + 1)])
        assert html.count('class="grid"') == 2

    def test_incomplete_page_leaves_blanks_not_stretched_labels(self):
        body = _body(render_sheet_html([_ctx(), _ctx()]))
        assert body.count('class="label"') == 2
        assert body.count("cell-empty") == SHEET_PER_PAGE - 2

    def test_sheet_has_cut_guides(self):
        """A merchant with scissors needs to see where to cut."""
        assert "dashed" in render_sheet_html([_ctx()])

    def test_same_label_content_in_both_outputs(self):
        ctx = _ctx()
        roll, sheet = render_html([ctx]), render_sheet_html([ctx])
        for required in ("NM7NA2ZQKG2C", "650.00 EGP", "+201001234567"):
            assert required in roll and required in sheet, required

    def test_merchant_branding_leads_with_numu_underneath(self):
        """The courier needs to know who a parcel returns to, and the
        customer sees it. NUMU's mark sits under, not over."""
        html = render_html(_ctx())
        assert "Vionne" in html
        assert "by-numu" in html

    def test_sheet_refuses_an_empty_run(self):
        with pytest.raises(WaybillRenderError):
            generate_waybill_sheet_pdf([])
