"""Render every NUMU email template to /email_previews/*.html for visual review."""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.infrastructure.external_services.notifications import email_templates as cfg
from src.infrastructure.external_services.resend.email_templates import (
    beta_invite,
    notifications,
    onboarding,
    transactional,
)
from src.infrastructure.external_services.resend.email_templates._base import (
    header,
    wrap,
)

OUT = ROOT / "email_previews"
OUT.mkdir(exist_ok=True)

# Sample data
STORE = "متجر النيل"
CUSTOMER = "أحمد محمود"
MERCHANT = "محمد علي"
ORDER = "NMU-2026-00347"
ITEMS = [
    {"name": "قميص قطن مصري", "quantity": 2, "price": 450.00},
    {"name": "بنطلون جينز", "quantity": 1, "price": 780.00},
    {"name": "حذاء جلد طبيعي", "quantity": 1, "price": 1250.00},
]
TOTAL = sum(i["price"] * i["quantity"] for i in ITEMS)

previews: dict[str, str] = {}

# 1. Order confirmation
previews["01_order_confirmation_ar"] = notifications.order_confirmation_html(
    order_number=ORDER,
    items=ITEMS,
    total=TOTAL,
    currency="EGP",
    store_name=STORE,
    customer_name=CUSTOMER,
)
previews["01_order_confirmation_en"] = notifications.order_confirmation_html(
    order_number=ORDER,
    items=[{"name": "Egyptian cotton shirt", "quantity": 2, "price": 450.00}],
    total=900,
    store_name="Nile Store",
    customer_name="Ahmed",
    language="en",
)

# 2. Shipped
previews["02_shipped_ar"] = notifications.shipping_notification_html(
    order_number=ORDER,
    tracking_number="TRK-9876543210",
    carrier="بوسطة",
    store_name=STORE,
    customer_name=CUSTOMER,
)

# 3. Delivered
previews["03_delivered_ar"] = notifications.delivery_confirmation_html(
    order_number=ORDER, store_name=STORE, customer_name=CUSTOMER
)

# 4. Confirmed (merchant accepted)
previews["04_confirmed_ar"] = notifications.order_confirmed_html(
    order_number=ORDER, store_name=STORE, customer_name=CUSTOMER
)

# 5. Processing
previews["05_processing_ar"] = notifications.order_processing_html(
    order_number=ORDER, store_name=STORE, customer_name=CUSTOMER
)

# 6. Cancelled
previews["06_cancelled_ar"] = notifications.order_cancelled_html(
    order_number=ORDER,
    store_name=STORE,
    customer_name=CUSTOMER,
    reason="المنتج مش متوفر دلوقتي في المخزن",
)

# 7. Refunded
previews["07_refunded_ar"] = notifications.order_refunded_html(
    order_number=ORDER,
    store_name=STORE,
    customer_name=CUSTOMER,
    reason="تم استلام الطلب وطلب الاسترجاع وفقاً لسياسة المتجر",
)

# 8. Welcome
previews["08_welcome_ar"] = onboarding.welcome_html(
    merchant_name=MERCHANT, dashboard_url="https://numueg.app/dashboard"
)

# 9. First product
previews["09_first_product_ar"] = onboarding.first_product_html(
    merchant_name=MERCHANT,
    product_name="قميص قطن مصري فاخر",
    dashboard_url="https://numueg.app/dashboard",
)

# 10. First order
previews["10_first_order_ar"] = onboarding.first_order_html(
    merchant_name=MERCHANT,
    order_number=ORDER,
    total="EGP 2,930.00",
    dashboard_url="https://numueg.app/dashboard",
)

# 11. Store approved
previews["11_store_approved_ar"] = onboarding.store_approved_html(
    merchant_name=MERCHANT,
    store_name=STORE,
    store_url="https://nile-store.numueg.app",
    dashboard_url="https://numueg.app/dashboard",
)

# 12. Beta invite
previews["12_beta_invite_ar"] = beta_invite.beta_invite_html(
    name=MERCHANT, invite_code="abc123def456ghi789jkl012"
)
previews["12_beta_invite_en"] = beta_invite.beta_invite_html(
    name="Ahmed", invite_code="abc123def456ghi789jkl012", language="en"
)

# 13. Verification email
verify_body = f"""
{header("تأكيد البريد الإلكتروني", "خطوة واحدة وخلصت", language="ar")}
<div class="body">
    <p class="lead">أهلاً بيك في <span class="brand">نُمو</span>،</p>
    <p>دخّل الكود ده في لوحة التحكم عشان تأكّد إيميلك:</p>
    <div class="code-box">
        <p class="digits">428193</p>
        <p class="hint">الكود ده صلاحيته ٢٤ ساعة</p>
    </div>
    <hr class="divider">
    <p>أو اضغط الزرار ده عشان تأكّد على طول:</p>
    <p class="center" style="margin:20px 0;">
        <a href="#" class="btn">تأكيد الإيميل</a>
    </p>
    <p class="muted" style="margin-top:24px;">لو ماعملتش حساب على نُمو، تجاهل الإيميل ده ببساطة.</p>
</div>"""
previews["13_verify_email_ar"] = wrap(
    verify_body, language="ar", preheader="كود تأكيد إيميلك"
)

