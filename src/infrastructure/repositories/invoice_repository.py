"""Invoice repository implementation."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.invoice import (
    DEFAULT_VAT_RATE,
    BuyerInfo,
    Invoice,
    InvoiceLineItem,
    InvoiceStatus,
    InvoiceType,
    SellerInfo,
    TaxLine,
    TaxType,
)
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.invoice import InvoiceModel


class InvoiceRepository:
    """Invoice repository using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        tid = get_tenant_id()
        if tid:
            return query.where(InvoiceModel.tenant_id == tid)
        return query

    async def create(self, invoice: Invoice) -> Invoice:
        """Persist a new invoice."""
        model = InvoiceModel(
            id=invoice.id,
            store_id=invoice.store_id,
            order_id=invoice.order_id,
            customer_id=invoice.customer_id,
            tenant_id=invoice.tenant_id or invoice.store_id,
            invoice_number=invoice.invoice_number,
            internal_id=invoice.internal_id,
            invoice_type=invoice.invoice_type,
            status=invoice.status,
            date_issued=invoice.date_issued,
            seller=invoice.seller.model_dump(),
            buyer=invoice.buyer.model_dump(),
            currency=invoice.currency,
            exchange_rate=int(invoice.exchange_rate * 100),
            line_items=[self._line_item_to_dict(li) for li in invoice.line_items],
            subtotal=invoice.subtotal,
            total_discount=invoice.total_discount,
            total_taxes=invoice.total_taxes,
            extra_discount=invoice.extra_discount,
            total=invoice.total,
            prices_include_vat=invoice.prices_include_vat,
            vat_rate=invoice.vat_rate,
            vat_amount=invoice.vat_amount,
            net_amount_before_vat=invoice.net_amount_before_vat,
            shipping_fee=invoice.shipping_fee,
            grand_total=invoice.grand_total,
            eta_uuid=invoice.eta_uuid,
            eta_long_id=invoice.eta_long_id,
            eta_submission_id=invoice.eta_submission_id,
            eta_internal_id=invoice.eta_internal_id,
            eta_hash=invoice.eta_hash,
            eta_status_code=invoice.eta_status_code,
            eta_status_message=invoice.eta_status_message,
            qr_code_data=invoice.qr_code_data,
            qr_code_image=invoice.qr_code_image,
            signature=invoice.signature,
            signature_type=invoice.signature_type,
            signature_timestamp=invoice.signature_timestamp,
            pdf_r2_key=invoice.pdf_r2_key,
            pdf_url=invoice.pdf_url,
            related_invoice_id=invoice.related_invoice_id,
            original_invoice_number=invoice.original_invoice_number,
            notes=invoice.notes,
            notes_ar=invoice.notes_ar,
        )
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def get_by_id(self, invoice_id: UUID) -> Invoice | None:
        query = select(InvoiceModel).where(InvoiceModel.id == invoice_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_order_id(self, order_id: UUID) -> Invoice | None:
        query = select(InvoiceModel).where(InvoiceModel.order_id == order_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_by_store(
        self,
        store_id: UUID,
        status_filter: InvoiceStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Invoice], int]:
        query = select(InvoiceModel).where(InvoiceModel.store_id == store_id)
        if status_filter:
            query = query.where(InvoiceModel.status == status_filter)
        query = self._tenant_filter(query)

        count_q = select(func.count()).select_from(query.subquery())
        total = (await self.session.execute(count_q)).scalar() or 0

        query = query.order_by(InvoiceModel.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await self.session.execute(query)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models], total

    async def update(self, invoice: Invoice) -> Invoice:
        query = select(InvoiceModel).where(InvoiceModel.id == invoice.id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if not model:
            raise ValueError(f"Invoice {invoice.id} not found")

        model.status = invoice.status
        model.seller = invoice.seller.model_dump()
        model.buyer = invoice.buyer.model_dump()
        model.line_items = [self._line_item_to_dict(li) for li in invoice.line_items]
        model.subtotal = invoice.subtotal
        model.total_discount = invoice.total_discount
        model.total_taxes = invoice.total_taxes
        model.extra_discount = invoice.extra_discount
        model.total = invoice.total
        model.prices_include_vat = invoice.prices_include_vat
        model.vat_rate = invoice.vat_rate
        model.vat_amount = invoice.vat_amount
        model.net_amount_before_vat = invoice.net_amount_before_vat
        model.shipping_fee = invoice.shipping_fee
        model.grand_total = invoice.grand_total
        model.eta_uuid = invoice.eta_uuid
        model.eta_long_id = invoice.eta_long_id
        model.eta_submission_id = invoice.eta_submission_id
        model.eta_hash = invoice.eta_hash
        model.eta_status_code = invoice.eta_status_code
        model.eta_status_message = invoice.eta_status_message
        model.qr_code_data = invoice.qr_code_data
        model.qr_code_image = invoice.qr_code_image
        model.pdf_r2_key = invoice.pdf_r2_key
        model.pdf_url = invoice.pdf_url
        model.notes = invoice.notes
        model.notes_ar = invoice.notes_ar

        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def delete(self, invoice_id: UUID) -> bool:
        query = select(InvoiceModel).where(InvoiceModel.id == invoice_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if not model:
            return False
        await self.session.delete(model)
        await self.session.flush()
        return True

    async def get_next_invoice_number(self, store_id: UUID) -> str:
        """Generate the next per-store invoice number for the current year.

        Format: ``INV-{year}-{seq:06d}`` where ``seq`` is the highest
        sequence already used for ``store_id`` in the current year, plus
        one. The composite UNIQUE index on ``(store_id, invoice_number)``
        is the source of truth — if two concurrent checkouts on the same
        store both compute the same next number, the second INSERT will
        raise IntegrityError, which the caller is expected to surface
        (and the merchant can retry).
        """
        year = datetime.utcnow().year
        prefix = f"INV-{year}-"
        query = (
            select(func.max(InvoiceModel.invoice_number))
            .where(InvoiceModel.store_id == store_id)
            .where(InvoiceModel.invoice_number.like(f"{prefix}%"))
        )
        result = await self.session.execute(query)
        last_number = result.scalar()
        last_seq = 0
        if last_number:
            try:
                last_seq = int(last_number[len(prefix) :])
            except (ValueError, IndexError):
                last_seq = 0
        return f"{prefix}{last_seq + 1:06d}"

    @staticmethod
    def _line_item_to_dict(item: InvoiceLineItem) -> dict:
        return {
            "description": item.description,
            "description_ar": item.description_ar,
            "item_type": item.item_type,
            "item_code": item.item_code,
            "unit_type": item.unit_type,
            "quantity": str(item.quantity),
            "unit_price": str(item.unit_price),
            "discount": str(item.discount),
            "sales_total": str(item.sales_total),
            "net_total": str(item.net_total),
            "taxes": [
                {
                    "tax_type": t.tax_type.value,
                    "amount": str(t.amount),
                    "sub_type": t.sub_type,
                    "rate": str(t.rate),
                }
                for t in item.taxes
            ],
            "total": str(item.total),
            "internal_code": item.internal_code,
        }

    @staticmethod
    def _dict_to_line_item(data: dict) -> InvoiceLineItem:
        taxes = [
            TaxLine(
                tax_type=TaxType(t["tax_type"]),
                amount=Decimal(t["amount"]),
                sub_type=t.get("sub_type", "V009"),
                rate=Decimal(t.get("rate", "14.00")),
            )
            for t in data.get("taxes", [])
        ]
        return InvoiceLineItem(
            description=data["description"],
            description_ar=data.get("description_ar"),
            item_type=data.get("item_type", "EGS"),
            item_code=data["item_code"],
            unit_type=data.get("unit_type", "EA"),
            quantity=Decimal(data["quantity"]),
            unit_price=Decimal(data["unit_price"]),
            discount=Decimal(data.get("discount", "0")),
            sales_total=Decimal(data["sales_total"]),
            net_total=Decimal(data["net_total"]),
            taxes=taxes,
            total=Decimal(data["total"]),
            internal_code=data.get("internal_code"),
        )

    def _to_entity(self, model: InvoiceModel) -> Invoice:
        seller_data = model.seller or {}
        buyer_data = model.buyer or {}
        line_items = [self._dict_to_line_item(li) for li in (model.line_items or [])]
        return Invoice(
            id=model.id,
            store_id=model.store_id,
            tenant_id=model.tenant_id,
            order_id=model.order_id,
            customer_id=model.customer_id,
            invoice_number=model.invoice_number,
            internal_id=model.internal_id,
            invoice_type=model.invoice_type or InvoiceType.INVOICE,
            status=model.status or InvoiceStatus.DRAFT,
            date_issued=model.date_issued or datetime.utcnow(),
            seller=SellerInfo(**seller_data),
            buyer=BuyerInfo(**buyer_data),
            currency=model.currency or "EGP",
            exchange_rate=Decimal(model.exchange_rate or 100) / 100,
            line_items=line_items,
            subtotal=model.subtotal or 0,
            total_discount=model.total_discount or 0,
            total_taxes=model.total_taxes or 0,
            extra_discount=model.extra_discount or 0,
            total=model.total or 0,
            prices_include_vat=(
                model.prices_include_vat
                if model.prices_include_vat is not None
                else True
            ),
            vat_rate=(
                Decimal(str(model.vat_rate))
                if model.vat_rate is not None
                else DEFAULT_VAT_RATE
            ),
            vat_amount=model.vat_amount or 0,
            net_amount_before_vat=model.net_amount_before_vat or 0,
            shipping_fee=model.shipping_fee or 0,
            grand_total=model.grand_total or model.total or 0,
            eta_uuid=model.eta_uuid,
            eta_long_id=model.eta_long_id,
            eta_submission_id=model.eta_submission_id,
            eta_internal_id=model.eta_internal_id,
            eta_hash=model.eta_hash,
            eta_status_code=model.eta_status_code,
            eta_status_message=model.eta_status_message,
            qr_code_data=model.qr_code_data,
            qr_code_image=model.qr_code_image,
            signature=model.signature,
            signature_type=model.signature_type,
            signature_timestamp=model.signature_timestamp,
            pdf_r2_key=model.pdf_r2_key,
            pdf_url=model.pdf_url,
            related_invoice_id=model.related_invoice_id,
            original_invoice_number=model.original_invoice_number,
            notes=model.notes,
            notes_ar=model.notes_ar,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
