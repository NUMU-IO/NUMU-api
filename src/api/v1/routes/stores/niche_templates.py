"""Niche-based store templates for the onboarding wizard."""

NICHE_TEMPLATES = {
    "fashion": {
        "theme": "editorial",
        "suggested_categories": ["ملابس رجالي", "ملابس حريمي", "أحذية", "إكسسوارات"],
        "suggested_sections": ["hero", "products", "features", "testimonials"],
    },
    "electronics": {
        "theme": "tech-wave",
        "suggested_categories": ["موبايلات", "لابتوب", "سماعات", "إكسسوارات"],
        "suggested_sections": ["hero", "products", "features"],
    },
    "beauty": {
        "theme": "luxury-minimal",
        "suggested_categories": ["عناية بالبشرة", "مكياج", "عطور", "عناية بالشعر"],
        "suggested_sections": ["hero", "products", "testimonials"],
    },
    "home": {
        "theme": "skeu",
        "suggested_categories": ["ديكور", "أثاث", "مطبخ", "حمام"],
        "suggested_sections": ["hero", "products", "features"],
    },
    "food": {
        "theme": "neo-brutalism",
        "suggested_categories": ["حلويات", "مشروبات", "وجبات", "سناكس"],
        "suggested_sections": ["hero", "products"],
    },
    "accessories": {
        "theme": "editorial",
        "suggested_categories": ["ساعات", "نظارات", "شنط", "مجوهرات"],
        "suggested_sections": ["hero", "products", "testimonials"],
    },
    "other": {
        "theme": "editorial",
        "suggested_categories": [],
        "suggested_sections": ["hero", "products", "features"],
    },
}

COUNTRY_DEFAULTS = {
    "EG": {
        "currency": "EGP",
        "shipping_zones": [
            "القاهرة",
            "الجيزة",
            "الإسكندرية",
            "المنصورة",
            "طنطا",
            "أسيوط",
        ],
    },
    "SA": {
        "currency": "SAR",
        "shipping_zones": ["الرياض", "جدة", "الدمام", "مكة", "المدينة"],
    },
    "AE": {
        "currency": "AED",
        "shipping_zones": ["دبي", "أبوظبي", "الشارقة", "عجمان"],
    },
    "JO": {
        "currency": "JOD",
        "shipping_zones": ["عمّان", "إربد", "الزرقاء"],
    },
    "KW": {
        "currency": "KWD",
        "shipping_zones": ["مدينة الكويت", "حولي", "الأحمدي"],
    },
}
