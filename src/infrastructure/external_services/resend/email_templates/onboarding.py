"""Onboarding email templates for merchant registration flow.

Sent at four milestones:
1. Welcome — on merchant registration
2. First product added
3. First order received
4. Store approved (live)

Egyptian Arabic ("ar") is the default. Brand chrome lives in `_base`.
"""

from src.infrastructure.external_services.resend.email_templates._base import (
    GOLD,
    header,
    wrap,
)

# ─────────────────────────────────────────────────────────────────────────
# 1. Welcome
# ─────────────────────────────────────────────────────────────────────────

_WELCOME = {
    "ar": {
        "title": 'أهلاً بيك في <span class="brand">نُمو</span>',
        "subtitle": "رحلتك مع التجارة الإلكترونية بتبدأ من هنا",
        "greeting": "أهلاً {merchant_name}،",
        "intro": 'مبروك على إنشاء متجرك على <span class="brand">نُمو</span>! انت دلوقتي جزء من أسرع منصة تجارة إلكترونية في مصر.',
        "next_steps": "الخطوات الجاية",
        "step_label": "الخطوة",
        "steps": [
            "ضيف أول منتج في متجرك",
            "اظبط طرق الدفع (باي موب، فوري، الدفع عند الاستلام)",
            "جهّز الشحن مع بوسطة",
            "شارك لينك متجرك وابدأ البيع",
        ],
        "btn": "روح للوحة التحكم",
        "help": "محتاج مساعدة؟ فريق الدعم موجود عشانك في أي وقت.",
        "preheader": "أهلاً بيك في نُمو — يلا نبني متجرك",
    },
    "en": {
        "title": "Welcome to NUMU",
        "subtitle": "Your e-commerce journey starts here",
        "greeting": "Hi {merchant_name},",
        "intro": "Congratulations on creating your NUMU store! You're now part of Egypt's fastest-growing e-commerce platform.",
        "next_steps": "Next Steps",
        "step_label": "Step",
        "steps": [
            "Add your first product to your store",
            "Configure payment methods (Paymob, Fawry, COD)",
            "Set up shipping with Bosta",
            "Share your store link and start selling",
        ],
        "btn": "Go to Dashboard",
        "help": "Need help? Our support team is here for you.",
        "preheader": "Welcome to NUMU — let's build your store",
    },
}


def welcome_html(merchant_name: str, dashboard_url: str, language: str = "ar") -> str:
    c = _WELCOME.get(language, _WELCOME["ar"])
    steps = "".join(
        f'<div class="panel" style="margin:12px 0; padding:16px 20px;">'
        f'<p class="label">{c["step_label"]} {i + 1}</p>'
        f'<p style="margin:4px 0 0; font-size:15px; color:#1A1A2E;">{s}</p>'
        f"</div>"
        for i, s in enumerate(c["steps"])
    )
    body = f"""
    {header(c["title"], c["subtitle"], language=language)}
    <div class="body">
        <p class="lead">{c["greeting"].format(merchant_name=merchant_name)}</p>
        <p>{c["intro"]}</p>

        <h2>{c["next_steps"]}</h2>
        {steps}

        <p class="center" style="margin-top:30px;">
            <a href="{dashboard_url}" class="btn">{c["btn"]}</a>
        </p>
        <p class="muted" style="margin-top:24px;">{c["help"]}</p>
    </div>"""
    return wrap(body, language=language, preheader=c["preheader"])


WELCOME_TEMPLATE = {
    "subject": {
        "ar": "أهلاً بيك في نُمو — يلا نبني متجرك",
        "en": "Welcome to NUMU — Let's Build Your Store",
    },
    "html_fn": welcome_html,
}


# ─────────────────────────────────────────────────────────────────────────
# 2. First product added
# ─────────────────────────────────────────────────────────────────────────

