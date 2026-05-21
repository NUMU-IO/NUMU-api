"""Seed data script for initial database setup."""

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.entities.coupon import CouponType
from src.core.entities.product import ProductStatus, ProductType
from src.core.entities.store import StoreStatus
from src.core.entities.user import UserRole, UserStatus
from src.core.value_objects.money import Currency
from src.infrastructure.database import AsyncSessionLocal
from src.infrastructure.database.models import (
    CategoryModel,
    CouponModel,
    ProductModel,
    PromotionDisplayModel,
    PromotionModel,
    PromotionTargetModel,
    PromotionTranslationModel,
    StoreModel,
    TenantModel,
    UserModel,
)
from src.infrastructure.external_services import password_service


async def seed_database():
    """Seed the database with initial data."""
    print("Starting database seeding...")

    async with AsyncSessionLocal() as session:
        # Check if data already exists
        existing_users = await session.execute(UserModel.__table__.select().limit(1))
        if existing_users.scalar():
            print("Database already seeded. Skipping...")
            return

        # Create super admin user
        admin_id = uuid4()
        admin_password = password_service.hash_password("admin123456")
        admin = UserModel(
            id=admin_id,
            email="admin@numueg.app",
            hashed_password=admin_password,
            first_name="Super",
            last_name="Admin",
            role=UserRole.SUPER_ADMIN,
            status=UserStatus.ACTIVE,
        )
        session.add(admin)
        print("Created admin user: admin@numueg.app")

        # Create store owner user
        owner_id = uuid4()
        owner_password = password_service.hash_password("owner123456")
        owner = UserModel(
            id=owner_id,
            email="owner@example.com",
            hashed_password=owner_password,
            first_name="Store",
            last_name="Owner",
            role=UserRole.STORE_OWNER,
            status=UserStatus.ACTIVE,
        )
        session.add(owner)
        print("Created store owner: owner@example.com")

        # Create customer user
        customer_id = uuid4()
        customer_password = password_service.hash_password("customer123456")
        customer = UserModel(
            id=customer_id,
            email="customer@example.com",
            hashed_password=customer_password,
            first_name="Test",
            last_name="Customer",
            role=UserRole.CUSTOMER,
            status=UserStatus.ACTIVE,
        )
        session.add(customer)
        print("Created customer: customer@example.com")

        # Flush to ensure users are created before tenant references them
        await session.flush()

        # Create a tenant for the store owner
        tenant_id = uuid4()
        tenant = TenantModel(
            id=tenant_id,
            name="Demo Company",
            subdomain="demo",
            owner_id=owner_id,
            plan="pro",
            is_active=True,
        )
        session.add(tenant)
        print("Created tenant: Demo Company (subdomain: demo)")

        # Create sample store (with tenant_id)
        store_id = uuid4()
        store = StoreModel(
            id=store_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            name="Demo Store",
            slug="demo-store",
            subdomain="demo",
            description="A sample demo store for testing",
            status=StoreStatus.ACTIVE,
            default_currency=Currency.EGP,
            default_language="ar",
            contact_email="store@example.com",
        )
        session.add(store)
        print("Created store: Demo Store")

        # Create categories (with tenant_id)
        electronics_id = uuid4()
        electronics = CategoryModel(
            id=electronics_id,
            tenant_id=tenant_id,
            store_id=store_id,
            name="Electronics",
            slug="electronics",
            description="Electronic devices and gadgets",
            is_active=True,
        )
        session.add(electronics)

        clothing_id = uuid4()
        clothing = CategoryModel(
            id=clothing_id,
            tenant_id=tenant_id,
            store_id=store_id,
            name="Clothing",
            slug="clothing",
            description="Fashion and apparel",
            is_active=True,
        )
        session.add(clothing)
        print("Created categories: Electronics, Clothing")

        # Create sample products (with tenant_id)
        products = [
            ProductModel(
                id=uuid4(),
                tenant_id=tenant_id,
                store_id=store_id,
                category_id=electronics_id,
                name="Wireless Headphones",
                slug="wireless-headphones",
                description="High-quality wireless headphones with noise cancellation",
                price_amount=249900,  # 2,499.00 EGP in piasters
                price_currency="EGP",
                sku="WH-001",
                quantity=50,
                product_type=ProductType.PHYSICAL,
                status=ProductStatus.ACTIVE,
            ),
            ProductModel(
                id=uuid4(),
                tenant_id=tenant_id,
                store_id=store_id,
                category_id=electronics_id,
                name="Smart Watch",
                slug="smart-watch",
                description="Feature-rich smartwatch with health tracking",
                price_amount=499900,  # 4,999.00 EGP in piasters
                price_currency="EGP",
                sku="SW-001",
                quantity=30,
                product_type=ProductType.PHYSICAL,
                status=ProductStatus.ACTIVE,
            ),
            ProductModel(
                id=uuid4(),
                tenant_id=tenant_id,
                store_id=store_id,
                category_id=clothing_id,
                name="Classic T-Shirt",
                slug="classic-t-shirt",
                description="Comfortable cotton t-shirt",
                price_amount=59900,  # 599.00 EGP in piasters
                price_currency="EGP",
                sku="TS-001",
                quantity=100,
                product_type=ProductType.PHYSICAL,
                status=ProductStatus.ACTIVE,
            ),
        ]

        for product in products:
            session.add(product)
        print(f"Created {len(products)} sample products")

        # ---- Offers / Promotions seed (offers-v2 step 01) ----
        # 1) A reusable code coupon for the discount_code surface.
        welcome_coupon_id = uuid4()
        welcome_coupon = CouponModel(
            id=welcome_coupon_id,
            tenant_id=tenant_id,
            store_id=store_id,
            code="WELCOME10",
            coupon_type=CouponType.PERCENTAGE,
            value=Decimal("10"),
            min_order_amount=Decimal("100"),
            usage_count=0,
            is_active=True,
        )
        session.add(welcome_coupon)

        now = datetime.now(UTC)

        # 2) Active discount_code promotion linked to the coupon above.
        code_promo_id = uuid4()
        session.add(
            PromotionModel(
                id=code_promo_id,
                tenant_id=tenant_id,
                store_id=store_id,
                name="Welcome 10% off (code: WELCOME10)",
                surface="discount_code",
                status="active",
                coupon_id=welcome_coupon_id,
                discount_rule=None,
                content={},
                priority=10,
                starts_at=now - timedelta(days=1),
                ends_at=now + timedelta(days=30),
            )
        )
        session.add(
            PromotionTranslationModel(
                tenant_id=tenant_id,
                promotion_id=code_promo_id,
                locale="en",
                content={"label": "WELCOME10 — 10% off your first order"},
            )
        )
        session.add(
            PromotionTranslationModel(
                tenant_id=tenant_id,
                promotion_id=code_promo_id,
                locale="ar",
                content={"label": "WELCOME10 — خصم 10٪ على أول طلب"},
            )
        )

        # 3) Active automatic 10% off targeting new visitors.
        auto_promo_id = uuid4()
        session.add(
            PromotionModel(
                id=auto_promo_id,
                tenant_id=tenant_id,
                store_id=store_id,
                name="Automatic 10% off — new visitors",
                surface="automatic",
                status="active",
                discount_rule={
                    "kind": "percentage",
                    "value_percent": 10,
                    "min_subtotal_cents": 50000,
                    "max_discount_cents": 20000,
                },
                content={"label": "First-time visitor discount"},
                priority=20,
            )
        )
        session.add(
            PromotionDisplayModel(
                tenant_id=tenant_id,
                promotion_id=auto_promo_id,
                trigger="always",
                trigger_value={},
                frequency="every_visit",
                pages=[],
                device_targets=["desktop", "mobile"],
                is_enabled=True,
            )
        )
        session.add(
            PromotionTargetModel(
                tenant_id=tenant_id,
                promotion_id=auto_promo_id,
                target_kind="audience",
                target_value={"kind": "new_visitor"},
                inclusion=True,
            )
        )
        session.add(
            PromotionTranslationModel(
                tenant_id=tenant_id,
                promotion_id=auto_promo_id,
                locale="en",
                content={"label": "10% off your first order, automatically"},
            )
        )
        session.add(
            PromotionTranslationModel(
                tenant_id=tenant_id,
                promotion_id=auto_promo_id,
                locale="ar",
                content={"label": "خصم 10٪ تلقائي على أول طلب لك"},
            )
        )

        # 4) Active announcement bar with bilingual content.
        bar_promo_id = uuid4()
        session.add(
            PromotionModel(
                id=bar_promo_id,
                tenant_id=tenant_id,
                store_id=store_id,
                name="Free shipping over 500 EGP — announcement bar",
                surface="announcement_bar",
                status="active",
                content={
                    "background": "#0f172a",
                    "text_color": "#ffffff",
                    "icon": "sparkle",
                    "dismissible": True,
                    "link_url": "/products",
                },
                priority=30,
            )
        )
        session.add(
            PromotionDisplayModel(
                tenant_id=tenant_id,
                promotion_id=bar_promo_id,
                trigger="always",
                trigger_value={},
                frequency="until_dismissed",
                pages=[],
                device_targets=["desktop", "mobile"],
                is_enabled=True,
            )
        )
        session.add(
            PromotionTranslationModel(
                tenant_id=tenant_id,
                promotion_id=bar_promo_id,
                locale="en",
                content={"headline": "Free shipping on orders over 500 EGP"},
            )
        )
        session.add(
            PromotionTranslationModel(
                tenant_id=tenant_id,
                promotion_id=bar_promo_id,
                locale="ar",
                content={"headline": "شحن مجاني للطلبات فوق 500 ج.م"},
            )
        )

        # 5) Paused popup so the merchant UI has something to "activate".
        popup_promo_id = uuid4()
        session.add(
            PromotionModel(
                id=popup_promo_id,
                tenant_id=tenant_id,
                store_id=store_id,
                name="Welcome popup — email capture",
                surface="popup",
                status="paused",
                content={
                    "layout": "centered",
                    "image_url": "",
                    "form_fields": ["email"],
                    "discount_code_to_reveal": "WELCOME10",
                    "show_after_dismiss_days": 30,
                },
                priority=40,
            )
        )
        session.add(
            PromotionDisplayModel(
                tenant_id=tenant_id,
                promotion_id=popup_promo_id,
                trigger="on_delay",
                trigger_value={"delay_ms": 5000},
                frequency="once_per_visitor",
                pages=["/"],
                device_targets=["desktop", "mobile"],
                is_enabled=True,
            )
        )
        session.add(
            PromotionTranslationModel(
                tenant_id=tenant_id,
                promotion_id=popup_promo_id,
                locale="en",
                content={
                    "headline": "Welcome! Get 10% off",
                    "body": "Sign up and we'll email you a code.",
                    "cta_label": "Get my code",
                },
            )
        )
        session.add(
            PromotionTranslationModel(
                tenant_id=tenant_id,
                promotion_id=popup_promo_id,
                locale="ar",
                content={
                    "headline": "أهلاً بك! احصل على خصم 10٪",
                    "body": "سجل بريدك ونرسل لك الكود.",
                    "cta_label": "أرسل لي الكود",
                },
            )
        )

        print(
            "Created 1 coupon (WELCOME10) and 4 promotions (1 code, 1 auto, 1 bar, 1 popup)"
        )

        # Commit all changes
        await session.commit()
        print("Database seeding completed successfully!")

        # Print login credentials
        print("\n" + "=" * 50)
        print("TEST ACCOUNTS:")
        print("=" * 50)
        print("Admin:    admin@numueg.app / admin123456")
        print("Owner:    owner@example.com / owner123456")
        print("Customer: customer@example.com / customer123456")
        print("=" * 50)
        print("\nTENANT INFO:")
        print("=" * 50)
        print("Tenant:   Demo Company")
        print("Subdomain: demo")
        print("=" * 50)


if __name__ == "__main__":
    asyncio.run(seed_database())
