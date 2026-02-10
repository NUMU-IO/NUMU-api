"""Seed beta merchant test accounts with products, customers, and orders.

Usage:
    python -m scripts.seed_beta_merchants          # seed all 10
    python -m scripts.seed_beta_merchants --count 3 # seed first 3 only
    python -m scripts.seed_beta_merchants --dry-run  # print plan, don't write

Requires a running PostgreSQL database configured in .env.
"""

import asyncio
import json
import logging
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("seed_beta")

FIXTURES = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "fixtures"
    / "beta_merchants.json"
)

# Egyptian governorates for random address generation
GOVERNORATES = ["Cairo", "Giza", "Alexandria", "Sharqia", "Dakahlia", "Qalyubia"]
CITIES = [
    "Nasr City",
    "Maadi",
    "Zamalek",
    "Dokki",
    "Mohandessin",
    "Heliopolis",
    "Smouha",
]
STREETS = [
    "Tahrir St.",
    "Abbas El-Akkad",
    "Makram Ebeid",
    "Gameat El-Dowal",
    "El-Horreya Rd.",
]

CUSTOMER_FIRST_NAMES = [
    "Ahmed",
    "Mohamed",
    "Sara",
    "Fatma",
    "Omar",
    "Nour",
    "Ali",
    "Yasmin",
    "Khaled",
    "Dina",
]
CUSTOMER_LAST_NAMES = [
    "Hassan",
    "Ibrahim",
    "Mostafa",
    "Ali",
    "Mahmoud",
    "Salem",
    "Farid",
    "Nabil",
    "Sayed",
    "Kamel",
]


def _random_phone() -> str:
    return (
        f"+201{random.choice(['0', '1', '2', '5'])}{random.randint(10000000, 99999999)}"
    )


def _random_address() -> dict:
    return {
        "country": "EG",
        "governorate": random.choice(GOVERNORATES),
        "city": random.choice(CITIES),
        "street": random.choice(STREETS),
        "building_number": str(random.randint(1, 200)),
    }


async def seed(engine, count: int | None = None, dry_run: bool = False):
    data = json.loads(FIXTURES.read_text(encoding="utf-8"))
    merchants = data["merchants"][:count] if count else data["merchants"]
    password = data["default_password"]

    logger.info("Seeding %d beta merchants...", len(merchants))
    if dry_run:
        for m in merchants:
            logger.info(
                "  [DRY-RUN] %s (%s) — %d products",
                m["name"],
                m["subdomain"],
                len(m["products"]),
            )
        return

    # Hash password once for all test users
    try:
        from passlib.context import CryptContext

        ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        hashed = ctx.hash(password)
    except ImportError:
        import hashlib

        hashed = hashlib.sha256(password.encode()).hexdigest()
        logger.warning("passlib not installed — using SHA-256 (not production-safe)")

    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        for idx, merchant in enumerate(merchants, 1):
            try:
                await _seed_one_merchant(session, merchant, hashed, idx)
                await session.commit()
                logger.info(
                    "[%d/%d] %s — %d products seeded",
                    idx,
                    len(merchants),
                    merchant["name"],
                    len(merchant["products"]),
                )
            except Exception:
                await session.rollback()
                logger.exception("Failed to seed %s", merchant["name"])

    logger.info("Done. %d merchants seeded.", len(merchants))