_FIRST_PRODUCT = {
    "ar": {
        "title": "أول منتج ليك اتنشر",
        "subtitle": "متجرك بدأ يكبر",
        "greeting": "أهلاً {merchant_name}،",
        "intro": "شغل جامد! ضفت <strong>{product_name}</strong> في متجرك.",
        "progress": "تقدمك",
        "step1_done": "&#10003; ضفت أول منتج",
        "step2": "اظبط طرق الدفع",
        "step3": "جهّز الشحن",
        "step4": "شارك متجرك وابدأ البيع",
        "btn": "ضيف منتجات تانية",
        "preheader": "أول منتج اتنشر على متجرك",
    },
    "en": {
        "title": "Your First Product is Live",
        "subtitle": "Your store is taking shape",
        "greeting": "Hi {merchant_name},",
        "intro": "Great job! You just added <strong>{product_name}</strong> to your store.",
        "progress": "Your Progress",
        "step1_done": "&#10003; Added your first product",
        "step2": "Configure payment methods",
        "step3": "Set up shipping",
        "step4": "Share your store and start selling",
        "btn": "Add More Products",
        "preheader": "Your first product is now live",
    },
}


def first_product_html(
    merchant_name: str, product_name: str, dashboard_url: str, language: str = "ar"
) -> str:
    c = _FIRST_PRODUCT.get(language, _FIRST_PRODUCT["ar"])
    body = f"""
    {header(c["title"], c["subtitle"], language=language)}
    <div class="body">
        <p class="lead">{c["greeting"].format(merchant_name=merchant_name)}</p>
        <p>{c["intro"].format(product_name=product_name)}</p>

        <h2>{c["progress"]}</h2>
        <div class="steps">
            <div class="step">
                <div class="step-dot done">&#10003;</div>
                <div class="step-text"><p class="title">{c["step1_done"]}</p></div>
            </div>
            <div class="step-line pending"></div>
            <div class="step">
                <div class="step-dot active">2</div>
                <div class="step-text"><p class="title">{c["step2"]}</p></div>
            </div>
            <div class="step-line pending"></div>
            <div class="step">
                <div class="step-dot pending">3</div>
                <div class="step-text"><p class="title">{c["step3"]}</p></div>
            </div>
            <div class="step-line pending"></div>
            <div class="step">
                <div class="step-dot pending">4</div>
                <div class="step-text"><p class="title">{c["step4"]}</p></div>
            </div>
        </div>

        <p class="center" style="margin-top:28px;">
            <a href="{dashboard_url}/products" class="btn">{c["btn"]}</a>
        </p>
    </div>"""
    return wrap(body, language=language, preheader=c["preheader"])


FIRST_PRODUCT_ADDED_TEMPLATE = {
    "subject": {
        "ar": "أول منتج ليك اتنشر على نُمو",
        "en": "Your First Product is Live on NUMU",
    },
    "html_fn": first_product_html,
}


# ─────────────────────────────────────────────────────────────────────────
# 3. First order received
# ─────────────────────────────────────────────────────────────────────────

_FIRST_ORDER = {
    "ar": {
        "title": "جالك أول أوردر",
        "subtitle": "خطوة كبيرة في رحلتك",
        "greeting": "أهلاً {merchant_name}،",
        "intro": "دي خطوة كبيرة! متجرك لسه استلم أول أوردر.",
        "order_label": "رقم الأوردر",
        "progress": "تقدمك",
        "step1_done": "&#10003; ضفت أول منتج",
        "step2_done": "&#10003; استلمت أول أوردر",
        "cta_text": "روح للوحة التحكم عشان تجهّز الأوردر:",
        "btn": "عرض الأوردر",
        "preheader": "جالك أول أوردر على نُمو",
    },
    "en": {
        "title": "You Got Your First Order",
        "subtitle": "A big milestone in your journey",
        "greeting": "Hi {merchant_name},",
        "intro": "This is a big milestone! Your store just received its first order.",
        "order_label": "Order",
        "progress": "Your Progress",
        "step1_done": "&#10003; Added your first product",
        "step2_done": "&#10003; Received your first order",
        "cta_text": "Head to your dashboard to process this order:",
        "btn": "View Order",
        "preheader": "Your first order has arrived",
    },
}


