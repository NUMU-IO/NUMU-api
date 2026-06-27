import {
  useEffect,
  useRef,
  useState } from "react";
import {
  useCart,
  useLocalization,
  useNavigation,
  useCollections,
  useCurrency,
  useShop,
} from "@numueg/theme-sdk";
import { EditableText } from "../lib/EditableText";
import type { EmpSectionProps } from "../lib/section";
import { useCartOpen, openCart, closeCart } from "../lib/cartUI";
import { CouponForm } from "../lib/CouponForm";
import { useT } from "../lib/i18n";

interface HeaderSettings {
  brand_name?: string;
  logo?: string;
  announcement_text?: string;
  menu_handle?: string;
  show_search?: boolean;
  show_account?: boolean;
  show_cart?: boolean;
}

export default function Header({
  id,
  settings,
  solidHeader,
}: EmpSectionProps & { solidHeader?: boolean }) {
  const s = settings as HeaderSettings;
  const t = useT();
  const shop = useShop();
  // The merchant rarely overrides the brand, and the theme's placeholder
  // ("STORE") gets baked into the store's saved customization on activation.
  // Treat that placeholder as "unset" so the real store name always wins.
  const brand =
    s.brand_name && s.brand_name !== "STORE"
      ? s.brand_name
      : shop?.name || s.brand_name || "STORE";
  const DEFAULT_LINKS = [
    { label: t("Home", "الرئيسية"), url: "/" },
    { label: t("Shop", "المتجر"), url: "/products" },
    { label: t("Contact", "تواصل"), url: "/pages/contact" },
  ];

  const { cart, updateQuantity, removeItem, loading } = useCart();
  const { formatMoney, locale, setLocale, availableLocales } =
    useLocalization();
  const nav = useNavigation(s.menu_handle || "main-menu");
  const { collections } = useCollections({ limit: 6 });
  const currency = useCurrency();
  const drawerOpen = useCartOpen();

  const [scrolled, setScrolled] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [megaOpen, setMegaOpen] = useState(false);
  const megaTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 50);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const items = cart?.items ?? [];
  const count = items.reduce((n, it) => n + it.quantity, 0);
  const cartCurrency = cart?.currency;

  const links =
    nav.items.length > 0
      ? nav.items.map((it) => ({ label: it.title, url: it.url }))
      : DEFAULT_LINKS;

  const showCurrency = currency.presentment.length > 1;

  // Language toggle. The store advertises locales via `availableLocales`; when
  // it doesn't, fall back to the Arabic/English pair this store ships with.
  const isAr = typeof locale === "string" && locale.toLowerCase().startsWith("ar");
  const localePair =
    availableLocales.length >= 2 ? availableLocales : ["ar", "en"];
  const nextLocale =
    localePair.find((l) =>
      isAr ? !l.toLowerCase().startsWith("ar") : l.toLowerCase().startsWith("ar"),
    ) || (isAr ? "en" : "ar");
  // Label shows the language you'll switch TO, in its own script.
  const switchLabel = isAr ? "EN" : "ع";

  const enterMega = () => {
    clearTimeout(megaTimer.current);
    setMegaOpen(true);
  };
  const leaveMega = () => {
    megaTimer.current = setTimeout(() => setMegaOpen(false), 180);
  };

  return (
    <>
      <header
        className={`nt-header${solidHeader || scrolled ? " is-scrolled" : ""}`}
      >
        {s.announcement_text ? (
          <EditableText
            as="div"
            className="nt-announce"
            sectionId={id}
            settingId="announcement_text"
            value={s.announcement_text}
          />
        ) : null}

        <div className="nt-container">
          <div className="nt-header__bar">
            {/* Left: desktop nav + mobile burger */}
            <nav className="nt-header__nav" aria-label="Primary">
              {links.map((l, i) => {
                const isShop = l.url === "/products";
                if (!isShop) {
                  return (
                    <a key={i} href={l.url}>
                      {l.label}
                    </a>
                  );
                }
                return (
                  <div
                    key={i}
                    style={{ position: "relative" }}
                    onMouseEnter={enterMega}
                    onMouseLeave={leaveMega}
                  >
                    <a href={l.url}>{l.label}</a>
                    {megaOpen && collections.length > 0 ? (
                      <div
                        className="nt-mega"
                        onMouseEnter={enterMega}
                        onMouseLeave={leaveMega}
                      >
                        <div className="nt-mega__col">
                          <p className="nt-mega__label">{l.label}</p>
                          <a
                            className="nt-mega__link nt-mega__link--strong"
                            href="/products"
                          >
                            {t("All products", "كل المنتجات")}
                          </a>
                          {collections.slice(0, 5).map((c) => (
                            <a
                              key={c.id}
                              className="nt-mega__link"
                              href={`/collections/${c.slug}`}
                            >
                              {c.name}
                            </a>
                          ))}
                        </div>
                        <div className="nt-mega__cards">
                          {collections.slice(0, 3).map((c) => (
                            <a
                              key={c.id}
                              className="nt-mega__card"
                              href={`/collections/${c.slug}`}
                            >
                              <span className="nt-mega__cardmedia">
                                {c.image_url ? (
                                  <img src={c.image_url} alt={c.name} />
                                ) : (
                                  <span className="nt-cat__placeholder">
                                    {c.name?.[0] ?? "?"}
                                  </span>
                                )}
                              </span>
                              <span className="nt-mega__cardname">
                                {c.name}
                              </span>
                            </a>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </nav>

            <button
              className="nt-burger"
              type="button"
              aria-label="Menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((v) => !v)}
            >
              {menuOpen ? <IconX /> : <IconMenu />}
            </button>

            {/* Center: logo */}
            <a className="nt-header__logo" href="/" aria-label={brand}>
              {s.logo ? (
                <img src={s.logo} alt={brand} />
              ) : (
                <EditableText
                  as="span"
                  sectionId={id}
                  settingId="brand_name"
                  value={brand}
                />
              )}
            </a>

            {/* Right: actions */}
            <div className="nt-header__actions">
              <button
                className="nt-langswitch"
                type="button"
                onClick={() => setLocale(nextLocale)}
                aria-label={isAr ? "Switch to English" : "التبديل إلى العربية"}
                title={isAr ? "English" : "العربية"}
              >
                {switchLabel}
              </button>

              {showCurrency ? (
                <select
                  className="nt-header__currency"
                  aria-label="Currency"
                  value={currency.selected}
                  onChange={(e) => currency.setSelected(e.target.value)}
                >
                  {currency.presentment.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              ) : null}

              {s.show_search !== false ? (
                <a className="nt-iconbtn" href="/search" aria-label="بحث">
                  <IconSearch />
                </a>
              ) : null}

              {s.show_account !== false ? (
                <a className="nt-iconbtn" href="/account" aria-label="الحساب">
                  <IconUser />
                </a>
              ) : null}

              {s.show_cart !== false ? (
                <button
                  className="nt-iconbtn"
                  type="button"
                  onClick={openCart}
                  aria-label={`السلة، ${count} عناصر`}
                >
                  <IconBag />
                  <span>{count}</span>
                </button>
              ) : null}
            </div>
          </div>
        </div>

        {menuOpen ? (
          <div className="nt-mobilemenu">
            {links.map((l, i) => (
              <a key={i} href={l.url} onClick={() => setMenuOpen(false)}>
                {l.label}
              </a>
            ))}
            {collections.slice(0, 5).map((c) => (
              <a
                key={c.id}
                href={`/collections/${c.slug}`}
                onClick={() => setMenuOpen(false)}
                style={{ paddingInlineStart: "1rem", fontWeight: 400 }}
              >
                {c.name}
              </a>
            ))}
          </div>
        ) : null}
      </header>

      {/* Cart drawer */}
      <div
        className={`nt-drawer${drawerOpen ? " is-open" : ""}`}
        role="dialog"
        aria-label="السلة"
        aria-hidden={!drawerOpen}
      >
        <div className="nt-drawer__overlay" onClick={closeCart} />
        <div className="nt-drawer__panel">
          <div className="nt-drawer__head">
            <span className="nt-drawer__title">
              {count > 0
                ? `${count} ${t("in cart", "عنصر في السلة")}`
                : t("Cart", "السلة")}
            </span>
            <button
              className="nt-drawer__close"
              type="button"
              onClick={closeCart}
              aria-label="إغلاق"
            >
              <IconX />
            </button>
          </div>

          <div className="nt-drawer__body">
            {items.length === 0 ? (
              <div className="nt-drawer__empty">
                <p>{t("Your cart is empty", "السلة فاضية")}</p>
              </div>
            ) : (
              items.map((line) => (
                <div className="nt-line" key={line.id}>
                  <div className="nt-line__img">
                    {line.image_url ? (
                      <img src={line.image_url} alt={line.name} />
                    ) : null}
                  </div>
                  <div className="nt-line__body">
                    <div className="nt-line__top">
                      <div>
                        <p className="nt-line__name">{line.name}</p>
                        {line.variant_name ? (
                          <p className="nt-line__variant">
                            {line.variant_name}
                          </p>
                        ) : null}
                      </div>
                      <p>{formatMoney(line.price * line.quantity, cartCurrency)}</p>
                    </div>
                    <div className="nt-line__controls">
                      <div className="nt-qty">
                        <button
                          type="button"
                          aria-label="تقليل"
                          disabled={loading}
                          onClick={() =>
                            updateQuantity(line.id, line.quantity - 1)
                          }
                        >
                          −
                        </button>
                        <span>{line.quantity}</span>
                        <button
                          type="button"
                          aria-label="زيادة"
                          disabled={loading}
                          onClick={() =>
                            updateQuantity(line.id, line.quantity + 1)
                          }
                        >
                          +
                        </button>
                      </div>
                      <button
                        className="nt-line__remove"
                        type="button"
                        disabled={loading}
                        onClick={() => removeItem(line.id)}
                        aria-label="حذف"
                      >
                        <IconX />
                      </button>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>

          {items.length > 0 ? (
            <div className="nt-drawer__foot">
              <CouponForm compact />
              <div className="nt-subtotal">
                <span>{t("Subtotal", "المجموع الفرعي")}</span>
                <span>{formatMoney(cart?.subtotal ?? 0, cartCurrency)}</span>
              </div>
              {cart?.discount_amount && cart.discount_amount > 0 ? (
                <div className="nt-subtotal nt-discount">
                  <span>
                    {t("Discount", "الخصم")}
                    {cart.discount_code ? ` (${cart.discount_code})` : ""}
                  </span>
                  <span>−{formatMoney(cart.discount_amount, cartCurrency)}</span>
                </div>
              ) : null}
              <div className="nt-subtotal nt-total">
                <span>{t("Total", "الإجمالي")}</span>
                <span>
                  {formatMoney(
                    cart?.total ??
                      (cart?.subtotal ?? 0) - (cart?.discount_amount ?? 0),
                    cartCurrency,
                  )}
                </span>
              </div>
              <a className="nt-btn nt-btn--block" href="/checkout">
                {t("Checkout", "إتمام الطلب")}
              </a>
              <button
                className="nt-iconbtn"
                type="button"
                style={{ justifyContent: "center" }}
                onClick={closeCart}
              >
                {t("Continue shopping", "متابعة التسوق")}
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </>
  );
}

/* ── Inline icons (no runtime icon dep) ── */
const IconSearch = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <circle cx="11" cy="11" r="7" />
    <path d="m21 21-4.35-4.35" />
  </svg>
);
const IconUser = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <circle cx="12" cy="8" r="4" />
    <path d="M4 21a8 8 0 0 1 16 0" />
  </svg>
);
const IconBag = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z" />
    <path d="M3 6h18" />
    <path d="M16 10a4 4 0 0 1-8 0" />
  </svg>
);
const IconMenu = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <path d="M3 12h18M3 6h18M3 18h18" />
  </svg>
);
const IconX = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <path d="M18 6 6 18M6 6l12 12" />
  </svg>
);
