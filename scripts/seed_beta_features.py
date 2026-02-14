"""Seed waitlist & feedback data for beta launch features, and optionally send a beta invite email.

Usage:
    python -m scripts.seed_beta_features                    # seed waitlist + feedback
    python -m scripts.seed_beta_features --invite EMAIL     # also send beta invite to EMAIL
"""

import asyncio
import logging
import random
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("seed_beta_features")

# Waitlist entries to seed (pending — not yet invited)
WAITLIST_ENTRIES = [
    {
        "email": "yahyasheriif@gmail.com",
        "name": "Yahya Sherif",
        "company_name": "Yahya's Store",
        "phone": "+201012345678",
        "source": "direct",
        "priority_score": 900,
    },
    {
        "email": "amina.tech@example.com",
        "name": "Amina Hassan",
        "company_name": "Amina Tech Supplies",
        "phone": "+201155678901",
        "source": "referral",
        "priority_score": 750,
    },
    {
        "email": "tarek.fashion@example.com",
        "name": "Tarek Samir",
        "company_name": "Tarek Fashion House",
        "phone": "+201223456789",
        "source": "landing_page",
        "priority_score": 620,
    },
    {
        "email": "mona.organic@example.com",
        "name": "Mona Adel",
        "company_name": "Mona's Organic Kitchen",
        "phone": "+201098765432",
        "source": "social_media",
        "priority_score": 580,
    },
    {
        "email": "kareem.crafts@example.com",
        "name": "Kareem Youssef",
        "company_name": "Kareem Handmade Crafts",
        "phone": "+201567890123",
        "source": "referral",
        "priority_score": 510,
    },
    {
        "email": "layla.beauty@example.com",
        "name": "Layla Farouk",
        "company_name": "Layla Beauty Essentials",
        "phone": "+201234567890",
        "source": "landing_page",
        "priority_score": 450,
    },
    {
        "email": "hossam.sports@example.com",
        "name": "Hossam Nabil",
        "company_name": "Hossam Sports Gear",
        "phone": "+201187654321",
        "source": "social_media",
        "priority_score": 380,
    },
    {
        "email": "nada.books@example.com",
        "name": "Nada Ibrahim",
        "company_name": "Nada's Book Corner",
        "phone": "+201076543210",
        "source": "direct",
        "priority_score": 320,
    },
]

# Feedback entries to attach to existing beta merchant stores
FEEDBACK_ENTRIES = [
    {
        "category": "usability",
        "rating": 5,
        "title": "Dashboard is very intuitive",
        "body": "Love how easy it is to add products and manage orders. The Arabic support is excellent.",
        "contact_ok": True,
    },
    {
        "category": "feature_request",
        "rating": 4,
        "title": "Need bulk product upload",
        "body": "It would be great to upload products via CSV or Excel file. I have over 200 SKUs to add.",
        "contact_ok": True,
    },
    {
        "category": "bug",
        "rating": 3,
        "title": "Product image upload sometimes fails",
        "body": "When uploading images larger than 2MB the upload hangs. Smaller images work fine.",
        "contact_ok": True,
    },
    {
        "category": "payment",
        "rating": 4,
        "title": "Add Fawry payment option",
        "body": "Many of my customers prefer paying via Fawry. Can this be added as a payment method?",
        "contact_ok": True,
    },
    {
        "category": "performance",
        "rating": 3,
        "title": "Order list loads slowly with many orders",
        "body": "After ~50 orders the orders page takes 4-5 seconds to load. Pagination would help.",
        "contact_ok": False,
    },
    {
        "category": "general",
        "rating": 5,
        "title": "Great platform for Egyptian merchants",
        "body": "Finally a platform that understands the Egyptian market. E-invoicing support is a huge plus!",
        "contact_ok": True,
    },
    {
        "category": "feature_request",
        "rating": 4,
        "title": "WhatsApp order notifications",
        "body": "Would love to get WhatsApp notifications when new orders come in, not just email.",
        "contact_ok": True,
    },
    {
        "category": "usability",
        "rating": 4,
        "title": "Mobile dashboard needs work",
        "body": "The desktop experience is great but the dashboard is hard to use on my phone.",
        "contact_ok": True,
    },
]


