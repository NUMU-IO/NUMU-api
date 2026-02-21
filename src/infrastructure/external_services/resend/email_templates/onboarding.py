"""Onboarding email templates for merchant registration flow.

Sent at three milestones:
1. Welcome - on merchant registration
2. First product added
3. First order received

Supports English (en) and Egyptian Arabic (ar) with RTL layout.
"""

_BASE_STYLE_LTR = """
<style>
    body { font-family: Arial, sans-serif; line-height: 1.6; color: #333; margin: 0; }
    .container { max-width: 600px; margin: 0 auto; padding: 0; }
    .header { background: linear-gradient(135deg, #D4AF37, #1034A6); padding: 30px; text-align: center; }
    .header h1 { color: white; margin: 0; font-size: 28px; }
    .header p { color: rgba(255,255,255,0.85); margin: 5px 0 0; }
    .content { padding: 30px; background: #ffffff; }
    .content h2 { color: #1034A6; }
    .step { background: #f8f9fa; border-left: 4px solid #D4AF37; padding: 15px; margin: 15px 0; border-radius: 0 5px 5px 0; }
    .step.done { border-left-color: #28a745; }
    .btn { display: inline-block; padding: 14px 28px; background: #D4AF37; color: #fff; text-decoration: none; border-radius: 5px; font-weight: bold; }
    .footer { padding: 20px; text-align: center; color: #999; font-size: 12px; background: #f5f5f5; }
</style>
"""