def first_order_html(
    merchant_name: str,
    order_number: str,
    total: str,
    dashboard_url: str,
    language: str = "ar",
) -> str:
    c = _FIRST_ORDER.get(language, _FIRST_ORDER["ar"])
    body = f"""
    {header(c["title"], c["subtitle"], language=language)}
    <div class="body">
        <p class="lead">{c["greeting"].format(merchant_name=merchant_name)}</p>
        <p>{c["intro"]}</p>

        <div class="panel" style="text-align:center;">
            <p class="label">{c["order_label"]}</p>
            <p class="value" style="font-size:20px;">#{order_number}</p>
            <p style="margin:10px 0 0; font-size:26px; color:{GOLD}; font-weight:700; direction:ltr;">{total}</p>
        </div>

        <h2>{c["progress"]}</h2>
        <div class="steps">
            <div class="step">
                <div class="step-dot done">&#10003;</div>
                <div class="step-text"><p class="title">{c["step1_done"]}</p></div>
            </div>
            <div class="step-line done"></div>
            <div class="step">
                <div class="step-dot done">&#10003;</div>
                <div class="step-text"><p class="title">{c["step2_done"]}</p></div>
            </div>
        </div>

        <p>{c["cta_text"]}</p>
        <p class="center" style="margin-top:18px;">
            <a href="{dashboard_url}/orders" class="btn">{c["btn"]}</a>
        </p>
    </div>"""
    return wrap(body, language=language, preheader=c["preheader"])


FIRST_ORDER_RECEIVED_TEMPLATE = {
    "subject": {
        "ar": "جالك أول أوردر على نُمو",
        "en": "You Got Your First Order on NUMU",
    },
    "html_fn": first_order_html,
}


# ─────────────────────────────────────────────────────────────────────────
# 4. Store approved
# ─────────────────────────────────────────────────────────────────────────

_STORE_APPROVED = {
    "ar": {
        "title": "متجرك اتفعّل",
        "subtitle": "وقت البيع وصل",
        "greeting": "أهلاً {merchant_name}،",
        "intro": "خبر حلو! متجرك <strong>{store_name}</strong> اتراجع واتقبل. المتجر بقى شغّال ومستني الزباين.",
        "store_url_label": "لينك متجرك",
        "next_steps": "إيه الخطوة الجاية؟",
        "steps": [
            "ضيف منتجات في متجرك",
            "اظبط طرق الدفع",
            "خصّص شكل متجرك",
            "شارك لينك متجرك على السوشيال ميديا",
        ],
        "step_label": "الخطوة",
        "btn": "روح للوحة التحكم",
        "help": "محتاج مساعدة في البداية؟ فريق الدعم موجود عشانك.",
        "preheader": "متجرك اتفعّل وبقى شغّال",
    },
    "en": {
        "title": "Your Store is Live",
        "subtitle": "Time to start selling",
        "greeting": "Hi {merchant_name},",
        "intro": "Great news! Your store <strong>{store_name}</strong> has been reviewed and approved. It's now live and ready for customers.",
        "store_url_label": "Your store URL",
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
        "preheader": "Your NUMU store is live",
    },
}


def store_approved_html(
    merchant_name: str,
    store_name: str,
    store_url: str,
    dashboard_url: str,
    language: str = "ar",
) -> str:
    c = _STORE_APPROVED.get(language, _STORE_APPROVED["ar"])
    steps = "".join(
        f'<div class="panel" style="margin:12px 0; padding:16px 20px;">'
        f'<p class="label">{c["step_label"]} {i + 1}</p>'
        f'<p style="margin:4px 0 0; font-size:15px; color:#1A1A2E;">{s}</p>'
        f"</div>"
        for i, s in enumerate(c["steps"])
    )
    body = f"""
    {header(c["title"], c["subtitle"], language=language)}
    <div class="body">
        <p class="lead">{c["greeting"].format(merchant_name=merchant_name)}</p>
        <p>{c["intro"].format(store_name=store_name)}</p>

        <div class="panel" style="text-align:center;">
            <p class="label">{c["store_url_label"]}</p>
            <p style="margin:6px 0 0;"><a href="{store_url}" style="font-size:16px; word-break:break-all;">{store_url}</a></p>
        </div>

        <h2>{c["next_steps"]}</h2>
        {steps}

        <p class="center" style="margin-top:28px;">
            <a href="{dashboard_url}" class="btn">{c["btn"]}</a>
        </p>
        <p class="muted" style="margin-top:24px;">{c["help"]}</p>
    </div>"""
    return wrap(body, language=language, preheader=c["preheader"])


STORE_APPROVED_TEMPLATE = {
    "subject": {
        "ar": "متجرك على نُمو اتفعّل",
        "en": "Your NUMU Store is Live",
    },
    "html_fn": store_approved_html,
}
