"""Canonical definitions of the rich (Bosta-style) WhatsApp system templates.

Single source of truth for the template **bodies / footers / buttons** shared
by two consumers so they can never drift:

1. The seed migration (``..._seed_rich_wa_templates``) — inserts the local
   ``whatsapp_templates`` mirror rows (``body_text`` / ``footer_text`` /
   ``buttons``) used by the merchant-hub preview + the send-guard's status check.
2. The platform-WABA submission script (``scripts/submit_platform_whatsapp_templates.py``)
   — POSTs these to Meta via ``TemplateClient.create_template``.

The positional ``{{n}}`` placeholders here MUST match the parameter ORDER of the
matching entry in ``EGYPTIAN_TEMPLATES``
(``src/core/interfaces/services/messaging_service.py``) — that mapping is what
fills the placeholders at send time.

WhatsApp body markdown: ``*bold*``, ``_italic_``, newlines. Rules respected:
body never starts/ends on a variable; no two adjacent variables. Footer is
plain text (≤60 chars, no variables). ``button`` payloads are set at send time,
not here — the ``QUICK_REPLY`` button definitions only carry the display text.
"""

# Apex redirector targets for URL buttons (see routes/order_redirect.py).
_TRACK_URL = "https://numueg.app/o/{{1}}"
_CART_URL = "https://numueg.app/cart/{{1}}"
# COD-recovery pay deep-link (apex → tenant /pay redirect; see the recover-flow
# spec). Suffix is "<subdomain>/<order_id>".
_PAY_URL = "https://numueg.app/pay/{{1}}"