async def seed_waitlist(session, now: datetime) -> None:
    """Seed pending waitlist entries."""
    logger.info("Seeding %d waitlist entries...", len(WAITLIST_ENTRIES))

    for i, entry in enumerate(WAITLIST_ENTRIES, 1):
        ref_code = f"REF-{secrets.token_hex(4).upper()}"
        signup_date = now - timedelta(days=random.randint(1, 45))
        await session.execute(
            text("""
                INSERT INTO public.waitlist (
                    id, email, name, company_name, phone,
                    status, priority_score,
                    referral_code, referral_count,
                    source, notes,
                    created_at, updated_at
                )
                VALUES (
                    :id, :email, :name, :company, :phone,
                    'pending', :priority,
                    :ref_code, :ref_count,
                    :source, :notes,
                    :date, :date
                )
                ON CONFLICT (email) DO NOTHING
            """),
            {
                "id": str(uuid4()),
                "email": entry["email"],
                "name": entry["name"],
                "company": entry["company_name"],
                "phone": entry["phone"],
                "priority": entry["priority_score"],
                "ref_code": ref_code,
                "ref_count": random.randint(0, 5),
                "source": entry["source"],
                "notes": None,
                "date": signup_date,
            },
        )
        logger.info(
            "  [%d/%d] %s (%s)", i, len(WAITLIST_ENTRIES), entry["name"], entry["email"]
        )

    await session.commit()
    logger.info("Waitlist seeding done.")


async def seed_feedback(session, now: datetime) -> None:
    """Seed feedback entries from existing beta merchant stores."""
    # Get existing beta merchant store_ids and user_ids
    result = await session.execute(
        text("""
            SELECT s.id AS store_id, s.owner_id AS user_id
            FROM public.stores s
            JOIN public.users u ON u.id = s.owner_id
            WHERE u.role = 'STORE_OWNER'
            LIMIT 10
        """)
    )
    stores = result.fetchall()

    if not stores:
        logger.warning("No stores found — skipping feedback seeding.")
        return

    logger.info(
        "Seeding %d feedback entries across %d stores...",
        len(FEEDBACK_ENTRIES),
        len(stores),
    )

    for i, fb in enumerate(FEEDBACK_ENTRIES):
        store = random.choice(stores)
        fb_date = now - timedelta(days=random.randint(1, 14))
        await session.execute(
            text("""
                INSERT INTO public.feedback (
                    id, store_id, user_id,
                    category, rating, title, body, contact_ok,
                    created_at, updated_at
                )
                VALUES (
                    :id, :store_id, :user_id,
                    :category, :rating, :title, :body, :contact_ok,
                    :date, :date
                )
            """),
            {
                "id": str(uuid4()),
                "store_id": str(store.store_id),
                "user_id": str(store.user_id),
                "category": fb["category"],
                "rating": fb["rating"],
                "title": fb["title"],
                "body": fb["body"],
                "contact_ok": fb["contact_ok"],
                "date": fb_date,
            },
        )
        logger.info(
            "  [%d/%d] %s — %s",
            i + 1,
            len(FEEDBACK_ENTRIES),
            fb["category"],
            fb["title"],
        )

    await session.commit()
    logger.info("Feedback seeding done.")


async def send_beta_invite(email: str) -> None:
    """Send the beta invite email directly via Resend."""
    from src.core.interfaces.services.email_service import EmailMessage
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )
    from src.infrastructure.external_services.resend.email_templates.beta_invite import (
        beta_invite_html,
    )

    invite_code = secrets.token_urlsafe(32)

    svc = ResendEmailService()
    logger.info("Sending beta invite email to %s ...", email)

    await svc.send_email(
        EmailMessage(
            to=email,
            subject="You're invited to NUMU Beta!",
            html_content=beta_invite_html(
                name=email.split("@")[0].replace(".", " ").title(),
                invite_code=invite_code,
            ),
        )
    )
    logger.info(
        "Beta invite sent to %s (invite code: %s)", email, invite_code[:16] + "..."
    )


async def run(engine, invite_email: str | None = None):
    now = datetime.now(UTC)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        await seed_waitlist(session, now)
        await seed_feedback(session, now)

    if invite_email:
        await send_beta_invite(invite_email)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Seed beta launch feature data")
    parser.add_argument(
        "--invite", type=str, default=None, help="Email address to send beta invite to"
    )
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.config import settings

    engine = create_async_engine(settings.database_url, echo=False)
    asyncio.run(run(engine, invite_email=args.invite))


if __name__ == "__main__":
    main()
