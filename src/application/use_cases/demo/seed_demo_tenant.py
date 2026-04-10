"""Per-tenant demo data seeder.

Stream 1.3 of the NUMU plan. Populates a freshly-provisioned demo tenant
with realistic data the user can play with in the merchant dashboard:
categories, products, customers, and orders spread across statuses with
dates over the last 30 days.

Every seeded row carries ``metadata / extra_data = {"demo_seeded": True}``
so the ConvertDemoUseCase can wipe fake content while preserving anything
the user added during their demo session.
"""

import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.order import FulfillmentStatus, OrderStatus, PaymentStatus
from src.core.entities.product import ProductStatus, ProductType
from src.infrastructure.database.models import (
    CategoryModel,
    CustomerModel,
    OrderModel,
    ProductModel,
)

logger = logging.getLogger(__name__)

DEMO_SEED_FLAG = {"demo_seeded": True}

# ─── Egyptian demo data ──────────────────────────────────────────────────

_CATEGORIES = [
    ("Tops", "tops", "بلوزات وتيشيرتات"),
    ("Bottoms", "bottoms", "بناطيل وجيبات"),
    ("Shoes", "shoes", "أحذية"),
    ("Accessories", "accessories", "إكسسوارات"),
    ("New Arrivals", "new-arrivals", "وصل حديثاً"),
]

_PRODUCTS = [
    # (name, slug, category_idx, price_piasters, sku, qty, description_ar)
    (
        "Cotton T-Shirt",
        "cotton-t-shirt",
        0,
        34900,
        "TS-001",
        120,
        "تيشيرت قطن ١٠٠٪ مريح جداً",
    ),
    ("Linen Blouse", "linen-blouse", 0, 59900, "BL-001", 45, "بلوزة كتان صيفي أنيقة"),
    (
        "Oversized Hoodie",
        "oversized-hoodie",
        0,
        79900,
        "HD-001",
        30,
        "هودي أوفر سايز شتوي",
    ),
    (
        "Slim Fit Jeans",
        "slim-fit-jeans",
        1,
        89900,
        "JN-001",
        60,
        "جينز سليم فيت بقصة عصرية",
    ),
    ("Wide-Leg Pants", "wide-leg-pants", 1, 69900, "WP-001", 40, "بنطلون واسع مريح"),
    ("Pleated Skirt", "pleated-skirt", 1, 54900, "SK-001", 35, "جيبة بليسيه أنيقة"),
    (
        "Leather Sneakers",
        "leather-sneakers",
        2,
        149900,
        "SN-001",
        25,
        "سنيكرز جلد طبيعي",
    ),
    ("Slide Sandals", "slide-sandals", 2, 39900, "SD-001", 80, "صندل سلايد خفيف"),
    ("Gold Necklace", "gold-necklace", 3, 249900, "NK-001", 15, "سلسلة مطلية ذهب"),
    ("Canvas Tote Bag", "canvas-tote-bag", 3, 44900, "TB-001", 50, "شنطة كانفاس عملية"),
]

_FIRST_NAMES = [
    "أحمد",
    "محمد",
    "فاطمة",
    "سارة",
    "ياسمين",
    "عمر",
    "نور",
    "ليلى",
    "كريم",
    "دينا",
    "حسن",
    "مريم",
    "يوسف",
    "هدى",
    "خالد",
    "رنا",
    "طارق",
    "سلمى",
    "علي",
    "منى",
]
_LAST_NAMES = [
    "محمود",
    "إبراهيم",
    "علي",
    "حسن",
    "عبدالله",
    "سعيد",
    "الشافعي",
    "حماد",
    "فوزي",
    "النجار",
    "خليل",
    "رمضان",
    "عبدالرحمن",
    "سليمان",
    "طه",
    "عثمان",
    "بدر",
    "حسين",
    "أنور",
    "نصر",
]

_GOVERNORATES = [
    "القاهرة",
    "الجيزة",
    "الإسكندرية",
    "المنصورة",
    "طنطا",
    "أسيوط",
    "الزقازيق",
    "بورسعيد",
    "السويس",
    "الفيوم",
]

_EGYPTIAN_ADDRESS = {
    "address_line_1": "١٢ شارع التحرير",
    "city": "القاهرة",
    "state": "القاهرة",
    "postal_code": "11511",
    "country": "EG",
    "country_code": "EG",
    "phone": "+201001234567",
}


@dataclass
class DemoSeedSummary:
    tenant_id: UUID
    store_id: UUID
    products_created: int
    categories_created: int
    customers_created: int
    orders_created: int


