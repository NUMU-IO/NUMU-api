"""AbandonedCheckout repository implementation."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.abandoned_checkout import AbandonedCheckout
from src.core.interfaces.repositories.abandoned_checkout_repository import (
    IAbandonedCheckoutRepository,
)
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.abandoned_checkout import (
    AbandonedCheckoutModel,
)


class AbandonedCheckoutRepository(IAbandonedCheckoutRepository):
    """SQLAlchemy implementation of the abandoned-checkout repository.

    All queries include an explicit tenant_id filter as defense-in-depth
    alongside RLS.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        tid = get_tenant_id()
        if tid:
            return query.where(AbandonedCheckoutModel.tenant_id == tid)
        return query

    def _to_entity(self, model: AbandonedCheckoutModel) -> AbandonedCheckout:
        return AbandonedCheckout(
            id=model.id,
            tenant_id=model.tenant_id,
            store_id=model.store_id,
            customer_id=model.customer_id,
            line_items=list(model.line_items or []),
            email=model.email,
            phone=model.phone,
            shipping_address=model.shipping_address,
            subtotal=model.subtotal or 0,
            shipping_cost=model.shipping_cost or 0,
            tax_amount=model.tax_amount or 0,
            discount_amount=model.discount_amount or 0,
            total=model.total or 0,
            currency=model.currency or "EGP",
            coupon_code=model.coupon_code,
            utm_source=model.utm_source,
            utm_medium=model.utm_medium,
            utm_campaign=model.utm_campaign,
            last_activity_at=model.last_activity_at,
            abandoned_at=model.abandoned_at,
            recovered_at=model.recovered_at,
            recovery_email_sent_at=model.recovery_email_sent_at,
            recovered_order_id=model.recovered_order_id,
            extra_data=model.extra_data or {},
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: AbandonedCheckout) -> AbandonedCheckoutModel:
        return AbandonedCheckoutModel(
            id=entity.id,
            tenant_id=entity.tenant_id,
            store_id=entity.store_id,
            customer_id=entity.customer_id,
            line_items=entity.line_items,
            email=entity.email,
            phone=entity.phone,
            shipping_address=entity.shipping_address,
            subtotal=entity.subtotal,
            shipping_cost=entity.shipping_cost,
            tax_amount=entity.tax_amount,
            discount_amount=entity.discount_amount,
            total=entity.total,
            currency=entity.currency,
            coupon_code=entity.coupon_code,
            utm_source=entity.utm_source,
            utm_medium=entity.utm_medium,
            utm_campaign=entity.utm_campaign,
            last_activity_at=entity.last_activity_at,
            abandoned_at=entity.abandoned_at,
            recovered_at=entity.recovered_at,
            recovery_email_sent_at=entity.recovery_email_sent_at,
            recovered_order_id=entity.recovered_order_id,
            extra_data=entity.extra_data,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> AbandonedCheckout | None:
        query = select(AbandonedCheckoutModel).where(
            AbandonedCheckoutModel.id == entity_id
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
    ) -> list[AbandonedCheckout]:
        query = (
            select(AbandonedCheckoutModel)
            .order_by(AbandonedCheckoutModel.last_activity_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: AbandonedCheckout) -> AbandonedCheckout:
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: AbandonedCheckout) -> AbandonedCheckout:
        query = select(AbandonedCheckoutModel).where(
            AbandonedCheckoutModel.id == entity.id
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if not model:
            raise ValueError(f"AbandonedCheckout {entity.id} not found")
        # Copy the mutable fields. id / store_id / tenant_id are immutable.
        model.customer_id = entity.customer_id
        model.line_items = entity.line_items
        model.email = entity.email
        model.phone = entity.phone
        model.shipping_address = entity.shipping_address
        model.subtotal = entity.subtotal
        model.shipping_cost = entity.shipping_cost
        model.tax_amount = entity.tax_amount
        model.discount_amount = entity.discount_amount
        model.total = entity.total
        model.currency = entity.currency
        model.coupon_code = entity.coupon_code
        model.utm_source = entity.utm_source
        model.utm_medium = entity.utm_medium
        model.utm_campaign = entity.utm_campaign
        model.last_activity_at = entity.last_activity_at
        model.abandoned_at = entity.abandoned_at
        # Recovery is monotonic — an upsert may SET these, never clear them.
        #
        # The storefront's cart-track POST and the order request overlap at
        # checkout (the submit handler fires both). Under READ COMMITTED the
        # cart-track UPDATE blocks on the row the order has just flipped to
        # recovered, then proceeds with the entity it loaded BEFORE that flip
        # — and wrote `recovered_at = NULL` back. The order existed, the row
        # sat "Abandoned" on the merchant's page regardless.
        if entity.recovered_at is not None:
            model.recovered_at = entity.recovered_at
        if entity.recovery_email_sent_at is not None:
            model.recovery_email_sent_at = entity.recovery_email_sent_at
        if entity.recovered_order_id is not None:
            model.recovered_order_id = entity.recovered_order_id
        model.extra_data = entity.extra_data
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def delete(self, entity_id: UUID) -> bool:
        query = select(AbandonedCheckoutModel).where(
            AbandonedCheckoutModel.id == entity_id
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        result = await self.session.execute(
            select(func.count(AbandonedCheckoutModel.id))
        )
        return result.scalar() or 0

    async def list_by_store(
        self,
        store_id: UUID,
        skip: int = 0,
        limit: int = 50,
        abandoned_only: bool = True,
        recovered_only: bool | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        has_contact: bool | None = None,
    ) -> tuple[list[AbandonedCheckout], int]:
        base = select(AbandonedCheckoutModel).where(
            AbandonedCheckoutModel.store_id == store_id
        )
        if abandoned_only:
            base = base.where(AbandonedCheckoutModel.abandoned_at.isnot(None))
        if recovered_only is True:
            base = base.where(AbandonedCheckoutModel.recovered_at.isnot(None))
        elif recovered_only is False:
            base = base.where(AbandonedCheckoutModel.recovered_at.is_(None))
        if has_contact is True:
            base = base.where(
                or_(
                    AbandonedCheckoutModel.email.isnot(None),
                    AbandonedCheckoutModel.phone.isnot(None),
                )
            )
        elif has_contact is False:
            base = base.where(
                AbandonedCheckoutModel.email.is_(None),
                AbandonedCheckoutModel.phone.is_(None),
            )
        if date_from:
            base = base.where(AbandonedCheckoutModel.created_at >= date_from)
        if date_to:
            base = base.where(AbandonedCheckoutModel.created_at <= date_to)

        count_query = select(func.count()).select_from(base.subquery())
        count_result = await self.session.execute(self._tenant_filter(count_query))
        total = count_result.scalar() or 0

        items_query = (
            base.order_by(AbandonedCheckoutModel.last_activity_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(items_query))
        return [self._to_entity(m) for m in result.scalars().all()], total

    async def mark_recovery_email_sent(
        self, checkout_id: UUID, when: datetime
    ) -> AbandonedCheckout:
        query = select(AbandonedCheckoutModel).where(
            AbandonedCheckoutModel.id == checkout_id
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if not model:
            raise ValueError(f"AbandonedCheckout {checkout_id} not found")
        model.recovery_email_sent_at = when
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def mark_recovered(
        self,
        checkout_id: UUID,
        order_id: UUID | None = None,
        when: datetime | None = None,
    ) -> AbandonedCheckout:
        query = select(AbandonedCheckoutModel).where(
            AbandonedCheckoutModel.id == checkout_id
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        if not model:
            raise ValueError(f"AbandonedCheckout {checkout_id} not found")
        model.recovered_at = when or datetime.now(UTC)
        if order_id is not None:
            model.recovered_order_id = order_id
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def find_active_for_session(
        self,
        store_id: UUID,
        session_fingerprint: str | None,
        email: str | None,
        phone: str | None = None,
        checkout_id: UUID | None = None,
    ) -> AbandonedCheckout | None:
        """Find the most recent un-recovered cart for this shopper.

        Match keys, strongest first:

        * ``checkout_id`` — the recovery-link case. A shopper who opens the
          WhatsApp/email link usually lands with a BRAND-NEW fingerprint
          (other device, other browser), so without this key every recovery
          click minted a duplicate contactless row while the original —
          the one carrying the phone/email — sat "abandoned" forever, and
          order-time reconciliation then marked the ghost instead of it.
          The storefront carries the id from the recover redirect into its
          cart-track payload. Store-scoped and only ever an un-recovered
          row, so a stale/foreign id degrades to the weaker keys.
        * ``session_fingerprint`` / ``email`` / ``phone`` — OR-matched.
          Phone joins the set because the identity layer now captures it
          long before email exists; it is what stitches a returning
          shopper's new session onto the cart we can actually recover.
        """
        if checkout_id is not None:
            query = (
                select(AbandonedCheckoutModel)
                .where(
                    AbandonedCheckoutModel.id == checkout_id,
                    AbandonedCheckoutModel.store_id == store_id,
                    AbandonedCheckoutModel.recovered_at.is_(None),
                )
                .limit(1)
            )
            result = await self.session.execute(self._tenant_filter(query))
            model = result.scalar_one_or_none()
            if model is not None:
                return self._to_entity(model)

        if not session_fingerprint and not email and not phone:
            return None

        clauses = []
        if session_fingerprint:
            clauses.append(
                AbandonedCheckoutModel.extra_data["session_fingerprint"].astext
                == session_fingerprint
            )
        if email:
            clauses.append(AbandonedCheckoutModel.email == email)
        if phone:
            clauses.append(AbandonedCheckoutModel.phone == phone)

        query = (
            select(AbandonedCheckoutModel)
            .where(
                AbandonedCheckoutModel.store_id == store_id,
                AbandonedCheckoutModel.recovered_at.is_(None),
                or_(*clauses),
            )
            .order_by(AbandonedCheckoutModel.last_activity_at.desc())
            .limit(1)
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    @staticmethod
    def _shopper_clauses(
        session_fingerprint: str | None,
        email: str | None,
        phone: str | None,
    ) -> list:
        """OR-able predicates identifying one shopper's rows.

        Email compares case-insensitively (the checkout form and the cart
        tracker can capitalise differently). Phone matches exactly OR on the
        last 9 digits, so "+20 100 123 4567", "01001234567" and
        "+201001234567" — all real inputs for one number — line up.
        """
        # Callers may hand over `Email` / `PhoneNumber` value objects.
        email = getattr(email, "value", email)
        phone = getattr(phone, "value", phone)
        clauses: list = []
        if session_fingerprint:
            clauses.append(
                AbandonedCheckoutModel.extra_data["session_fingerprint"].astext
                == session_fingerprint
            )
        if email and email.strip():
            clauses.append(
                func.lower(AbandonedCheckoutModel.email) == email.strip().lower()
            )
        if phone and phone.strip():
            clauses.append(AbandonedCheckoutModel.phone == phone)
            digits = "".join(ch for ch in phone if ch.isdigit())
            if len(digits) >= 9:
                clauses.append(
                    func.right(
                        func.regexp_replace(
                            AbandonedCheckoutModel.phone, r"\D", "", "g"
                        ),
                        9,
                    )
                    == digits[-9:]
                )
        return clauses

    async def mark_recovered_for_shopper(
        self,
        *,
        store_id: UUID,
        session_fingerprint: str | None,
        email: str | None,
        phone: str | None,
        order_id: UUID | None = None,
        when: datetime | None = None,
    ) -> int:
        """Flip EVERY open cart of this shopper to recovered.

        Order-time reconciliation used to recover only the single most
        recent matching row. A shopper with two open rows — the common
        shape once the identity layer writes a phone-only row early and a
        later session writes another — converted, and the other row stayed
        "Abandoned" with the same items and value. One order recovers all
        of that shopper's open carts; the merchant is not going to win them
        back twice.
        """
        clauses = self._shopper_clauses(session_fingerprint, email, phone)
        if not clauses:
            return 0
        now = datetime.now(UTC)
        values: dict = {"recovered_at": when or now, "updated_at": now}
        if order_id is not None:
            values["recovered_order_id"] = order_id
        stmt = (
            update(AbandonedCheckoutModel)
            .where(
                AbandonedCheckoutModel.store_id == store_id,
                AbandonedCheckoutModel.recovered_at.is_(None),
                or_(*clauses),
            )
            .values(**values)
        )
        tid = get_tenant_id()
        if tid:
            stmt = stmt.where(AbandonedCheckoutModel.tenant_id == tid)
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def recently_recovered_for_shopper(
        self,
        *,
        store_id: UUID,
        session_fingerprint: str | None,
        email: str | None,
        phone: str | None,
        within_seconds: int,
    ) -> bool:
        """Whether one of this shopper's carts was recovered very recently.

        The cart-track upsert calls this when it finds no open row: a POST
        landing just after the order (the checkout's in-flight enrichment,
        a debounced cart-change echo) would otherwise re-create the same
        cart as a brand-new "abandoned" row seconds after it converted.
        """
        clauses = self._shopper_clauses(session_fingerprint, email, phone)
        if not clauses:
            return False
        cutoff = datetime.now(UTC) - timedelta(seconds=within_seconds)
        query = (
            select(AbandonedCheckoutModel.id)
            .where(
                AbandonedCheckoutModel.store_id == store_id,
                AbandonedCheckoutModel.recovered_at >= cutoff,
                or_(*clauses),
            )
            .limit(1)
        )
        result = await self.session.execute(self._tenant_filter(query))
        return result.scalar_one_or_none() is not None

    async def mark_stale_as_abandoned(
        self, store_id: UUID, threshold_seconds: int
    ) -> int:
        """Bulk-flip `abandoned_at` on rows older than the threshold.

        Cheap UPDATE — runs at most ~once per merchant list-page load. Avoids
        a dedicated Celery beat job for Phase 4b. The cron job can be added
        later if we want abandonment to surface even when no merchant is
        actively viewing the page.

        `abandoned_at` is set to `last_activity_at + threshold` — the moment
        the row crossed the abandonment line — NOT `now()`. Using `now()`
        made every row look "just abandoned" the first time a merchant
        opened the page, even if the customer had been idle for hours.
        """
        cutoff = datetime.now(UTC) - timedelta(seconds=threshold_seconds)
        now = datetime.now(UTC)
        stmt = (
            update(AbandonedCheckoutModel)
            .where(
                AbandonedCheckoutModel.store_id == store_id,
                AbandonedCheckoutModel.abandoned_at.is_(None),
                AbandonedCheckoutModel.recovered_at.is_(None),
                AbandonedCheckoutModel.last_activity_at < cutoff,
            )
            .values(
                abandoned_at=AbandonedCheckoutModel.last_activity_at
                + timedelta(seconds=threshold_seconds),
                updated_at=now,
            )
        )
        # Tenant filter via raw where-clause (update() doesn't go through _tenant_filter)
        from src.infrastructure.database.connection import get_tenant_id

        tid = get_tenant_id()
        if tid:
            stmt = stmt.where(AbandonedCheckoutModel.tenant_id == tid)
        result = await self.session.execute(stmt)
        return result.rowcount or 0
