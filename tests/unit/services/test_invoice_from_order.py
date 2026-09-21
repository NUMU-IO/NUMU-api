"""The order → invoice mapping, and what the printed invoice may claim.

A cash-on-delivery merchant prints this at dispatch and packs it with the
parcel, so two things matter more than anything else on it:

* the grand total must equal what the order charges — i.e. what the courier
  collects. The mapping used to drop the order's discount, so a coupon or an
  automatic offer left the paper asking for MORE than the customer owed;
* it must not claim things that aren't true: "Tax Invoice" and VAT lines for
  a seller with no tax registration, or "certified by the Egyptian Tax
  Authority" for an invoice that was never submitted.

Orders and stores are plain namespaces, as elsewhere in this suite — the
mapping only reads attributes.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.application.services.invoice_from_order import (
    buyer_from_order,
    invoice_from_order,
    seller_from_store,
)
from src.infrastructure.external_services.invoice.pdf_generator import (
    InvoicePDFGenerator,
)


def _store(settings=None, address=None):
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        name="Bon Younes",
        settings=settings or {},
        address=address or {"city": "Cairo", "street": "Tahrir St"},
        contact_phone="+201000000000",
        contact_email=None,
        logo_url=None,
    )


def _line(name, unit_cents, qty=1, variant=None, sku=None):
    return SimpleNamespace(
        product_name=name,
        variant_name=variant,
        sku=sku,
        quantity=qty,
        unit_price=unit_cents,
    )


def _order(*, lines, shipping=0, discount=0, payment_status="pending"):
    subtotal = sum(li.unit_price * li.quantity for li in lines)
    return SimpleNamespace(
        id=uuid4(),
        customer_id=uuid4(),
        order_number="ORD-104233",
        currency="EGP",
        line_items=lines,
        shipping_cost=shipping,
        discount_amount=discount,
        subtotal=subtotal,
        total=subtotal + shipping - discount,
        payment_status=payment_status,
        shipping_address=SimpleNamespace(
            first_name="Sara",
            last_name="Ali",
            address_line1="12 Nile St",
            address_line2="Apt 4",
            city="Giza",
            state="Giza Governorate",
            phone="+201112223334",
        ),
    )


# -------- The money ----------------------------------------------------------


def test_grand_total_equals_the_order_total_with_a_discount():
    # The bug this pins: the discount never reached the invoice, so a COD
    # parcel carried a paper asking for 50.00 more than the courier collects.
    order = _order(
        lines=[_line("Abaya", 30_000), _line("Scarf", 10_000, qty=2)],
        shipping=5_000,
        discount=5_000,
    )
    invoice = invoice_from_order(_store(), order, invoice_number="INV-2026-000001")
    assert invoice.extra_discount == 5_000
    # 300 + 2×100 + 50 shipping − 50 discount
    assert invoice.grand_total == order.total == 50_000


def test_grand_total_equals_the_order_total_without_a_discount():
    order = _order(lines=[_line("Abaya", 30_000)], shipping=4_500)
    invoice = invoice_from_order(_store(), order, invoice_number="INV-2026-000002")
    assert invoice.grand_total == order.total == 34_500


def test_order_number_is_carried_for_matching_the_parcel():
    order = _order(lines=[_line("Abaya", 30_000)])
    invoice = invoice_from_order(_store(), order, invoice_number="INV-2026-000003")
    assert invoice.internal_id == "ORD-104233"
    assert invoice.invoice_number == "INV-2026-000003"


# -------- What's on each line, and who it's for ------------------------------


def test_variant_is_part_of_the_line_description():
    order = _order(lines=[_line("T-shirt", 20_000, variant="Red / L", sku="TS-RL")])
    invoice = invoice_from_order(_store(), order, invoice_number="INV-2026-000004")
    line = invoice.line_items[0]
    assert line.description == "T-shirt — Red / L"
    assert line.internal_code == "TS-RL"


def test_buyer_keeps_the_second_address_line_and_governorate():
    buyer = buyer_from_order(_order(lines=[]))
    assert buyer.name == "Sara Ali"
    assert buyer.street == "12 Nile St, Apt 4"
    assert buyer.governorate == "Giza Governorate"
    assert buyer.phone == "+201112223334"


def test_seller_reads_the_invoice_settings_before_legacy_and_address():
    store = _store(
        settings={
            "invoice": {
                "tax_id": "123-456-789",
                "name_ar": "بون يونس",
                "city": "Nasr City",
            },
            "tax_id": "OLD-LEGACY",
        },
        address={"city": "Cairo", "street": "Tahrir St"},
    )
    seller = seller_from_store(store)
    assert seller.tax_id == "123-456-789"
    assert seller.name_ar == "بون يونس"
    assert seller.city == "Nasr City"
    # Not set in the invoice block — falls through to the store address.
    assert seller.street == "Tahrir St"


def test_seller_falls_back_to_legacy_keys():
    seller = seller_from_store(_store(settings={"tax_id": "999-888-777"}))
    assert seller.tax_id == "999-888-777"


# -------- What the printed document may claim --------------------------------


def _html(
    store,
    order,
    *,
    payment_status="pending",
    eta_uuid="simulated-abc",
    method_key="cod",
    payments=None,
):
    invoice = invoice_from_order(store, order, invoice_number="INV-2026-000009")
    invoice.eta_uuid = eta_uuid
    gen = InvoicePDFGenerator(template_name="invoice_ar.html", language="ar_en")
    return gen._render_template(
        invoice,
        payment={
            "status": payment_status,
            "method": "Cash on Delivery",
            "method_key": method_key,
            "payments": payments or [],
        },
    )


REGISTERED = {"invoice": {"tax_id": "123-456-789"}}


def test_unregistered_seller_gets_a_plain_invoice_with_no_vat_lines():
    html = _html(_store(), _order(lines=[_line("Abaya", 30_000)]))
    # Asserted on the title element itself — the stylesheet carries a
    # "Tax Invoice" comment that is never rendered.
    assert '<span class="ar">فاتورة ضريبية</span>' not in html
    assert '<span class="ar">فاتورة</span>' in html
    assert "VAT 14% (included)" not in html
    assert "Prices include" not in html
    assert "VAT incl." not in html


def test_registered_seller_gets_a_tax_invoice_with_vat():
    html = _html(_store(settings=REGISTERED), _order(lines=[_line("Abaya", 30_000)]))
    assert '<span class="ar">فاتورة ضريبية</span>' in html
    assert '<span class="en">Tax Invoice</span>' in html
    assert "VAT 14% (included)" in html


def test_unpaid_order_shows_the_amount_the_courier_collects():
    order = _order(lines=[_line("Abaya", 30_000)], shipping=5_000, discount=3_000)
    html = _html(_store(), order, payment_status="pending")
    assert "Amount due on delivery" in html
    assert "320.00 EGP" in html  # 300 + 50 − 30


def test_paid_order_shows_no_amount_due():
    html = _html(
        _store(), _order(lines=[_line("Abaya", 30_000)]), payment_status="paid"
    )
    assert "Amount due on delivery" not in html


def test_order_discount_is_itemised_so_the_totals_add_up():
    order = _order(lines=[_line("Abaya", 30_000)], discount=3_000)
    html = _html(_store(), order)
    assert "Order discount" in html
    assert "-30.00 EGP" in html


def test_order_number_is_printed():
    assert "ORD-104233" in _html(_store(), _order(lines=[_line("Abaya", 30_000)]))


@pytest.mark.parametrize(
    "eta_uuid,claims",
    [("simulated-abc123", False), (None, False), ("3F2A9C0E-REAL-ETA-UUID", True)],
)
def test_tax_authority_certification_is_only_claimed_when_true(eta_uuid, claims):
    html = _html(
        _store(settings=REGISTERED),
        _order(lines=[_line("Abaya", 30_000)]),
        eta_uuid=eta_uuid,
    )
    assert ("معتمدة من مصلحة الضرائب المصرية" in html) is claims


def test_code_column_shows_the_sku_not_the_eta_placeholder():
    order = _order(lines=[_line("Abaya", 30_000, sku=None)])
    html = _html(_store(), order)
    assert "EG-0000-0000" not in html


def test_issue_date_is_today_not_the_order_date():
    invoice = invoice_from_order(
        _store(), _order(lines=[_line("Abaya", 30_000)]), invoice_number="INV-X"
    )
    assert invoice.date_issued.date() == datetime.now(UTC).date()


# -------- Paper + direction --------------------------------------------------


def test_prints_on_a5():
    # The invoice-book size, chosen for packing with a COD parcel.
    html = _html(_store(), _order(lines=[_line("Abaya", 30_000)]))
    assert "size: A5;" in html


def test_phone_and_sku_keep_their_own_direction_on_the_rtl_page():
    # Left to the page direction, "+201060082542" printed as "201060082542+"
    # and "S-W-P" as "-S-W P".
    order = _order(lines=[_line("Sidetracked", 18_000, sku="S-W-P")])
    html = _html(_store(), order)
    assert '<span class="ltr">+201112223334</span>' in html
    assert '<span class="ltr">SKU S-W-P</span>' in html


def test_english_product_name_is_isolated_as_ltr():
    order = _order(
        lines=[_line("Sidetracked", 18_000, variant="Type: Paperback, Paper: White")]
    )
    html = _html(_store(), order)
    assert (
        '<span dir="ltr" class="bidi">Sidetracked — Type: Paperback, Paper: White</span>'
        in html
    )


def test_name_is_not_printed_twice_when_there_is_no_translation():
    html = _html(_store(), _order(lines=[_line("Abaya", 30_000)]))
    assert html.count("Sara Ali") == 1


def test_seller_phone_comes_from_the_store_contact_phone():
    store = _store()
    store.contact_phone = "+20223456789"
    store.contact_email = "hello@bonyounes.com"
    seller = seller_from_store(store)
    assert seller.phone == "+20223456789"
    assert seller.email == "hello@bonyounes.com"


def test_no_platform_logo_on_the_merchants_invoice():
    # No store logo: the store's name heads the page, never the NUMU mark.
    html = _html(_store(), _order(lines=[_line("Abaya", 30_000)]))
    assert "numu_logo" not in html
    assert 'class="wordmark"' in html


# -------- Recorded payments ("سجل دفعة") -------------------------------------

DEPOSIT = [
    {
        "amount_cents": 10_000,
        "method": "InstaPay",
        "method_ar": "إنستاباي",
        "date": "2026-09-21",
    }
]


def test_recorded_deposit_is_listed_and_the_balance_is_what_is_left():
    # 300 order, 100 deposit recorded by InstaPay: the parcel's paper must ask
    # the customer for 200, not 300.
    order = _order(lines=[_line("Abaya", 18_000)], shipping=12_000)
    html = _html(_store(), order, payments=DEPOSIT)
    assert "إنستاباي" in html and "-100.00 EGP" in html
    assert "Balance due on delivery" in html
    assert "200.00 EGP" in html
    assert "Partially paid" in html


def test_paid_cod_order_shows_the_collected_rest_and_a_zero_balance():
    # 300 order, 100 deposit recorded, then marked paid when the courier
    # collected: the paper shows both payments and a 0 balance, while the
    # GRAND TOTAL stays the invoice's value.
    order = _order(lines=[_line("Abaya", 18_000)], shipping=12_000)
    html = _html(_store(), order, payment_status="paid", payments=DEPOSIT)
    assert "300.00 EGP" in html  # grand total unchanged
    assert "-100.00 EGP" in html  # recorded deposit
    assert "Collected on delivery" in html and "-200.00 EGP" in html
    assert "0.00 EGP" in html and "Balance due" in html
    assert "Amount due" not in html


def test_paid_order_with_no_recorded_payment_is_shown_paid_in_full():
    order = _order(lines=[_line("Abaya", 30_000)])
    html = _html(_store(), order, payment_status="paid", method_key="paymob")
    assert "-300.00 EGP" in html
    assert "Collected on delivery" not in html
    assert "Balance due" in html


def test_unpaid_invoice_never_shows_a_zero_balance_row():
    html = _html(_store(), _order(lines=[_line("Abaya", 30_000)]))
    assert 'class="settled"' not in html


def test_unpaid_non_cod_order_is_due_but_not_on_delivery():
    order = _order(lines=[_line("Abaya", 30_000)])
    html = _html(_store(), order, method_key="bank_transfer")
    assert "Amount due" in html
    assert "on delivery" not in html


async def test_only_settled_payments_reduce_what_is_due(monkeypatch):
    # A voided payment or an upload still under review must never lower the
    # balance the courier collects.
    from src.api.v1.routes.stores import invoices as invoices_module
    from src.core.entities.instapay import PaymentProofStatus
    from src.infrastructure.repositories import payment_proof_repository

    def proof(status, cents, method="instapay"):
        return SimpleNamespace(
            status=status,
            declared_amount_cents=cents,
            recorded_method=method,
            review_decision_at=datetime(2026, 9, 21, tzinfo=UTC),
            created_at=datetime(2026, 9, 20, tzinfo=UTC),
        )

    async def list_for_order(self, order_id):
        return [
            proof(PaymentProofStatus.APPROVED, 10_000, "vodafone_cash"),
            proof(PaymentProofStatus.AUTO_APPROVED, 5_000),
            proof(PaymentProofStatus.REJECTED, 20_000),
            proof(PaymentProofStatus.AWAITING_REVIEW, 30_000),
        ]

    monkeypatch.setattr(
        payment_proof_repository.PaymentProofRepository,
        "list_for_order",
        list_for_order,
    )
    payments = await invoices_module._settled_payments(
        SimpleNamespace(session=None), uuid4()
    )
    assert [p["amount_cents"] for p in payments] == [10_000, 5_000]
    assert payments[0]["method_ar"] == "فودافون كاش"
    assert payments[0]["date"] == "2026-09-21"


# -------- Invoice list: the order's real payment status ----------------------


async def test_list_reads_each_orders_payment_status_in_one_store_scoped_query():
    from unittest.mock import AsyncMock, MagicMock

    from src.api.v1.routes.stores import invoices as invoices_module
    from src.core.entities.order import PaymentStatus

    paid_id, pending_id = uuid4(), uuid4()
    result = MagicMock()
    result.all.return_value = [(paid_id, PaymentStatus.PAID), (pending_id, "pending")]
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    statuses = await invoices_module._order_payment_statuses(
        SimpleNamespace(session=session), uuid4(), [paid_id, pending_id, paid_id]
    )
    assert statuses == {paid_id: "paid", pending_id: "pending"}
    session.execute.assert_awaited_once()


async def test_list_skips_the_query_when_no_invoice_has_an_order():
    from unittest.mock import AsyncMock

    from src.api.v1.routes.stores import invoices as invoices_module

    session = SimpleNamespace(execute=AsyncMock())
    assert (
        await invoices_module._order_payment_statuses(
            SimpleNamespace(session=session), uuid4(), []
        )
        == {}
    )
    session.execute.assert_not_awaited()
