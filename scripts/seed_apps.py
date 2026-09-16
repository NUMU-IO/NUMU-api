"""Seed / upsert the first-party app registry.

Usage:
    python -m scripts.seed_apps            # upsert every app below
    python -m scripts.seed_apps --dry-run  # print what would change, write nothing
    python -m scripts.seed_apps --list     # show what is currently in the table

Why this exists: nothing could create an `apps` row. No route, no seed, no MCP
tool and no admin UI referenced `AppModel` — the module docstring said plainly
that "admins seed via SQL/console", which on this platform means hand-written
SQL against the production database, with no staging to rehearse it in.

Idempotent by `slug`: re-running updates name/description/version/manifest and
never touches `app_installations`, so a merchant's settings survive an app
update. Re-running is the intended way to ship a manifest change.

SAFETY: seeding the first row is also what makes the storefront app endpoints
reachable, so the default-deny projection in
`api/v1/routes/storefront/app_public.py` must be deployed FIRST. `manifest`
below is public by definition — anything secret would need a home outside it.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("seed_apps")


# ── The Variant Swatches manifest ──────────────────────────────────────────
#
# `public_settings` is the allowlist the storefront projection reads. Every
# setting here is presentation, so every one is listed; a setting that ever
# holds a credential must NOT be added.
#
# Labels ship under `locales.ar` / `locales.en` because that nested shape is
# what the hub actually reads — the flat `label_ar` every theme file on disk
# uses is inert, which is why Arabic labels never appeared in the theme editor.
# Arabic copy is Egyptian colloquial, per the house rule.
VARIANT_SWATCHES = {
    # The SLUG is the install key and never reaches a shopper. Eighteen themes
    # call `useInstalledApp("variant-swatches")` and the storefront routes key
    # on it, so it stays put even though the app is branded "Nimo" — renaming
    # an id to match a brand is eighteen theme edits for nothing.
    "slug": "variant-swatches",
    "name": "Nimo",
    "description": (
        "Turn your product options into colour and image swatches, so shoppers "
        "see every choice at a glance instead of opening a dropdown."
    ),
    "version": "1.0.0",
    "icon_url": "/apps/variant-swatches.png",
    "manifest": {
        "version": "1.0.0",
        "slots": ["variant-picker"],
        # ── Listing metadata. Every app supplies this shape; the hub's app
        # detail page renders whatever is present and omits what is not, so a
        # third-party app gets the same page for free.
        "tagline": "Colour and image swatches for your product options",
        # Localised name / tagline / description. `locales.ar` is the nested
        # shape the hub actually reads; the top-level English values stay as the
        # fallback for a locale nobody translated.
        "locales": {
            "ar": {
                "tagline": "ألوان وصور لاختيارات المنتج",
            },
            "en": {
                "tagline": "Colour and image swatches for your product options",
            },
        },
        "developer": {
            "name": "NUMU",
            "url": "https://numueg.app",
            "support_email": "support@numueg.app",
            "is_first_party": True,
        },
        "lockup_url": "/apps/variant-swatches-lockup.png",
        # Real renders of the app on a product page, not mock-ups.
        "screenshots": [
            {
                "url": "/apps/variant-swatches-shot-2.png",
                "locales": {
                    "ar": {
                        "caption": "دواير ألوان وصور بدل القائمة المنسدلة، واللون اللي خلص عليه خط."
                    },
                    "en": {
                        "caption": "Round colour and image swatches instead of a dropdown, with the sold-out colour struck through."
                    },
                },
            },
            {
                "url": "/apps/variant-swatches-shot-1.png",
                "locales": {
                    "ar": {
                        "caption": "أو كروت فيها الصورة والاسم والسعر، واللي خلص عليه علامة X."
                    },
                    "en": {
                        "caption": "Or boxed swatches carrying the image, the name and the price, with sold-out crossed out."
                    },
                },
            },
        ],
        "highlights": [
            {
                "locales": {
                    "ar": {"text": "ورّي كل اختيارات المنتج قدام الزبون على طول"},
                    "en": {"text": "Show every option visually, not in a dropdown"},
                }
            },
            {
                "locales": {
                    "ar": {"text": "اظبط الشكل والمقاس والنِسبة على ستايل متجرك"},
                    "en": {"text": "Match your brand: shape, size and ratio"},
                }
            },
            {
                "locales": {
                    "ar": {"text": "الألوان اللي خلصت تبان بخط عليها أو تختفي"},
                    "en": {"text": "Strike through or hide sold-out options"},
                }
            },
            {
                "locales": {
                    "ar": {"text": "حط صورة لكل لون وهتظهر جوه الدايرة"},
                    "en": {"text": "Give each colour its own image"},
                }
            },
        ],
        # ── The feature list the merchant reads BEFORE installing. Same shape
        # as `highlights` but with a body: highlights are the four-line pitch,
        # features are the full tour, which is what a merchant comparing apps
        # actually wants. Every entry below describes something the app really
        # does today.
        "features": [
            {
                "icon": "palette",
                "locales": {
                    "ar": {
                        "title": "ألوان بالكود بتاعك",
                        "body": (
                            "اكتب كود اللون لكل اختيار، أو سيب نيمو يجيبه لك من قاموس "
                            "الألوان بالعربي والإنجليزي. ولو اللون مش معروف، بيظهر شكل "
                            "واضح إنه لسه محتاج كود — مش مربع رمادي بيغلط الزبون."
                        ),
                    },
                    "en": {
                        "title": "Colour swatches from your own codes",
                        "body": (
                            "Type a hex code per value, or let Nimo fill it from its "
                            "Arabic and English colour dictionary. A colour it cannot "
                            "resolve is drawn as clearly unset — never as a grey chip "
                            "that tells a shopper the colour is grey."
                        ),
                    },
                },
            },
            {
                "icon": "image",
                "locales": {
                    "ar": {
                        "title": "صورة لكل اختيار",
                        "body": (
                            "ارفع صورة للاختيار وهتملى الخانة كلها — القماش، الطبعة، "
                            "أو صورة المنتج باللون ده. وينفع كمان على اختيارات مش ألوان "
                            "زي الموديل أو الخامة."
                        ),
                    },
                    "en": {
                        "title": "An image for every value",
                        "body": (
                            "Upload an image for a value and it fills the swatch — the "
                            "fabric, the print, or the product in that colour. Works on "
                            "non-colour axes too, like style or material."
                        ),
                    },
                },
            },
            {
                "icon": "shapes",
                "locales": {
                    "ar": {
                        "title": "الشكل والمقاس والنِسبة",
                        "body": (
                            "دايرة، مربع، حواف مدورة، بيضاوي، أو حواف من عندك بأي قياس "
                            "CSS. وخلّي الخانة طولية للشنط والجزم، أو عريضة للأقمشة."
                        ),
                    },
                    "en": {
                        "title": "Shape, size and ratio",
                        "body": (
                            "Circle, square, rounded, pill, or your own radius in any "
                            "CSS length. Make the swatch portrait for shoes and bags, "
                            "or landscape for fabrics."
                        ),
                    },
                },
            },
            {
                "icon": "ban",
                "locales": {
                    "ar": {
                        "title": "اللي خلص من المخزن",
                        "body": (
                            "خط مايل على الخانة، أو علامة X، أو خلّيه باهت، أو اخفيه "
                            "خالص. نيمو بيقرا المخزون الحقيقي لكل مقاس ولون، ولو المخزون "
                            "مش متتبّع بيسيب الاختيار شغّال بدل ما يقفله غلط."
                        ),
                    },
                    "en": {
                        "title": "Sold-out options, four ways",
                        "body": (
                            "A diagonal line, a cross, a fade, or hide it entirely. "
                            "Nimo reads real per-variant stock, and leaves an untracked "
                            "option buyable rather than wrongly closing it."
                        ),
                    },
                },
            },
            {
                "icon": "grid",
                "locales": {
                    "ar": {
                        "title": "الألوان في شبكة المنتجات",
                        "body": (
                            "ورّي صف الألوان على كروت المنتجات كمان، وحدّد كام لون "
                            "يبانوا قبل ما يظهر رقم الباقي."
                        ),
                    },
                    "en": {
                        "title": "Swatches on the collection grid",
                        "body": (
                            "Show the colour row on product cards too, capped to as "
                            "many as you choose before a +N counter takes over."
                        ),
                    },
                },
            },
            {
                "icon": "tag",
                "locales": {
                    "ar": {
                        "title": "السعر جنب كل اختيار",
                        "body": (
                            "ورّي أرخص سعر الاختيار ده بيوصّل له، تحت الخانة على طول. "
                            "مفيد لما اللون أو المقاس بيغيّر السعر."
                        ),
                    },
                    "en": {
                        "title": "A price under every value",
                        "body": (
                            "Show the cheapest price a value leads to, right under the "
                            "swatch. Useful when a colour or size changes the price."
                        ),
                    },
                },
            },
            {
                "icon": "list",
                "locales": {
                    "ar": {
                        "title": "قايمة ألوان للمتجر كله",
                        "body": (
                            "اربط اسم اللون بالكود مرة واحدة، وكل المنتجات هتمشي عليه "
                            "من غير ما تفتح منتج منتج. وتقدر تستورد القايمة من ملف CSV."
                        ),
                    },
                    "en": {
                        "title": "One colour list for the whole store",
                        "body": (
                            "Map a colour name to a code once and every product follows "
                            "— no opening products one by one. Import the list from CSV."
                        ),
                    },
                },
            },
            {
                "icon": "languages",
                "locales": {
                    "ar": {
                        "title": "أسماء بالعربي",
                        "body": (
                            "اكتب اسم عربي لكل اختيار والزبون يشوفه بالعربي. الأرقام "
                            "والأكواد بتفضل من الشمال لليمين جوه السطر العربي."
                        ),
                    },
                    "en": {
                        "title": "Arabic labels, done properly",
                        "body": (
                            "Give each value an Arabic name and shoppers read it in "
                            "Arabic. Numbers and codes stay left-to-right inside the "
                            "Arabic row."
                        ),
                    },
                },
            },
            {
                "icon": "plug",
                "locales": {
                    "ar": {
                        "title": "من غير ما تلمس الثيم",
                        "body": (
                            "ثبّت التطبيق وصفحة المنتج هتشتغل بيه على طول. مفيش كود "
                            "تلزقه، ولو قفلت التطبيق صفحة المنتج بترجع زي ما كانت."
                        ),
                    },
                    "en": {
                        "title": "No theme code",
                        "body": (
                            "Install it and your product page picks it up. Nothing to "
                            "paste, and turning it off returns the page to how it was."
                        ),
                    },
                },
            },
        ],
        "pricing": {
            "plan": "free",
            "locales": {
                "ar": {"label": "مجاني مع باقتك"},
                "en": {"label": "Free with your plan"},
            },
        },
        "languages": ["ar", "en"],
        "compatibility": {
            "locales": {
                "ar": {
                    "text": (
                        "بيشتغل على صفحة المنتج في كل ثيمات نُمو. وصف الألوان على شبكة "
                        "المنتجات شغّال دلوقتي في ثيم Genova، وبيتفعّل في باقي الثيمات "
                        "واحد ورا التاني."
                    )
                },
                "en": {
                    "text": (
                        "Works on the product page in every NUMU theme. The "
                        "collection-grid row is live on Genova today and reaches the "
                        "other themes as each one adopts it."
                    )
                },
            }
        },
        "public_settings": [
            "swatch_style",
            "show_price",
            "swatch_radius",
            "swatch_ratio",
            "color_option_names",
            "image_source",
            "swatch_shape",
            "swatch_size",
            "out_of_stock",
            "show_on_cards",
            "card_max_visible",
            "color_overrides",
        ],
        "app_locales": {
            "ar": {
                "name": "Nimo",
                "description": (
                    "حوّل اختيارات المنتج لمربعات ألوان وصور، عشان الزبون يشوف كل "
                    "الاختيارات قدامه على طول من غير ما يفتح قائمة."
                ),
            },
            "en": {
                "name": "Nimo",
                "description": (
                    "Turn your product options into colour and image swatches, so "
                    "shoppers see every choice at a glance."
                ),
            },
        },
        "settings_schema": [
            {
                "id": "color_option_names",
                "type": "text",
                "group": "mapping",
                "group_locales": {
                    "ar": "أي اختيارات تبقى ألوان",
                    "en": "Which options are colours",
                },
                "default": "Color,Colour,اللون,الالوان",
                "locales": {
                    "ar": {
                        "label": "أسماء خانة اللون",
                        "info": "اكتب أسماء الخانة اللي فيها الألوان، وبينهم فاصلة.",
                    },
                    "en": {
                        "label": "Colour option names",
                        "info": "Comma-separated names of the options that hold colours.",
                    },
                },
            },
            {
                "id": "swatch_style",
                "type": "select",
                "group": "style",
                "group_locales": {"ar": "الشكل", "en": "Look"},
                "default": "chip",
                "options": [
                    {
                        "value": "chip",
                        "locales": {
                            "ar": {"label": "مربع لون بس"},
                            "en": {"label": "Swatch only"},
                        },
                    },
                    {
                        "value": "pills",
                        "locales": {
                            "ar": {"label": "لون + اسم في شكل بيضاوي"},
                            "en": {"label": "Swatch pill"},
                        },
                    },
                    {
                        "value": "box",
                        "locales": {
                            "ar": {"label": "كارت فيه اللون والاسم"},
                            "en": {"label": "Swatch box"},
                        },
                    },
                    {
                        "value": "polaroid",
                        "locales": {
                            "ar": {"label": "كارت بولارويد"},
                            "en": {"label": "Polaroid"},
                        },
                    },
                    {
                        "value": "button",
                        "locales": {
                            "ar": {"label": "زرار بالاسم"},
                            "en": {"label": "Button"},
                        },
                    },
                    {
                        "value": "radio",
                        "locales": {
                            "ar": {"label": "اختيار بدائرة"},
                            "en": {"label": "Radio"},
                        },
                    },
                ],
                "locales": {
                    "ar": {
                        "label": "شكل الاختيارات",
                        "info": "ده اللي الزبون هيشوفه مكان القائمة المنسدلة.",
                    },
                    "en": {
                        "label": "Option style",
                        "info": "What the shopper sees instead of a dropdown.",
                    },
                },
            },
            {
                "id": "show_price",
                "type": "checkbox",
                "group": "style",
                "group_locales": {"ar": "الشكل", "en": "Look"},
                "default": False,
                "locales": {
                    "ar": {
                        "label": "ورّي سعر كل اختيار",
                        "info": "بيظهر في الأشكال اللي فيها اسم تحت اللون.",
                    },
                    "en": {
                        "label": "Show each option's price",
                        "info": "Appears on the styles that carry a label.",
                    },
                },
            },
            {
                "id": "swatch_shape",
                "type": "select",
                "group": "style",
                "group_locales": {"ar": "الشكل", "en": "Look"},
                "default": "circle",
                "options": [
                    {
                        "value": "circle",
                        "locales": {
                            "ar": {"label": "دائرة"},
                            "en": {"label": "Circle"},
                        },
                    },
                    {
                        "value": "square",
                        "locales": {"ar": {"label": "مربع"}, "en": {"label": "Square"}},
                    },
                    {
                        "value": "rounded",
                        "locales": {
                            "ar": {"label": "مربع بحواف"},
                            "en": {"label": "Rounded"},
                        },
                    },
                    {
                        "value": "pill",
                        "locales": {"ar": {"label": "بيضاوي"}, "en": {"label": "Pill"}},
                    },
                    {
                        "value": "custom",
                        "locales": {
                            "ar": {"label": "شكل من عندك"},
                            "en": {"label": "Custom"},
                        },
                    },
                ],
                "locales": {
                    "ar": {"label": "شكل الخانة"},
                    "en": {"label": "Swatch shape"},
                },
            },
            {
                "id": "swatch_radius",
                "type": "text",
                "group": "style",
                "group_locales": {"ar": "الشكل", "en": "Look"},
                "default": "12px",
                "visible_if": "settings.swatch_shape == 'custom'",
                "locales": {
                    "ar": {
                        "label": "تدوير الحواف",
                        "info": "أي قيمة CSS، زي 4px أو 14px أو 30%.",
                    },
                    "en": {
                        "label": "Corner radius",
                        "info": "Any CSS length or percentage, e.g. 4px, 14px, 30%.",
                    },
                },
            },
            {
                "id": "swatch_ratio",
                "type": "select",
                "group": "style",
                "group_locales": {"ar": "الشكل", "en": "Look"},
                "default": "1:1",
                "options": [
                    {
                        "value": "1:1",
                        "locales": {
                            "ar": {"label": "مربع (1:1)"},
                            "en": {"label": "Square (1:1)"},
                        },
                    },
                    {
                        "value": "3:4",
                        "locales": {
                            "ar": {"label": "طولي (3:4)"},
                            "en": {"label": "Portrait (3:4)"},
                        },
                    },
                    {
                        "value": "2:3",
                        "locales": {
                            "ar": {"label": "طولي أكتر (2:3)"},
                            "en": {"label": "Tall (2:3)"},
                        },
                    },
                    {
                        "value": "4:3",
                        "locales": {
                            "ar": {"label": "عرضي (4:3)"},
                            "en": {"label": "Landscape (4:3)"},
                        },
                    },
                    {
                        "value": "16:9",
                        "locales": {
                            "ar": {"label": "عريض (16:9)"},
                            "en": {"label": "Wide (16:9)"},
                        },
                    },
                ],
                "locales": {
                    "ar": {
                        "label": "نسبة الطول للعرض",
                        "info": "الطولي بيناسب الشنط والجزم واللبس. الدايرة بتفضل مربعة دايماً.",
                    },
                    "en": {
                        "label": "Tile ratio",
                        "info": "Portrait suits bags, shoes and garments. Circle always stays square.",
                    },
                },
            },
            {
                "id": "swatch_size",
                "type": "select",
                "group": "style",
                "group_locales": {"ar": "الشكل", "en": "Look"},
                "default": "m",
                "options": [
                    {
                        "value": "s",
                        "locales": {"ar": {"label": "صغير"}, "en": {"label": "Small"}},
                    },
                    {
                        "value": "m",
                        "locales": {"ar": {"label": "وسط"}, "en": {"label": "Medium"}},
                    },
                    {
                        "value": "l",
                        "locales": {"ar": {"label": "كبير"}, "en": {"label": "Large"}},
                    },
                ],
                "locales": {
                    "ar": {"label": "حجم الخانة"},
                    "en": {"label": "Swatch size"},
                },
            },
            {
                "id": "image_source",
                "type": "select",
                "group": "style",
                "group_locales": {"ar": "الشكل", "en": "Look"},
                "default": "custom",
                "options": [
                    {
                        "value": "custom",
                        "locales": {
                            "ar": {"label": "الصورة اللي اخترتها للون"},
                            "en": {"label": "The image you picked"},
                        },
                    },
                    {
                        "value": "color_only",
                        "locales": {
                            "ar": {"label": "اللون بس، من غير صور"},
                            "en": {"label": "Colour only, no images"},
                        },
                    },
                ],
                "locales": {
                    "ar": {
                        "label": "اللون يبان إزاي",
                        "info": "لو اللون ليه صورة وليه لون، ده اللي بيقرر مين يظهر.",
                    },
                    "en": {
                        "label": "What a swatch shows",
                        "info": "Decides which wins when a value has both an image and a colour.",
                    },
                },
            },
            {
                "id": "out_of_stock",
                "type": "select",
                "group": "stock",
                "group_locales": {"ar": "المخزون", "en": "Stock"},
                "default": "strike",
                "options": [
                    {
                        "value": "strike",
                        "locales": {
                            "ar": {"label": "خط على اللون اللي خلص"},
                            "en": {"label": "Line through it"},
                        },
                    },
                    {
                        "value": "dim",
                        "locales": {
                            "ar": {"label": "اللون يبهت شوية"},
                            "en": {"label": "Fade it"},
                        },
                    },
                    {
                        "value": "cross",
                        "locales": {
                            "ar": {"label": "علامة × على اللون"},
                            "en": {"label": "Cross it out"},
                        },
                    },
                    {
                        "value": "hide",
                        "locales": {
                            "ar": {"label": "اللون يختفي خالص"},
                            "en": {"label": "Hide it completely"},
                        },
                    },
                ],
                "locales": {
                    "ar": {
                        "label": "الألوان اللي خلصت",
                        "info": "لو اخترت تخفيه، الزباين مش هيعرفوا إنه كان موجود ولا هيسألوا عليه.",
                    },
                    "en": {
                        "label": "Sold-out colours",
                        "info": "Hiding removes colour discovery and the restock signal.",
                    },
                },
            },
            {
                "id": "show_on_cards",
                "type": "checkbox",
                "group": "surfaces",
                "group_locales": {"ar": "مكان الظهور", "en": "Where it shows"},
                "default": True,
                "locales": {
                    "ar": {"label": "ورّي الألوان في صفحة المنتجات كمان"},
                    "en": {"label": "Show swatches on collection cards"},
                },
            },
            {
                "id": "card_max_visible",
                "type": "range",
                "group": "surfaces",
                "group_locales": {"ar": "مكان الظهور", "en": "Where it shows"},
                "min": 3,
                "max": 10,
                "default": 5,
                "visible_if": "settings.show_on_cards == true",
                "locales": {
                    "ar": {"label": "أكتر عدد ألوان يبان على الكارت"},
                    "en": {"label": "Max swatches per card"},
                },
            },
            {
                "id": "color_overrides",
                "type": "key_color_map",
                "group": "mapping",
                "group_locales": {
                    "ar": "أي اختيارات تبقى ألوان",
                    "en": "Which options are colours",
                },
                "locales": {
                    "ar": {
                        "label": "ألوان ثابتة لكل اسم",
                        "info": "لو كتبت لون هنا، هيتطبق على كل المنتجات اللي فيها الاسم ده.",
                    },
                    "en": {
                        "label": "Store-wide colour overrides",
                        "info": "Applies to every product using that option value.",
                    },
                },
            },
        ],
    },
}

APPS = [VARIANT_SWATCHES]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print, write nothing")
    parser.add_argument("--list", action="store_true", help="show the current table")
    args = parser.parse_args()

    url = os.getenv("DATABASE_URL")
    if not url:
        logger.error("DATABASE_URL is not set. Refusing to guess a database.")
        return 2
    # Never let a stray production URL be used without it being obvious.
    logger.info("target: %s", url.split("@")[-1])

    engine = create_async_engine(url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        await session.execute(text("SET search_path TO public"))

        if args.list:
            rows = (
                await session.execute(
                    text("SELECT slug, name, version, status FROM apps ORDER BY slug")
                )
            ).all()
            if not rows:
                logger.info("apps table is EMPTY")
            for r in rows:
                logger.info("%s | %s | v%s | %s", r[0], r[1], r[2], r[3])
            await engine.dispose()
            return 0

        for app in APPS:
            existing = (
                await session.execute(
                    text("SELECT id, version FROM apps WHERE slug = :slug"),
                    {"slug": app["slug"]},
                )
            ).one_or_none()

            if args.dry_run:
                logger.info(
                    "%s %s (v%s)",
                    "WOULD UPDATE" if existing else "WOULD INSERT",
                    app["slug"],
                    app["version"],
                )
                continue

            # `status` is a native Postgres ENUM (the Phase 6 migration was
            # hot-fixed to make it one), so the value is cast explicitly.
            await session.execute(
                text(
                    """
                    INSERT INTO apps (
                        id, slug, name, description, version, icon_url,
                        manifest, status, created_at, updated_at
                    )
                    VALUES (
                        gen_random_uuid(), :slug, :name, :description, :version,
                        :icon_url, CAST(:manifest AS jsonb),
                        CAST('published' AS appstatus), now(), now()
                    )
                    ON CONFLICT (slug) DO UPDATE SET
                        name = EXCLUDED.name,
                        description = EXCLUDED.description,
                        version = EXCLUDED.version,
                        icon_url = EXCLUDED.icon_url,
                        manifest = EXCLUDED.manifest,
                        updated_at = now()
                    """
                ),
                {
                    "slug": app["slug"],
                    "name": app["name"],
                    "description": app["description"],
                    "version": app["version"],
                    "icon_url": app["icon_url"],
                    "manifest": json.dumps(app["manifest"], ensure_ascii=False),
                },
            )
            logger.info(
                "%s %s v%s",
                "updated" if existing else "inserted",
                app["slug"],
                app["version"],
            )

        if not args.dry_run:
            await session.commit()

    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