# Each entry: name, language, category, body, footer, buttons (Meta format).
# ``body_examples`` give Meta realistic preview values (one per {{n}}), which
# improves the approval pass-rate and mirrors the merchant-hub sample preview.
RICH_TEMPLATES: list[dict] = [
    # 1. COD confirm-request — 7 vars + 3 quick-reply buttons. UTILITY.
    {
        "name": "order_confirmation_request_v2",
        "language": "en_US",
        "category": "UTILITY",
        "body": (
            "Hi {{1}} 👋 Thanks for your order from *{{2}}*. Please review the "
            "details and tap a button below.\n\n"
            "📦 *Order details*\n\n"
            "🧾 Order number: {{3}}\n"
            "💰 Total: {{4}}\n"
            "💵 Payment: {{5}}\n"
            "🛍️ Items: {{6}}\n"
            "🏡 Delivery to: {{7}}\n\n"
            "Tap *Confirm Order* and we'll start preparing it right away. 🙌"
        ),
        "footer": "You can change this anytime — just reply here.",
        # NOTE: Meta forbids emojis / newlines / formatting / variables in
        # button TEXT (error 2388060). Keep button labels plain — emojis live
        # in the body only.
        "buttons": [
            {"type": "QUICK_REPLY", "text": "Confirm Order"},
            {"type": "QUICK_REPLY", "text": "Postpone delivery"},
            {"type": "QUICK_REPLY", "text": "Cancel Order"},
        ],
        "body_examples": [
            "Ahmed",
            "Cairo Style",
            "ORD-000032",
            "EGP 250.00",
            "Cash on delivery",
            "2",
            "12 Tahrir St, Cairo",
        ],
    },
    {
        "name": "order_confirmation_request_v2",
        "language": "ar",
        "category": "UTILITY",
        "body": (
            "أهلاً يا {{1}} 👋 شكراً لطلبك من *{{2}}*. راجع التفاصيل واضغط أحد "
            "الأزرار تحت.\n\n"
            "📦 *تفاصيل الطلب*\n\n"
            "🧾 رقم الطلب: {{3}}\n"
            "💰 الإجمالي: {{4}}\n"
            "💵 طريقة الدفع: {{5}}\n"
            "🛍️ عدد المنتجات: {{6}}\n"
            "🏡 التوصيل إلى: {{7}}\n\n"
            "اضغط *تأكيد الأوردر* وهنبدأ التحضير على طول. 🙌"
        ),
        "footer": "تقدر تغيّر ده في أي وقت — رد هنا.",
        "buttons": [
            {"type": "QUICK_REPLY", "text": "تأكيد الأوردر"},
            {"type": "QUICK_REPLY", "text": "تأجيل التسليم"},
            {"type": "QUICK_REPLY", "text": "إلغاء الأوردر"},
        ],
        "body_examples": [
            "أحمد",
            "متجر القاهرة",
            "ORD-000032",
            "EGP 250.00",
            "الدفع عند الاستلام",
            "٢",
            "١٢ شارع التحرير، القاهرة",
        ],
    },
    # 2. Passive order received — 5 vars + Track URL button. UTILITY.
    {
        "name": "order_confirmation_v3",
        "language": "en_US",
        "category": "UTILITY",
        "body": (
            "Hi {{1}} 👋 We've received your order from *{{2}}* — thank you! 🎉\n\n"
            "📦 *Order summary*\n\n"
            "🧾 Order number: {{3}}\n"
            "💰 Total: {{4}}\n"
            "💵 Payment: {{5}}\n\n"
            "Tap *Track order* below to follow it anytime."
        ),
        "footer": "Thank you for shopping with us.",
        "buttons": [
            {
                "type": "URL",
                "text": "Track order",
                "url": _TRACK_URL,
                "example": ["https://numueg.app/o/cairo-style/abc123"],
            }
        ],
        "body_examples": [
            "Ahmed",
            "Cairo Style",
            "ORD-000032",
            "EGP 250.00",
            "Cash on delivery",
        ],
    },
    {
        "name": "order_confirmation_v3",
        "language": "ar",
        "category": "UTILITY",
        "body": (
            "أهلاً يا {{1}} 👋 استلمنا طلبك من *{{2}}* — شكراً ليك! 🎉\n\n"
            "📦 *ملخص الطلب*\n\n"
            "🧾 رقم الطلب: {{3}}\n"
            "💰 الإجمالي: {{4}}\n"
            "💵 طريقة الدفع: {{5}}\n\n"
            "اضغط *تتبع الطلب* تحت عشان تتابعه في أي وقت."
        ),
        "footer": "شكراً لتسوقك معنا.",
        "buttons": [
            {
                "type": "URL",
                "text": "تتبع الطلب",
                "url": _TRACK_URL,
                "example": ["https://numueg.app/o/cairo-style/abc123"],
            }
        ],
        "body_examples": [
            "أحمد",
            "متجر القاهرة",
            "ORD-000032",
            "EGP 250.00",
            "الدفع عند الاستلام",
        ],
    },
    # 3. Shipped — 4 vars + Track URL button. UTILITY.
    {
        "name": "order_shipped_v3",
        "language": "en",
        "category": "UTILITY",
        "body": (
            "Good news {{1}}! 🚚 Your order {{2}} is on its way.\n\n"
            "📦 *Shipping details*\n\n"
            "🚛 Carrier: {{3}}\n"
            "🔖 Tracking number: {{4}}\n\n"
            "Tap *Track order* below to follow your delivery."
        ),
        "footer": "Thank you for your patience.",
        "buttons": [
            {
                "type": "URL",
                "text": "Track order",
                "url": _TRACK_URL,
                "example": ["https://numueg.app/o/cairo-style/abc123"],
            }
        ],
        "body_examples": ["Ahmed", "ORD-000032", "Bosta", "51338085"],
    },
    {
        "name": "order_shipped_v3",
        "language": "ar",
        "category": "UTILITY",
        "body": (
            "أخبار حلوة يا {{1}}! 🚚 طلبك رقم {{2}} في الطريق إليك.\n\n"
            "📦 *تفاصيل الشحن*\n\n"
            "🚛 شركة الشحن: {{3}}\n"
            "🔖 رقم التتبع: {{4}}\n\n"
            "اضغط *تتبع الطلب* تحت عشان تتابع شحنتك."
        ),
        "footer": "شكراً لصبرك.",
        "buttons": [
            {
                "type": "URL",
                "text": "تتبع الطلب",
                "url": _TRACK_URL,
                "example": ["https://numueg.app/o/cairo-style/abc123"],
            }
        ],
        "body_examples": ["أحمد", "ORD-000032", "بوسطة", "51338085"],
    },
    # 4. Delivered — 3 vars, no buttons. UTILITY.
    {
        "name": "order_delivered_v2",
        "language": "en",
        "category": "UTILITY",
        "body": (
            "Hi {{1}}! ✅ Your order {{2}} has been delivered.\n\n"
            "Thank you for shopping with *{{3}}* 🛍️ We hope you love it. 💚"
        ),
        "footer": "We'd love your feedback!",
        "buttons": [],
        "body_examples": ["Ahmed", "ORD-000032", "Cairo Style"],
    },
    {
        "name": "order_delivered_v2",
        "language": "ar",
        "category": "UTILITY",
        "body": (
            "أهلاً يا {{1}}! ✅ تم تسليم طلبك رقم {{2}}.\n\n"
            "شكراً لتسوقك من *{{3}}* 🛍️ نتمنى ينال إعجابك. 💚"
        ),
        "footer": "رأيك يهمنا!",
        "buttons": [],
        "body_examples": ["أحمد", "ORD-000032", "متجر القاهرة"],
    },
    # 5. Payment received — 3 vars, no buttons. UTILITY.
    {
        "name": "payment_received_v2",
        "language": "en",
        "category": "UTILITY",
        "body": (
            "Hi {{1}} 👋 We've received your payment. ✅\n\n"
            "🧾 Order: {{2}}\n"
            "💰 Amount paid: {{3}}\n\n"
            "Thank you! Your order is now being processed. 🙌"
        ),
        "footer": "Thank you for your payment.",
        "buttons": [],
        "body_examples": ["Ahmed", "ORD-000032", "EGP 250.00"],
    },
    {
        "name": "payment_received_v2",
        "language": "ar",
        "category": "UTILITY",
        "body": (
            "أهلاً يا {{1}} 👋 استلمنا دفعتك. ✅\n\n"
            "🧾 الطلب: {{2}}\n"
            "💰 المبلغ المدفوع: {{3}}\n\n"
            "شكراً ليك! طلبك بقى تحت التجهيز. 🙌"
        ),
        "footer": "شكراً على الدفع.",
        "buttons": [],
        "body_examples": ["أحمد", "ORD-000032", "EGP 250.00"],
    },
    # 6. Abandoned cart — 2 vars + Complete URL button. MARKETING.
    {
        "name": "abandoned_cart_v3",
        "language": "en",
        "category": "MARKETING",
        "body": (
            "Hi {{1}} 👋 You left some great items in your cart at *{{2}}*. 🛒\n\n"
            "Don't miss out — they might sell out soon! Tap *Complete order* to "
            "check out in seconds. ⚡"
        ),
        "footer": "Reply STOP to unsubscribe.",
        "buttons": [
            {
                "type": "URL",
                "text": "Complete order",
                "url": _CART_URL,
                "example": [
                    "https://numueg.app/cart/cairo-style/"
                    "b3f1c2a4-5d6e-7f80-9a1b-2c3d4e5f6a7b"
                ],
            }
        ],
        "body_examples": ["Ahmed", "Cairo Style"],
    },
    {
        "name": "abandoned_cart_v3",
        "language": "ar",
        "category": "MARKETING",
        "body": (
            "أهلاً يا {{1}} 👋 سيبت منتجات حلوة في عربة التسوق في *{{2}}*. 🛒\n\n"
            "لا يفوتك — ممكن تخلص بسرعة! اضغط *أكمل الطلب* وكمّل الشراء في ثواني. ⚡"
        ),
        "footer": "اكتب STOP لإلغاء الاشتراك.",
        "buttons": [
            {
                "type": "URL",
                "text": "أكمل الطلب",
                "url": _CART_URL,
                "example": [
                    "https://numueg.app/cart/cairo-style/"
                    "b3f1c2a4-5d6e-7f80-9a1b-2c3d4e5f6a7b"
                ],
            }
        ],
        "body_examples": ["أحمد", "متجر القاهرة"],
    },
    # COD-to-prepaid recovery offer — the "recover" flow. URL button → /pay.
    # UTILITY (order-centric) to dodge the MARKETING frequency cap.
    {
        "name": "cod_recovery_offer_v1",
        "language": "en",
        "category": "UTILITY",
        "body": (
            "Hi {{1}}, your order {{2}} from *{{3}}* is {{4}}. {{5}} Pay online "
            "now to secure your order — it's quick and safe. 💳"
        ),
        "footer": "Prefer cash? No problem — your order stays as is.",
        "buttons": [
            {
                "type": "URL",
                "text": "Pay online",
                "url": _PAY_URL,
                "example": ["https://numueg.app/pay/cairo-style/ord-42"],
            }
        ],
        "body_examples": [
            "Sara",
            "ORD-000042",
            "Cairo Style",
            "EGP 250.00",
            "Get 10% off when you pay online.",
        ],
    },
    {
        "name": "cod_recovery_offer_v1",
        "language": "ar",
        "category": "UTILITY",
        "body": (
            "مرحباً يا {{1}}، طلبك {{2}} من *{{3}}* قيمته {{4}}. {{5}} ادفع "
            "أونلاين الآن لتأكيد طلبك — سريع وآمن. 💳"
        ),
        "footer": "تفضل الدفع كاش؟ مفيش مشكلة — طلبك زي ما هو.",
        "buttons": [
            {
                "type": "URL",
                "text": "ادفع أونلاين",
                "url": _PAY_URL,
                "example": ["https://numueg.app/pay/cairo-style/ord-42"],
            }
        ],
        "body_examples": [
            "سارة",
            "ORD-000042",
            "Cairo Style",
            "EGP 250.00",
            "خصم 10% عند الدفع أونلاين.",
        ],
    },
]
