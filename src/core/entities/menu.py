"""Menu (store navigation / link list) domain entity."""

from uuid import UUID, uuid4

from pydantic import Field

from src.core.entities.base import BaseEntity


class Menu(BaseEntity):
    """A store-level navigation menu (link list).

    ``items`` is a nested list of menu-item dicts (depth <= 3)::

        {id, label: {en, ar}, url, type, resource_id?, children: [...]}

    URLs are stored already-resolved (the merchant picks them via the link
    picker), so the storefront resolver is largely pass-through. ``handle`` is
    unique per store (``main-menu`` for the header, ``footer`` for the footer,
    or any custom handle).
    """

    store_id: UUID
    tenant_id: UUID | None = None
    handle: str
    title: dict[str, str] = Field(default_factory=dict)
    items: list[dict] = Field(default_factory=list)
    is_active: bool = True


def _item(label_en: str, label_ar: str, url: str, item_type: str = "link") -> dict:
    """Build a single default menu item (no children)."""
    return {
        "id": uuid4().hex,
        "label": {"en": label_en, "ar": label_ar},
        "url": url,
        "type": item_type,
        "children": [],
    }


def build_default_menus(store_id: UUID, tenant_id: UUID | None) -> list[Menu]:
    """Default seed menus for a new store: a header ``main-menu`` + ``footer``.

    Bilingual (en + Egyptian Arabic). Handles match the theme ``link_list``
    convention so a freshly-created store's header/footer have sensible nav
    out of the box.
    """
    main_menu = Menu(
        store_id=store_id,
        tenant_id=tenant_id,
        handle="main-menu",
        title={"en": "Main menu", "ar": "القائمة الرئيسية"},
        items=[
            _item("Home", "الرئيسية", "/", "home"),
            _item("Products", "المنتجات", "/products", "catalog"),
            _item("About", "من نحن", "/about", "page"),
            _item("Contact", "اتصل بنا", "/contact", "page"),
        ],
    )
    footer = Menu(
        store_id=store_id,
        tenant_id=tenant_id,
        handle="footer",
        title={"en": "Footer", "ar": "تذييل الصفحة"},
        items=[
            _item("Shipping", "الشحن", "/shipping", "page"),
            _item("Returns", "الإرجاع", "/returns", "page"),
            _item("FAQ", "الأسئلة الشائعة", "/faq", "page"),
            _item("Track order", "تتبّع الطلب", "/track", "page"),
        ],
    )
    return [main_menu, footer]