async def _seed_one_merchant(
    session: AsyncSession,
    merchant: dict,
    hashed_password: str,
    idx: int,
):
    now = datetime.now(UTC)

    # 1. Create user
    user_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO public.users (id, email, hashed_password, first_name, last_name, role, status, email_verified_at, created_at, updated_at)
            VALUES (:id, :email, :pw, :first, :last, 'STORE_OWNER', 'ACTIVE', :now, :now, :now)
            ON CONFLICT (email) DO NOTHING
        """),
        {
            "id": str(user_id),
            "email": merchant["email"],
            "pw": hashed_password,
            "first": f"Beta Merchant {idx}",
            "last": merchant["name"].split()[0],
            "now": now,
        },
    )

    # 2. Create tenant
    tenant_id = uuid4()
    schema_name = f"tenant_{merchant['subdomain'].replace('-', '_')}"
    await session.execute(
        text("""
            INSERT INTO public.tenants (id, name, subdomain, owner_id, plan, is_active, settings, created_at, updated_at)
            VALUES (:id, :name, :sub, :owner, 'beta', true, :settings, :now, :now)
            ON CONFLICT (subdomain) DO NOTHING
        """),
        {
            "id": str(tenant_id),
            "name": merchant["name"],
            "sub": merchant["subdomain"],
            "owner": str(user_id),
            "settings": json.dumps({"schema_name": schema_name}),
            "now": now,
        },
    )

    # 3. Create store
    store_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO public.stores (id, tenant_id, name, slug, subdomain, owner_id, status, default_currency, default_language, created_at, updated_at)
            VALUES (:id, :tid, :name, :slug, :sub, :owner, 'ACTIVE', 'EGP', 'ar', :now, :now)
            ON CONFLICT (slug) DO NOTHING
        """),
        {
            "id": str(store_id),
            "tid": str(tenant_id),
            "name": merchant["name"],
            "slug": merchant["subdomain"],
            "sub": merchant["subdomain"],
            "owner": str(user_id),
            "now": now,
        },
    )

    # 4. Create products
    product_ids = []
    for prod in merchant["products"]:
        pid = uuid4()
        product_ids.append((pid, prod))
        await session.execute(
            text("""
                INSERT INTO public.products (id, tenant_id, store_id, name, slug, price_amount, price_currency, product_type, quantity, low_stock_threshold, status, created_at, updated_at)
                VALUES (:id, :tid, :sid, :name, :slug, :price, 'EGP', 'PHYSICAL', :qty, 10, 'ACTIVE', :now, :now)
                ON CONFLICT DO NOTHING
            """),
            {
                "id": str(pid),
                "tid": str(tenant_id),
                "sid": str(store_id),
                "name": prod["name"],
                "slug": prod["code"].lower(),
                "price": prod["price"],
                "qty": prod["quantity"],
                "now": now,
            },
        )

    # 5. Create customers
    customer_ids = []
    for i in range(min(5, len(CUSTOMER_FIRST_NAMES))):
        cid = uuid4()
        customer_ids.append(cid)
        fname = CUSTOMER_FIRST_NAMES[i]
        lname = random.choice(CUSTOMER_LAST_NAMES)
        await session.execute(
            text("""
                INSERT INTO public.customers (id, tenant_id, store_id, first_name, last_name, email, phone, accepts_marketing, total_orders, total_spent, created_at, updated_at)
                VALUES (:id, :tid, :sid, :fn, :ln, :email, :phone, true, 0, 0, :now, :now)
                ON CONFLICT DO NOTHING
            """),
            {
                "id": str(cid),
                "tid": str(tenant_id),
                "sid": str(store_id),
                "fn": fname,
                "ln": lname,
                "email": f"{fname.lower()}.{lname.lower()}@test.numu.eg",
                "phone": _random_phone(),
                "now": now,
            },
        )

    # 6. Create orders (8 per store with varied statuses)
    statuses = [
        "CONFIRMED",
        "CONFIRMED",
        "SHIPPED",
        "SHIPPED",
        "DELIVERED",
        "DELIVERED",
        "DELIVERED",
        "PENDING",
    ]
    payment_statuses = [
        "PAID",
        "PAID",
        "PAID",
        "PAID",
        "PAID",
        "PAID",
        "PAID",
        "PENDING",
    ]

    for i, (order_status, pay_status) in enumerate(zip(statuses, payment_statuses)):
        oid = uuid4()
        customer = random.choice(customer_ids) if customer_ids else None

        # Pick 1-3 random products
        order_products = random.sample(
            product_ids, min(random.randint(1, 3), len(product_ids))
        )
        line_items = []
        subtotal = 0
        for pid, prod in order_products:
            qty = random.randint(1, 3)
            line_total = prod["price"] * qty
            subtotal += line_total
            line_items.append({
                "product_id": str(pid),
                "product_name": prod["name"],
                "quantity": qty,
                "unit_price": prod["price"],
                "total": line_total,
            })

        tax = int(subtotal * 0.14)
        total = subtotal + tax
        order_date = now - timedelta(days=random.randint(1, 30))

        await session.execute(
            text("""
                INSERT INTO public.orders (
                    id, tenant_id, store_id, customer_id, order_number,
                    status, payment_status, fulfillment_status,
                    line_items, shipping_address,
                    subtotal, shipping_cost, tax_amount, discount_amount, total, currency,
                    created_at, updated_at
                )
                VALUES (
                    :id, :tid, :sid, :cid, :num,
                    :status, :pay_status, :ful_status,
                    :items, :addr,
                    :sub, 0, :tax, 0, :total, 'EGP',
                    :date, :date
                )
                ON CONFLICT DO NOTHING
            """),
            {
                "id": str(oid),
                "tid": str(tenant_id),
                "sid": str(store_id),
                "cid": str(customer) if customer else None,
                "num": f"ORD-{merchant['subdomain'][:4].upper()}-{i + 1:04d}",
                "status": order_status,
                "pay_status": pay_status,
                "ful_status": "FULFILLED"
                if order_status == "DELIVERED"
                else "UNFULFILLED",
                "items": json.dumps(line_items),
                "addr": json.dumps(_random_address()),
                "sub": subtotal,
                "tax": tax,
                "total": total,
                "date": order_date,
            },
        )

    # 7. Add to waitlist as converted
    await session.execute(
        text("""
            INSERT INTO public.waitlist (id, email, name, company_name, status, priority_score, referral_code, invite_code, invited_at, converted_at, source, created_at, updated_at)
            VALUES (:id, :email, :name, :company, 'converted', 500, :ref, :inv, :now, :now, 'beta_seed', :now, :now)
            ON CONFLICT (email) DO NOTHING
        """),
        {
            "id": str(uuid4()),
            "email": merchant["email"],
            "name": f"Beta Merchant {idx}",
            "company": merchant["name"],
            "ref": f"SEED{idx:04d}",
            "inv": f"SEED-INV-{idx:04d}",
            "now": now,
        },
    )


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Seed beta merchant data")
    parser.add_argument(
        "--count", type=int, default=None, help="Number of merchants to seed"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print plan without writing"
    )
    args = parser.parse_args()

    # Load settings
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.config import settings

    engine = create_async_engine(settings.database_url, echo=False)
    asyncio.run(seed(engine, count=args.count, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