# 14. Password reset
reset_body = f"""
{header("إعادة تعيين كلمة المرور", "طلبنا تغيير الباسورد", language="ar")}
<div class="body">
    <p class="lead">أهلاً بيك،</p>
    <p>وصلنا طلب لإعادة تعيين كلمة المرور بتاعتك على <span class="brand">نُمو</span>. اضغط على الزرار ده عشان تظبط باسورد جديد:</p>
    <p class="center" style="margin:28px 0;">
        <a href="#" class="btn">إعادة تعيين كلمة المرور</a>
    </p>
    <hr class="divider">
    <p class="muted">اللينك ده صلاحيته ساعة واحدة بس.</p>
    <p class="muted">لو ماطلبتش إعادة تعيين كلمة المرور، تجاهل الإيميل ده وحسابك في أمان.</p>
</div>"""
previews["14_password_reset_ar"] = wrap(reset_body, language="ar")

# 15. Invoice email
invoice_body = f"""
{header("فاتورة ضريبية", STORE, badge="#INV-2026-00045", language="ar")}
<div class="body">
    <p class="lead">أهلاً بيك،</p>
    <p>شكراً لطلبك رقم <strong>#{ORDER}</strong>. مرفق فاتورتك الضريبية رقم <strong>INV-2026-00045</strong>.</p>
    <div class="panel">
        <p style="margin:0; font-size:14px; color:#1A1A2E;">
            📎 الفاتورة مرفقة كملف PDF. تقدر تحمّلها وتحتفظ بيها لسجلاتك.
        </p>
    </div>
    <p class="muted" style="margin-top:24px;">دي فاتورة إلكترونية صادرة وفقاً لمتطلبات مصلحة الضرايب المصرية.</p>
</div>"""
previews["15_invoice_ar"] = wrap(invoice_body, language="ar")

# 16. Configuration request received (merchant)
previews["16_cfg_request_received_ar"] = (
    cfg.ConfigurationRequestEmailTemplate.request_received_merchant(
        merchant_name=MERCHANT,
        service_name="باي موب",
        request_id="req_8a3f9e21-b4c5",
    ).html_body
)

# 17. Credentials ready
previews["17_credentials_ready_ar"] = (
    cfg.CredentialsConfiguredEmailTemplate.credentials_ready(
        merchant_name=MERCHANT,
        service_name="باي موب",
        service_type="payment",
        features=["دفع بكارت ائتمان", "محفظة فودافون كاش", "تقسيط ValU"],
        action_url="https://numueg.app/dashboard/settings/payments",
    ).html_body
)

# 18. Credentials revoked
previews["18_credentials_revoked_ar"] = (
    cfg.CredentialsConfiguredEmailTemplate.credentials_revoked(
        merchant_name=MERCHANT,
        service_name="باي موب",
        reason="انتهت صلاحية مفاتيح الـ API",
    ).html_body
)

# 19a. OTP — order confirmation
previews["19a_otp_order_ar"] = transactional.otp_code_email(
    code="428193", purpose="order", expires_minutes=5
)["html"]

# 19b. OTP — password reset
previews["19b_otp_password_reset_ar"] = transactional.otp_code_email(
    code="729184", purpose="password_reset", expires_minutes=15
)["html"]

# 19c. OTP — phone verification
previews["19c_otp_phone_ar"] = transactional.otp_code_email(
    code="305817", purpose="phone", expires_minutes=5
)["html"]

# 19d. Waitlist welcome
previews["19d_waitlist_welcome_ar"] = transactional.waitlist_welcome_email(
    name=MERCHANT, referral_code="REF-AHMED-2026-XYZ"
)["html"]

# 20. Admin notification (English, kept for internal staff)
previews["20_admin_new_request_en"] = (
    cfg.ConfigurationRequestEmailTemplate.new_request_admin(
        merchant_name="Nile Store",
        service_name="Paymob",
        service_type="payment",
        priority="urgent",
        notes="Customer requested ASAP - ramadan launch coming",
        action_url="https://admin.numueg.app/requests/req_8a3f9e21",
    ).html_body
)

# Write all
for name, html in previews.items():
    (OUT / f"{name}.html").write_text(html, encoding="utf-8")

# Index page
links = "\n".join(
    f'<li><a href="{name}.html" target="preview">{name}</a></li>'
    for name in sorted(previews.keys())
)
index = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>NUMU Email Previews</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; margin: 0; display: flex; height: 100vh; }}
  nav {{ width: 320px; background: #0A1A3D; color: #fff; padding: 24px; overflow-y: auto; }}
  nav h1 {{ font-size: 18px; margin: 0 0 6px; color: #D4AF37; }}
  nav p {{ font-size: 12px; color: rgba(255,255,255,0.6); margin: 0 0 18px; }}
  nav ul {{ list-style: none; padding: 0; margin: 0; }}
  nav li {{ margin: 4px 0; }}
  nav a {{ color: #fff; text-decoration: none; display: block; padding: 8px 12px; border-radius: 6px; font-size: 13px; }}
  nav a:hover {{ background: rgba(212,175,55,0.15); color: #D4AF37; }}
  iframe {{ flex: 1; border: 0; background: #FAF7F0; }}
</style>
</head><body>
<nav>
  <h1>NUMU Email Previews</h1>
  <p>{len(previews)} templates • Egyptian Arabic primary</p>
  <ul>{links}</ul>
</nav>
<iframe name="preview" src="01_order_confirmation_ar.html"></iframe>
</body></html>"""
(OUT / "index.html").write_text(index, encoding="utf-8")

print(f"Wrote {len(previews)} previews to {OUT}")
print(f"Open: {OUT / 'index.html'}")

if "--open" in sys.argv:
    webbrowser.open((OUT / "index.html").as_uri())
