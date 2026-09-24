"""Paid themes: one purchase per store from the wallet, the partner's share,
VAT on NUMU's fee, the install gate and refunds.

In-memory SQLite, same helpers as test_app_billing.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from src.api.v1.routes.marketplace import store_install
from src.api.v1.schemas.tenant.marketplace import InstallThemeRequest
from src.application.services import app_billing as billing
from src.application.services.marketplace_service import (
    MarketplaceService,
    ThemePurchaseRequired,
)
from src.core.entities.user import UserRole, UserStatus
from src.infrastructure.database.models.public.app_billing import (
    AppFeeInvoiceModel,
    PartnerLedgerEntryModel,
)
from src.infrastructure.database.models.public.partner_account import (
    PartnerAccountModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.public.wallet import WalletTransactionModel
from src.infrastructure.database.models.tenant.marketplace_theme import (
    MarketplaceThemeModel,
    MarketplaceThemePurchaseModel,
    MarketplaceThemeVersionModel,
)
from src.infrastructure.repositories.marketplace_repository import (
    MarketplaceRepository,
)
from tests.unit.application.test_app_billing import (
    NOW,
    _balance,
    _ledger_sum,
    _source,
    _top_up,
)

PRICE = 50_000
FEE, VAT = 10_000, 1_400
TOTAL = PRICE + VAT


@pytest.fixture(autouse=True)
async def _sqlite_now(test_session):
    raw = await (await test_session.connection()).get_raw_connection()
    await raw.driver_connection.create_function(
        "NOW", 0, lambda: datetime.now(UTC).isoformat(" ")
    )


async def _user(s):
    u = UserModel(
        id=uuid4(),
        email=f"u-{uuid4().hex[:8]}@example.com",
        hashed_password="x",
        first_name="Test",
        last_name="User",
        role=UserRole.STORE_OWNER,
        status=UserStatus.ACTIVE,
        email_verified_at=datetime.now(UTC),
    )
    s.add(u)
    await s.flush()
    return u


async def _world(s, *, balance=100_000, share_bps=None, partner=True):
    dev = await _user(s)
    buyer = await _user(s)
    account = None
    if partner:
        account = PartnerAccountModel(
            user_id=dev.id,
            kind="company",
            display_name="Nile Themes",
            support_email=dev.email,
            status="approved",
            country="EG",
            share_bps=share_bps,
        )
        s.add(account)
    tenant = TenantModel(
        id=uuid4(),
        name="Store Co",
        subdomain=f"t-{uuid4().hex[:8]}",
        plan="starter",
        lifecycle_state="active",
    )
    s.add(tenant)
    theme = MarketplaceThemeModel(
        id=uuid4(),
        developer_id=dev.id,
        name="Nile",
        slug=f"nile-{uuid4().hex[:8]}",
        status="published",
        price_cents=PRICE,
        currency="EGP",
        flags={},
    )
    s.add(theme)
    await s.flush()
    s.add(
        MarketplaceThemeVersionModel(
            id=uuid4(),
            theme_id=theme.id,
            version_string="1.0.0",
            status="published",
            lint_status="passed",
            bundle_url="https://cdn.numueg.app/nile/1.0.0/theme.js",
        )
    )
    if balance:
        await _top_up(s, tenant.id, balance)
    await s.commit()
    return tenant, theme, buyer, account


async def _buy(s, tenant, theme, buyer, store_id):
    return await billing.purchase_theme(
        s,
        theme=theme,
        store_id=store_id,
        tenant_id=tenant.id,
        user_id=buyer.id,
        source=_source(s),
        now=NOW,
    )


def _svc(s):
    return MarketplaceService(marketplace_repo=MarketplaceRepository(s))


@pytest.mark.asyncio
async def test_a_store_buys_once_and_a_retry_charges_nothing(test_session):
    tenant, theme, buyer, _ = await _world(test_session)
    store_id = uuid4()
    purchase, charged = await _buy(test_session, tenant, theme, buyer, store_id)
    await test_session.commit()
    again, charged_again = await _buy(test_session, tenant, theme, buyer, store_id)
    await test_session.commit()

    assert charged and not charged_again and again.id == purchase.id
    assert purchase.amount_cents == TOTAL
    assert await _balance(test_session, tenant.id) == 100_000 - TOTAL
    charges = await test_session.scalar(
        select(func.count()).where(WalletTransactionModel.kind == "app_charge")
    )
    assert charges == 1


@pytest.mark.asyncio
async def test_vat_and_the_partners_own_share(test_session):
    tenant, theme, buyer, _ = await _world(test_session, share_bps=7_000)
    await _buy(test_session, tenant, theme, buyer, uuid4())
    await test_session.commit()

    sale = await test_session.scalar(select(PartnerLedgerEntryModel))
    assert (sale.theme_id, sale.share_bps, sale.amount_cents) == (
        theme.id,
        7_000,
        35_000,
    )
    assert (sale.platform_fee_cents, sale.vat_cents) == (15_000, 2_100)
    invoice = await test_session.scalar(select(AppFeeInvoiceModel))
    assert (invoice.theme_id, invoice.fee_cents, invoice.vat_cents) == (
        theme.id,
        15_000,
        2_100,
    )
    assert invoice.total_cents == PRICE + 2_100


@pytest.mark.asyncio
async def test_a_numu_theme_keeps_it_all(test_session):
    tenant, theme, buyer, _ = await _world(test_session, partner=False)
    await _buy(test_session, tenant, theme, buyer, uuid4())
    await test_session.commit()
    assert (
        await test_session.scalar(select(func.count(PartnerLedgerEntryModel.id))) == 0
    )
    invoice = await test_session.scalar(select(AppFeeInvoiceModel))
    assert (invoice.fee_cents, invoice.vat_cents) == (PRICE, 7_000)


@pytest.mark.asyncio
async def test_a_short_wallet_is_refused_with_both_amounts(test_session):
    tenant, theme, buyer, _ = await _world(test_session, balance=TOTAL - 1)
    with pytest.raises(billing.InsufficientFundsError) as exc:
        await _buy(test_session, tenant, theme, buyer, uuid4())
    assert (exc.value.needed_cents, exc.value.balance_cents) == (TOTAL, TOTAL - 1)


@pytest.mark.asyncio
async def test_install_and_activate_need_a_purchase(test_session):
    tenant, theme, buyer, _ = await _world(test_session)
    store_id = uuid4()
    svc = _svc(test_session)
    with pytest.raises(HTTPException) as exc:
        await store_install.install_theme(
            store_id,
            InstallThemeRequest(marketplace_theme_id=str(theme.id)),
            svc,
            buyer.id,
        )
    assert exc.value.status_code == 402
    assert exc.value.detail["code"] == "theme_purchase_required"

    await _buy(test_session, tenant, theme, buyer, store_id)
    await test_session.commit()
    installed = await svc.install_theme(store_id, theme.id, user_id=buyer.id)
    assert installed["marketplace_theme_id"] == str(theme.id)
    with pytest.raises(ThemePurchaseRequired):
        await svc.install_theme(uuid4(), theme.id, user_id=buyer.id)


@pytest.mark.asyncio
async def test_a_refund_reverses_everything(test_session):
    tenant, theme, buyer, account = await _world(test_session)
    store_id = uuid4()
    purchase, _ = await _buy(test_session, tenant, theme, buyer, store_id)
    await _svc(test_session).install_theme(store_id, theme.id, user_id=buyer.id)
    await test_session.commit()

    await billing.refund_charge(
        test_session,
        charge_id=purchase.wallet_transaction_id,
        actor_user_id=uuid4(),
        note="refund",
    )
    await test_session.commit()

    assert await _balance(test_session, tenant.id) == 100_000
    assert await _ledger_sum(test_session, account.id) == 0
    assert purchase.status == "refunded"
    note = await test_session.scalar(
        select(AppFeeInvoiceModel).where(AppFeeInvoiceModel.kind == "credit_note")
    )
    assert (note.theme_id, note.vat_cents, note.total_cents) == (theme.id, -VAT, -TOTAL)
    with pytest.raises(ThemePurchaseRequired):
        await _svc(test_session).install_theme(store_id, theme.id, user_id=buyer.id)
    with pytest.raises(ThemePurchaseRequired):
        await _svc(test_session).activate_theme(store_id, theme.id, user_id=buyer.id)

    again, charged = await _buy(test_session, tenant, theme, buyer, store_id)
    await test_session.commit()
    assert charged and again.id != purchase.id
    rows = await test_session.scalar(
        select(func.count(MarketplaceThemePurchaseModel.id)).where(
            MarketplaceThemePurchaseModel.store_id == store_id
        )
    )
    assert rows == 2


@pytest.mark.asyncio
async def test_a_partner_sees_only_their_own_theme_sales(test_session):
    tenant, theme, buyer, account = await _world(test_session)
    _, _, _, other = await _world(test_session)
    await _buy(test_session, tenant, theme, buyer, uuid4())
    await test_session.commit()

    month = NOW.strftime("%Y-%m")
    mine = await billing.partner_statement(test_session, account.id, month)
    theirs = await billing.partner_statement(test_session, other.id, month)
    assert mine["net_sales_cents"] == PRICE - FEE
    assert mine["entries"][0]["item"] == "theme"
    assert mine["entries"][0]["app_name"] == "Nile"
    assert theirs["net_sales_cents"] == 0 and theirs["entries"] == []


@pytest.mark.asyncio
async def test_a_partner_price_waits_for_billing_and_review(test_session):
    from src.infrastructure.database.models.public.platform_config import (
        PlatformConfigModel,
    )

    dev = await _user(test_session)
    svc = _svc(test_session)
    data = {"name": "Delta", "slug": f"delta-{uuid4().hex[:6]}", "price_cents": PRICE}
    with pytest.raises(ValueError, match="coming soon"):
        await svc.create_listing(dev.id, data)

    test_session.add(
        PlatformConfigModel(key="partner_billing", value={"enabled": True})
    )
    await test_session.flush()
    with pytest.raises(ValueError, match="between"):
        await svc.create_listing(dev.id, {**data, "price_cents": 100})
    listing = await svc.create_listing(dev.id, data)
    assert (listing["price_cents"], listing["pending_price_cents"]) == (0, PRICE)

    version = MarketplaceThemeVersionModel(
        id=uuid4(),
        theme_id=UUID(listing["id"]),
        version_string="1.0.0",
        status="pending_review",
        lint_status="passed",
        bundle_url="https://cdn.numueg.app/delta/1.0.0/theme.js",
    )
    test_session.add(version)
    await test_session.flush()
    item = next(
        i
        for i in await svc.list_pending_reviews()
        if i["version_id"] == str(version.id)
    )
    assert (item["price_cents"], item["pending_price_cents"]) == (0, PRICE)
    await svc.review_version(uuid4(), version.id, "approve")
    theme = await test_session.get(MarketplaceThemeModel, UUID(listing["id"]))
    await test_session.refresh(theme)
    assert (theme.price_cents, theme.pending_price_cents, theme.currency) == (
        PRICE,
        None,
        "EGP",
    )