_BASE_STYLE_RTL = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Aref+Ruqaa:wght@400;700&family=Cairo:wght@400;600;700&display=swap');
    body { font-family: 'Cairo', 'Segoe UI', Tahoma, Arial, sans-serif; line-height: 1.8; color: #333; margin: 0; direction: rtl; text-align: right; }
    .container { max-width: 600px; margin: 0 auto; padding: 0; }
    .header { background: linear-gradient(135deg, #D4AF37, #1034A6); padding: 30px; text-align: center; }
    .header h1 { color: white; margin: 0; font-size: 28px; }
    .header p { color: rgba(255,255,255,0.85); margin: 5px 0 0; }
    .brand { font-family: 'Aref Ruqaa', 'Traditional Arabic', serif; font-weight: 700; }
    .content { padding: 30px; background: #ffffff; }
    .content h2 { color: #1034A6; }
    .step { background: #f8f9fa; border-right: 4px solid #D4AF37; border-left: none; padding: 15px; margin: 15px 0; border-radius: 5px 0 0 5px; }
    .step.done { border-right-color: #28a745; }
    .btn { display: inline-block; padding: 14px 28px; background: #D4AF37; color: #fff; text-decoration: none; border-radius: 5px; font-weight: bold; }
    .footer { padding: 20px; text-align: center; color: #999; font-size: 12px; background: #f5f5f5; }
</style>
"""


def _wrap(body_html: str, language: str = "en") -> str:
    style = _BASE_STYLE_RTL if language == "ar" else _BASE_STYLE_LTR
    direction = "rtl" if language == "ar" else "ltr"
    footer = (
        '&copy; 2026 <span class="brand">نُمو</span>. جميع الحقوق محفوظة.'
        if language == "ar"
        else "&copy; 2026 NUMU. All rights reserved."
    )
    return f"""<!DOCTYPE html>
<html lang="{language}" dir="{direction}"><head><meta charset="utf-8">{style}</head>
<body><div class="container">{body_html}
<div class="footer"><p>{footer}</p></div>
</div></body></html>"""


# ---------------------------------------------------------------------------
# 1. Welcome email
# ---------------------------------------------------------------------------

_WELCOME = {
    "en": {
        "title": "Welcome to NUMU!",
        "subtitle": "Your journey starts here",
        "greeting": "Hi {merchant_name},",
        "intro": "Congratulations on creating your NUMU store! You're now part of Egypt's fastest-growing e-commerce platform.",
        "next_steps": "Next Steps",
        "step_label": "Step",
        "steps": [
            "Add your first product to your store",
            "Configure your payment methods (Paymob, Fawry, COD)",
            "Set up shipping with Bosta",
            "Share your store link and start selling!",
        ],
        "btn": "Go to Dashboard",
        "help": "Need help? Our support team is here for you.",
    },
    "ar": {
        "title": 'أهلاً بيك في <span class="brand">نُمو</span>!',
        "subtitle": "رحلتك بتبدأ من هنا",
        "greeting": "أهلاً {merchant_name}،",
        "intro": 'مبروك على إنشاء متجرك على <span class="brand">نُمو</span>! انت دلوقتي جزء من أسرع منصة تجارة إلكترونية في مصر.',
        "next_steps": "الخطوات الجاية",
        "step_label": "الخطوة",
        "steps": [
            "أضف أول منتج في متجرك",
            "اظبط طرق الدفع (باي موب، فوري، الدفع عند الاستلام)",
            "جهّز الشحن مع بوسطة",
            "شارك لينك متجرك وابدأ البيع!",
        ],
        "btn": "روح للوحة التحكم",
        "help": "محتاج مساعدة؟ فريق الدعم موجود عشانك.",
    },
}


def welcome_html(merchant_name: str, dashboard_url: str, language: str = "en") -> str:
    c = _WELCOME.get(language, _WELCOME["en"])
    steps = "".join(
        f'<div class="step"><strong>{c["step_label"]} {i + 1}:</strong> {s}</div>'
        for i, s in enumerate(c["steps"])
    )
    return _wrap(
        f"""
    <div class="header"><h1>{c["title"]}</h1><p>{c["subtitle"]}</p></div>
    <div class="content">
        <p>{c["greeting"].format(merchant_name=merchant_name)}</p>
        <p>{c["intro"]}</p>
        <h2>{c["next_steps"]}</h2>
        {steps}
        <p style="text-align:center; margin-top:25px;">
            <a href="{dashboard_url}" class="btn">{c["btn"]}</a>
        </p>
        <p>{c["help"]}</p>
    </div>""",
        language=language,
    )


WELCOME_TEMPLATE = {
    "subject": {
        "en": "Welcome to NUMU - Let's Build Your Store!",
        "ar": "أهلاً بيك في نُمو - يلا نبني متجرك!",
    },
    "html_fn": welcome_html,
}


# ---------------------------------------------------------------------------
# 2. First product added
# ---------------------------------------------------------------------------

_FIRST_PRODUCT = {
    "en": {
        "title": "Your First Product is Live!",
        "greeting": "Hi {merchant_name},",
        "intro": "Great job! You just added <strong>{product_name}</strong> to your store.",
        "progress": "Your Progress",
        "step_label": "Step",
        "step1_done": "&#10003; Step 1: Add your first product - <em>Done!</em>",
        "step2": "Step 2: Configure payment methods",
        "step3": "Step 3: Set up shipping",
        "step4": "Step 4: Share your store and start selling",
        "btn": "Add More Products",
    },
    "ar": {
        "title": "!أول منتج ليك اتنشر",
        "greeting": "أهلاً {merchant_name}،",
        "intro": "شغل جامد! أضفت <strong>{product_name}</strong> في متجرك.",
        "progress": "تقدمك",
        "step_label": "الخطوة",
        "step1_done": "&#10003; الخطوة ١: أضف أول منتج - <em>تم!</em>",
        "step2": "الخطوة ٢: اظبط طرق الدفع",
        "step3": "الخطوة ٣: جهّز الشحن",
        "step4": "الخطوة ٤: شارك متجرك وابدأ البيع",
        "btn": "أضف منتجات تانية",
    },
}


def first_product_html(
    merchant_name: str, product_name: str, dashboard_url: str, language: str = "en"
) -> str:
    c = _FIRST_PRODUCT.get(language, _FIRST_PRODUCT["en"])
    return _wrap(
        f"""
    <div class="header"><h1>{c["title"]}</h1></div>
    <div class="content">
        <p>{c["greeting"].format(merchant_name=merchant_name)}</p>
        <p>{c["intro"].format(product_name=product_name)}</p>
        <h2>{c["progress"]}</h2>
        <div class="step done">{c["step1_done"]}</div>
        <div class="step"><strong>{c["step2"]}</strong></div>
        <div class="step"><strong>{c["step3"]}</strong></div>
        <div class="step"><strong>{c["step4"]}</strong></div>
        <p style="text-align:center; margin-top:25px;">
            <a href="{dashboard_url}/products" class="btn">{c["btn"]}</a>
        </p>
    </div>""",
        language=language,
    )


FIRST_PRODUCT_ADDED_TEMPLATE = {
    "subject": {
        "en": "Your First Product is Live on NUMU!",
        "ar": "أول منتج ليك اتنشر على نُمو!",
    },
    "html_fn": first_product_html,
}


# ---------------------------------------------------------------------------
# 3. First order received
# ---------------------------------------------------------------------------

_FIRST_ORDER = {
    "en": {
        "title": "You Got Your First Order!",
        "greeting": "Hi {merchant_name},",
        "intro": "This is a big milestone! Your store just received its first order.",
        "order_label": "Order",
        "progress": "Your Progress",
        "step1_done": "&#10003; Add your first product",
        "step2_done": "&#10003; Receive your first order",
        "cta_text": "Head to your dashboard to process this order:",
        "btn": "View Order",
    },
    "ar": {
        "title": "!جالك أول أوردر",
        "greeting": "أهلاً {merchant_name}،",
        "intro": "دي خطوة كبيرة! متجرك استلم أول أوردر.",
        "order_label": "أوردر",
        "progress": "تقدمك",
        "step1_done": "&#10003; أضفت أول منتج",
        "step2_done": "&#10003; استلمت أول أوردر",
        "cta_text": "روح للوحة التحكم عشان تجهّز الأوردر:",
        "btn": "عرض الأوردر",
    },
}


def first_order_html(
    merchant_name: str,
    order_number: str,
    total: str,
    dashboard_url: str,
    language: str = "en",
) -> str:
    c = _FIRST_ORDER.get(language, _FIRST_ORDER["en"])
    border_side = "border-right-color" if language == "ar" else "border-left-color"
    return _wrap(
        f"""
    <div class="header"><h1>{c["title"]}</h1></div>
    <div class="content">
        <p>{c["greeting"].format(merchant_name=merchant_name)}</p>
        <p>{c["intro"]}</p>

        <div class="step done" style="{border_side}: #D4AF37;">
            <p style="margin:0; font-size: 18px;"><strong>{c["order_label"]} #{order_number}</strong></p>
            <p style="margin:5px 0 0; font-size: 22px; color: #D4AF37;"><strong>{total}</strong></p>
        </div>

        <h2>{c["progress"]}</h2>
        <div class="step done">{c["step1_done"]}</div>
        <div class="step done">{c["step2_done"]}</div>

        <p>{c["cta_text"]}</p>
        <p style="text-align:center; margin-top:15px;">
            <a href="{dashboard_url}/orders" class="btn">{c["btn"]}</a>
        </p>
    </div>""",
        language=language,
    )


FIRST_ORDER_RECEIVED_TEMPLATE = {
    "subject": {
        "en": "You Got Your First Order on NUMU!",
        "ar": "جالك أول أوردر على نُمو!",
    },
    "html_fn": first_order_html,
}


# ---------------------------------------------------------------------------
# 4. Store approved
# ---------------------------------------------------------------------------

_STORE_APPROVED = {
    "en": {
        "title": "Your Store is Live!",
        "subtitle": "Time to start selling",
        "greeting": "Hi {merchant_name},",
        "intro": "Great news! Your store <strong>{store_name}</strong> has been reviewed and approved. It's now live and ready for customers.",
        "store_url_label": "Your store URL:",
        "next_steps": "What's Next?",
        "steps": [
            "Add products to your store",
            "Set up your payment methods",
            "Customize your store's look and feel",
            "Share your store link on social media",
        ],
        "step_label": "Step",
        "btn": "Go to Dashboard",
        "help": "Need help getting started? Our support team is here for you.",
    },
    "ar": {
        "title": "متجرك اتفعّل!",
        "subtitle": "وقت البيع",
        "greeting": "أهلاً {merchant_name}،",
        "intro": "خبر حلو! متجرك <strong>{store_name}</strong> اتراجع واتقبل. المتجر شغّال دلوقتي ومستني الزباين.",
        "store_url_label": "لينك متجرك:",
        "next_steps": "إيه الخطوة الجاية؟",
        "steps": [
            "أضف منتجات في متجرك",
            "اظبط طرق الدفع",
            "خصّص شكل متجرك",
            "شارك لينك متجرك على السوشيال ميديا",
        ],
        "step_label": "الخطوة",
        "btn": "روح للوحة التحكم",
        "help": "محتاج مساعدة؟ فريق الدعم موجود عشانك.",
    },
}


def store_approved_html(
    merchant_name: str,
    store_name: str,
    store_url: str,
    dashboard_url: str,
    language: str = "en",
) -> str:
    c = _STORE_APPROVED.get(language, _STORE_APPROVED["en"])
    steps = "".join(
        f'<div class="step"><strong>{c["step_label"]} {i + 1}:</strong> {s}</div>'
        for i, s in enumerate(c["steps"])
    )
    return _wrap(
        f"""
    <div class="header"><h1>{c["title"]}</h1><p>{c["subtitle"]}</p></div>
    <div class="content">
        <p>{c["greeting"].format(merchant_name=merchant_name)}</p>
        <p>{c["intro"].format(store_name=store_name)}</p>
        <div class="step done">
            <strong>{c["store_url_label"]}</strong><br>
            <a href="{store_url}" style="color: #1034A6; font-size: 16px;">{store_url}</a>
        </div>
        <h2>{c["next_steps"]}</h2>
        {steps}
        <p style="text-align:center; margin-top:25px;">
            <a href="{dashboard_url}" class="btn">{c["btn"]}</a>
        </p>
        <p>{c["help"]}</p>
    </div>""",
        language=language,
    )


STORE_APPROVED_TEMPLATE = {
    "subject": {
        "en": "Your NUMU Store is Live!",
        "ar": "متجرك على نُمو اتفعّل!",
    },
    "html_fn": store_approved_html,
}