class SeedDemoTenantUseCase:
    """Populate a freshly-created demo tenant with realistic sample data."""

    DEMO_SEED_FLAG = "demo_seeded"

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def execute(
        self, tenant_id: UUID, store_id: UUID, niche: str = "fashion"
    ) -> DemoSeedSummary:
        logger.info(
            "demo_seed_started",
            extra={
                "tenant_id": str(tenant_id),
                "store_id": str(store_id),
                "niche": niche,
            },
        )

        now = datetime.now(UTC)

        # ─── 1. Categories ────────────────────────────────────────────
        category_ids = []
        for name, slug, desc_ar in _CATEGORIES:
            cat_id = uuid4()
            category_ids.append(cat_id)
            self.db.add(
                CategoryModel(
                    id=cat_id,
                    tenant_id=tenant_id,
                    store_id=store_id,
                    name=name,
                    slug=slug,
                    description=desc_ar,
                    is_active=True,
                )
            )

        # ─── 2. Products ──────────────────────────────────────────────
        product_ids = []
        product_prices = []
        for name, slug, cat_idx, price, sku, qty, desc in _PRODUCTS:
            prod_id = uuid4()
            product_ids.append(prod_id)
            product_prices.append(price)
            self.db.add(
                ProductModel(
                    id=prod_id,
                    tenant_id=tenant_id,
                    store_id=store_id,
                    category_id=category_ids[cat_idx],
                    name=name,
                    slug=slug,
                    description=desc,
                    price_amount=price,
                    price_currency="EGP",
                    sku=sku,
                    quantity=qty,
                    product_type=ProductType.PHYSICAL,
                    status=ProductStatus.ACTIVE,
                    metadata=DEMO_SEED_FLAG,
                )
            )

        # ─── 3. Customers ─────────────────────────────────────────────
        customer_ids = []
        for i in range(20):
            cust_id = uuid4()
            customer_ids.append(cust_id)
            fn = _FIRST_NAMES[i % len(_FIRST_NAMES)]
            ln = _LAST_NAMES[i % len(_LAST_NAMES)]
            self.db.add(
                CustomerModel(
                    id=cust_id,
                    tenant_id=tenant_id,
                    store_id=store_id,
                    email=f"demo-customer-{i + 1}@demo.numu.local",
                    first_name=fn,
                    last_name=ln,
                    phone=f"+2010{random.randint(10000000, 99999999)}",
                    is_verified=True,
                    total_orders=0,
                    total_spent=0,
                    extra_data=DEMO_SEED_FLAG,
                )
            )

        await self.db.flush()  # get IDs assigned before creating orders

        # ─── 4. Orders (spread across statuses and last 30 days) ──────
        order_statuses = (
            [
                (
                    OrderStatus.PENDING,
                    PaymentStatus.PENDING,
                    FulfillmentStatus.UNFULFILLED,
                )
            ]
            * 8
            + [
                (
                    OrderStatus.CONFIRMED,
                    PaymentStatus.PAID,
                    FulfillmentStatus.UNFULFILLED,
                )
            ]
            * 6
            + [
                (
                    OrderStatus.SHIPPED,
                    PaymentStatus.PAID,
                    FulfillmentStatus.PARTIALLY_FULFILLED,
                )
            ]
            * 8
            + [(OrderStatus.DELIVERED, PaymentStatus.PAID, FulfillmentStatus.FULFILLED)]
            * 6
            + [
                (
                    OrderStatus.CANCELLED,
                    PaymentStatus.REFUNDED,
                    FulfillmentStatus.UNFULFILLED,
                )
            ]
            * 2
        )

        for i, (o_status, p_status, f_status) in enumerate(order_statuses):
            order_id = uuid4()
            customer = random.choice(customer_ids)
            product_idx = random.randint(0, len(product_ids) - 1)
            qty = random.randint(1, 3)
            unit_price = product_prices[product_idx]
            subtotal = unit_price * qty
            shipping = 5000  # 50 EGP flat
            total = subtotal + shipping

            days_ago = random.randint(0, 29)
            order_date = now - timedelta(days=days_ago, hours=random.randint(0, 23))
            gov = random.choice(_GOVERNORATES)

            address = {
                **_EGYPTIAN_ADDRESS,
                "city": gov,
                "state": gov,
                "first_name": _FIRST_NAMES[i % len(_FIRST_NAMES)],
                "last_name": _LAST_NAMES[i % len(_LAST_NAMES)],
            }

            self.db.add(
                OrderModel(
                    id=order_id,
                    tenant_id=tenant_id,
                    store_id=store_id,
                    customer_id=customer,
                    order_number=f"DEMO-{1000 + i}",
                    status=o_status,
                    payment_status=p_status,
                    fulfillment_status=f_status,
                    line_items=[
                        {
                            "product_id": str(product_ids[product_idx]),
                            "name": _PRODUCTS[product_idx][0],
                            "sku": _PRODUCTS[product_idx][4],
                            "quantity": qty,
                            "unit_price": unit_price,
                            "total": subtotal,
                        }
                    ],
                    shipping_address=address,
                    billing_address=address,
                    subtotal=subtotal,
                    shipping_cost=shipping,
                    tax_amount=0,
                    discount_amount=0,
                    total=total,
                    currency="EGP",
                    payment_method="cod",
                    notes="Demo order — auto-generated by SeedDemoTenantUseCase",
                    metadata=DEMO_SEED_FLAG,
                    created_at=order_date,
                    updated_at=order_date,
                )
            )

        await self.db.flush()

        summary = DemoSeedSummary(
            tenant_id=tenant_id,
            store_id=store_id,
            products_created=len(_PRODUCTS),
            categories_created=len(_CATEGORIES),
            customers_created=20,
            orders_created=len(order_statuses),
        )

        logger.info(
            "demo_seed_completed",
            extra={
                "tenant_id": str(tenant_id),
                "products": summary.products_created,
                "categories": summary.categories_created,
                "customers": summary.customers_created,
                "orders": summary.orders_created,
            },
        )
        return summary
