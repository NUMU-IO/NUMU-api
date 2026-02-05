"""Send test emails for all templates in both English and Arabic.

Usage:
    python scripts/test_emails.py <email>
"""

import asyncio
import sys
import time

# Ensure project root is on path
sys.path.insert(0, ".")

from src.config import settings  # noqa: E402
from src.core.interfaces.services.email_service import EmailMessage  # noqa: E402
from src.infrastructure.external_services.resend.email_service import ResendEmailService  # noqa: E402
from src.infrastructure.external_services.resend.email_templates.notifications import (  # noqa: E402
    DELIVERY_CONFIRMATION_TEMPLATE,
    ORDER_CONFIRMATION_TEMPLATE,
    SHIPPING_NOTIFICATION_TEMPLATE,
)
from src.infrastructure.external_services.resend.email_templates.onboarding import (  # noqa: E402
    FIRST_ORDER_RECEIVED_TEMPLATE,
    FIRST_PRODUCT_ADDED_TEMPLATE,
    WELCOME_TEMPLATE,
)

TARGET_EMAIL = sys.argv[1] if len(sys.argv) > 1 else "yousefmansourss290@gmail.com"

SAMPLE_ITEMS = [
    {"name": "Classic White T-Shirt", "quantity": 2, "price": 299.99},
    {"name": "Denim Jacket - Blue", "quantity": 1, "price": 899.00},
    {"name": "Canvas Sneakers", "quantity": 1, "price": 549.50},
]
SAMPLE_TOTAL = sum(i["price"] * i["quantity"] for i in SAMPLE_ITEMS)


async def send(svc: ResendEmailService, label: str, message: EmailMessage) -> None:
    try:
        await svc.send_email(message)
        print(f"  [OK] {label}")
    except Exception as e:
        print(f"  [FAIL] {label}: {e}")


async def main() -> None:
    svc = ResendEmailService()
    print(f"Sending test emails to: {TARGET_EMAIL}")
    print(f"From: {settings.email_from_name} <{settings.email_from_address}>")
    print()

    for lang in ("en", "ar"):
        lang_label = "English" if lang == "en" else "Arabic"
        print(f"--- {lang_label} ({lang}) ---")

        # 1. Order Confirmation
        html = ORDER_CONFIRMATION_TEMPLATE["html_fn"](
            order_number="NUMU-TEST-1001",
            items=SAMPLE_ITEMS,
            total=SAMPLE_TOTAL,
            currency="EGP",
            store_name="Test Store",
            customer_name="Yousef",
            language=lang,
        )
        subject = ORDER_CONFIRMATION_TEMPLATE["subject_fn"]("NUMU-TEST-1001", "Test Store", lang)
        await send(svc, "Order Confirmation", EmailMessage(to=TARGET_EMAIL, subject=subject, html_content=html))
        time.sleep(1)

        # 2. Shipping Notification
        html = SHIPPING_NOTIFICATION_TEMPLATE["html_fn"](
            order_number="NUMU-TEST-1001",
            tracking_number="EG123456789",
            carrier="Bosta",
            store_name="Test Store",
            customer_name="Yousef",
            language=lang,
        )
        subject = SHIPPING_NOTIFICATION_TEMPLATE["subject_fn"]("NUMU-TEST-1001", "Test Store", language=lang)
        await send(svc, "Shipping Notification", EmailMessage(to=TARGET_EMAIL, subject=subject, html_content=html))
        time.sleep(1)

        # 3. Delivery Confirmation
        html = DELIVERY_CONFIRMATION_TEMPLATE["html_fn"](
            order_number="NUMU-TEST-1001",
            store_name="Test Store",
            customer_name="Yousef",
            language=lang,
        )
        subject = DELIVERY_CONFIRMATION_TEMPLATE["subject_fn"]("NUMU-TEST-1001", "Test Store", language=lang)
        await send(svc, "Delivery Confirmation", EmailMessage(to=TARGET_EMAIL, subject=subject, html_content=html))
        time.sleep(1)

        # 4. Welcome (Onboarding)
        html = WELCOME_TEMPLATE["html_fn"](
            merchant_name="Yousef",
            dashboard_url="https://dashboard.numu.io",
            language=lang,
        )
        subject = WELCOME_TEMPLATE["subject"].get(lang, WELCOME_TEMPLATE["subject"]["en"])
        await send(svc, "Welcome", EmailMessage(to=TARGET_EMAIL, subject=subject, html_content=html))
        time.sleep(1)

        # 5. First Product Added (Onboarding)
        html = FIRST_PRODUCT_ADDED_TEMPLATE["html_fn"](
            merchant_name="Yousef",
            product_name="Classic White T-Shirt",
            dashboard_url="https://dashboard.numu.io",
            language=lang,
        )
        subject = FIRST_PRODUCT_ADDED_TEMPLATE["subject"].get(lang, FIRST_PRODUCT_ADDED_TEMPLATE["subject"]["en"])
        await send(svc, "First Product Added", EmailMessage(to=TARGET_EMAIL, subject=subject, html_content=html))
        time.sleep(1)

        # 6. First Order Received (Onboarding)
        html = FIRST_ORDER_RECEIVED_TEMPLATE["html_fn"](
            merchant_name="Yousef",
            order_number="NUMU-TEST-1001",
            total="EGP 2,048.48",
            dashboard_url="https://dashboard.numu.io",
            language=lang,
        )
        subject = FIRST_ORDER_RECEIVED_TEMPLATE["subject"].get(lang, FIRST_ORDER_RECEIVED_TEMPLATE["subject"]["en"])
        await send(svc, "First Order Received", EmailMessage(to=TARGET_EMAIL, subject=subject, html_content=html))
        time.sleep(1)

        print()

    print("Done! Check inbox (and spam folder).")


if __name__ == "__main__":
    asyncio.run(main())
