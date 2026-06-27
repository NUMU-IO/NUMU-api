"use client";

import { useEffect, useState } from "react";
import {
  Link,
  logoImgStyle,
  useCart,
  useLocale,
  useNavigation,
  useResolvedSettings,
  useShop,
  useThemeSettings,
} from "@numueg/theme-sdk";
import { Home, Menu, Search, ShoppingBag, User, X } from "lucide-react";
import {
  asImageUrl,
  asNumber,
  asString,
  localized,
  readBlocks,
  type SectionRenderProps,
} from "./_shared";
import BzAnnouncementBar from "./_announcement";
import BzCartDrawer from "./_cart-drawer";

interface NavItem {
  label: string;
  href: string;
}

const defaultNav = (locale: string | undefined): NavItem[] => [
  { label: localized(locale, "HOME", "الرئيسية"), href: "/" },
  { label: localized(locale, "SHOP", "تسوّق"), href: "/products" },
  { label: localized(locale, "ABOUT", "من نحن"), href: "/about" },
  { label: localized(locale, "CONTACT", "تواصل معنا"), href: "/contact" },
];

// Default announcement slides — shown when the merchant hasn't added any
// "Announcement message" blocks yet (mirrors defaultNav). Merchant blocks
// override these; the announcement_enabled toggle hides the bar entirely.
const defaultAnnouncements = (
  locale: string | undefined,
): { text: string; link?: string }[] => [
  {
    text: localized(
      locale,
      "✦ Free shipping on orders over 1000 EGP",
      "✦ شحن مجاني للطلبات فوق ١٠٠٠ جنيه",
    ),
  },
  {
    text: localized(
      locale,
      "✦ New drops every week — shop now",
      "✦ تشكيلات جديدة كل أسبوع — تسوّق الآن",
    ),
  },
];

/**
 * bz-header — Bazar's chrome header. Ported from the V2 BzStoreHeader:
 * navy bar with amber labels, an amber announcement marquee strip on
 * top, a centered logo / monogram, primary nav (from `nav_item` blocks
 * → falls back to a sensible default set), and search + cart icons. The
 * cart count is live from `useCart()`. A burger opens a full-screen
 * overlay on mobile.
 */
export default function BzHeader({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const shop = useShop();
  const { cart } = useCart();
  const themeSettings = useThemeSettings();
  const locale = useLocale();

  const brandName =
    asString(s.brand_name) ||
    asString(themeSettings.global_settings?.brand_name) ||
    shop?.name ||
    "BAZAR";

  const logoUrl =
    asImageUrl(s.logo_url) ||
    asImageUrl(themeSettings.global_settings?.logo_url) ||
    shop?.logo_url ||
    "";

  // Merchant-chosen logo appearance, now an ENGINE-LEVEL feature: shape + size
  // live in GLOBAL settings and the shaping is computed by the SDK's shared
  // `logoImgStyle` (inline styles, GIF-safe) so every theme renders it the same
  // way. `none` keeps the original artwork (theme's own h-9/h-11 sizing); the
  // shapes crop a 1:1 box.
  const logoShape = asString(themeSettings.global_settings?.logo_shape) || "none";
  const logoSize = asString(themeSettings.global_settings?.logo_size) || "small";
  const logoShaped = logoShape !== "none";
  const logoStyle = logoImgStyle(logoShape, logoSize);

  const showSearch = (s.show_search as boolean) !== false;
  const showCart = (s.show_cart as boolean) !== false;

  // Nav source priority (§5 hide-page→hide-nav-link):
  //   1. the merchant's `main-menu` (hub Navigation manager) via useNavigation —
  //      the SDK drops items whose target CMS page is unpublished/deleted, so a
  //      hidden page's link disappears here automatically;
  //   2. theme `nav_item` blocks (explicit per-theme override);
  //   3. the bilingual default set.
  const mainMenu = useNavigation("main-menu");
  const navBlocks = readBlocks(instance, "nav_item");
  const nav: NavItem[] =
    mainMenu.items.length > 0
      ? mainMenu.items
          .map((i) => ({ label: i.title, href: i.url || "/" }))
          .filter((n) => n.label)
      : navBlocks.length > 0
        ? navBlocks
            .map((r) => ({
              label: asString(r.label),
              href: asString(r.href) || "/",
            }))
            .filter((n) => n.label)
        : defaultNav(locale);

  // Announcement bar (chrome, rendered above the nav as its OWN bar). Driven by
  // `announcement` blocks — each block is one slide the merchant adds — plus
  // header settings (on/off, slide vs static, slide speed). Messages are
  // locale-resolved here so the bar component stays purely presentational.
  const isAr = (locale || "").toLowerCase().startsWith("ar");
  const announcementEnabled = (s.announcement_enabled as boolean) !== false;
  const announcementMode = asString(s.announcement_mode) || "slide";
  const announcementSpeed = asNumber(s.announcement_speed, 4);
  // The merchant's main message (the always-editable "Announcement text" header
  // setting) is slide #1 — this is what lets them "write whatever they want".
  // Optional `announcement` blocks add further rotating slides after it.
  const primaryText = isAr
    ? asString(s.announcement_text_ar) || asString(s.announcement_text)
    : asString(s.announcement_text) || asString(s.announcement_text_ar);
  const primaryMessage = primaryText.trim()
    ? [{ text: primaryText, link: undefined as string | undefined }]
    : [];
  const blockMessages = readBlocks(instance, "announcement")
    .map((b) => ({
      text: isAr
        ? asString(b.text_ar) || asString(b.text)
        : asString(b.text) || asString(b.text_ar),
      link: asString(b.link) || undefined,
    }))
    .filter((m) => m.text.trim());
  const combined = [...primaryMessage, ...blockMessages];
  const announcementMessages =
    combined.length > 0 ? combined : defaultAnnouncements(locale);

  const [mobileOpen, setMobileOpen] = useState(false);
  const [cartOpen, setCartOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const itemCount = cart?.items?.reduce((n, it) => n + it.quantity, 0) ?? 0;

  // Mobile bottom tab bar (V2 parity). Fixed, thumb-reachable primary actions.
  // The account tab points at `/account` (the storefront page handles the
  // logged-out state); search/cart/home map to their real routes.
  const bottomTabs = [
    { label: localized(locale, "HOME", "الرئيسية"), icon: Home, to: "/", badge: 0, action: undefined as "cart" | undefined },
    { label: localized(locale, "SEARCH", "بحث"), icon: Search, to: "/search", badge: 0, action: undefined as "cart" | undefined },
    // Cart opens the slide-in drawer instead of navigating to /cart.
    { label: localized(locale, "CART", "السلة"), icon: ShoppingBag, to: "/cart", badge: itemCount, action: "cart" as "cart" | undefined },
    { label: localized(locale, "ACCOUNT", "حسابي"), icon: User, to: "/account", badge: 0, action: undefined as "cart" | undefined },
  ];
  // Active tab from the current path. Each storefront route is its own SSR
  // render → fresh bundle mount, so reading location at render time is current.
  // Subdomain routing yields clean paths ("/cart"); endsWith also covers the
  // path-routing fallback ("/<domain>/cart").
  const currentPath =
    typeof window !== "undefined"
      ? window.location.pathname.replace(/\/+$/, "")
      : "";
  const tabActive = (to: string) =>
    to === "/"
      ? currentPath === "" || currentPath === "/"
      : currentPath === to || currentPath.endsWith(to);

  // Shadow + blur once the page scrolls a little, matching V2.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onScroll = () => setScrolled(window.scrollY > 20);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // Close the mobile overlay when crossing to desktop.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(min-width: 768px)");
    const close = () => setMobileOpen(false);
    mq.addEventListener("change", close);
    return () => mq.removeEventListener("change", close);
  }, []);

  // Lock body scroll + close on Escape while the drawer is open.
  useEffect(() => {
    if (typeof document === "undefined") return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = mobileOpen ? "hidden" : prev || "";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMobileOpen(false);
    };
    if (mobileOpen) window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [mobileOpen]);

  return (
    <div data-bz-section={sectionId}>
      {/* Announcement strip — its OWN bar above the nav (no longer fused with
          it). A customizable promo slider: the merchant adds message slides via
          the header's "Announcement message" blocks and picks slide-vs-static +
          speed in the header settings. */}
      {announcementEnabled && announcementMessages.length > 0 && (
        <BzAnnouncementBar
          messages={announcementMessages}
          mode={announcementMode}
          speed={announcementSpeed}
        />
      )}
      <header
        className={`sticky top-0 z-50 transition-all duration-300 ${
          scrolled
            ? "bg-[var(--bz-dark)]/95 backdrop-blur-md shadow-lg"
            : "bg-[var(--bz-dark)]"
        }`}
      >
        <div className="container mx-auto px-4 py-2.5 grid grid-cols-[1fr_auto_1fr] items-center gap-3">
          {/* Left — burger (mobile) + inline nav (desktop) */}
          <div className="flex items-center gap-3 sm:gap-4 justify-self-start">
            <button
              type="button"
              onClick={() => setMobileOpen(true)}
              className="md:hidden bz-label text-[var(--bz-amber)] flex items-center hover:opacity-80 transition-opacity"
              aria-label="Open menu"
              aria-expanded={mobileOpen}
              aria-controls="bz-mobile-menu"
            >
              <Menu size={20} aria-hidden="true" />
            </button>

            <nav
              className="hidden md:flex items-center gap-5 lg:gap-6"
              aria-label="Primary"
            >
              {nav.map((n) => (
                <Link
                  key={`${n.label}-${n.href}`}
                  to={n.href}
                  className="bz-label text-[var(--bz-amber)] text-xs font-bold tracking-[0.2em] hover:opacity-80 transition-opacity"
                >
                  {n.label}
                </Link>
              ))}
            </nav>
          </div>

          {/* Centered logo / monogram — sits in the grid's center column so it
              stays optically centered between nav and actions, and the bar
              grows to fit it (no overlap from absolute positioning). */}
          <Link
            to="/"
            aria-label={brandName}
            className="justify-self-center flex items-center justify-center max-w-[42vw] md:max-w-[30vw]"
          >
            {logoUrl ? (
              <img
                src={logoUrl}
                alt={brandName}
                className={
                  logoShaped ? "" : "h-9 sm:h-11 w-auto max-h-12 object-contain"
                }
                style={logoStyle}
              />
            ) : (
              <span className="bz-heading text-lg sm:text-2xl text-[var(--bz-amber)] truncate">
                {brandName}
              </span>
            )}
          </Link>

          {/* Right — search + cart */}
          <div className="flex items-center gap-3 sm:gap-4 justify-self-end">
            {showSearch && (
              <Link
                to="/search"
                className="bz-label text-[var(--bz-amber)] hover:opacity-80 transition-opacity"
                aria-label="Search"
              >
                <Search size={18} aria-hidden="true" />
              </Link>
            )}
            {showCart && (
              <button
                type="button"
                onClick={() => setCartOpen(true)}
                aria-haspopup="dialog"
                aria-expanded={cartOpen}
                className="bz-label text-[var(--bz-amber)] flex items-center gap-2 hover:opacity-80 transition-opacity relative"
                aria-label={`Cart (${itemCount} items)`}
              >
                <span className="hidden sm:inline text-xs font-bold tracking-[0.2em]">
                  {localized(locale, "CART", "السلة")}
                </span>
                <ShoppingBag size={18} aria-hidden="true" />
                {itemCount > 0 && (
                  <span className="absolute -top-1 -end-2 sm:-end-3 w-4 h-4 sm:w-5 sm:h-5 rounded-full bg-[var(--bz-amber)] text-[var(--bz-dark)] text-[9px] sm:text-[10px] font-bold flex items-center justify-center">
                    {itemCount}
                  </span>
                )}
              </button>
            )}
          </div>
        </div>
      </header>

      {/* Mobile menu — off-canvas slide-in DRAWER (not a full-screen page).
          Always mounted so it animates both IN and OUT. A dimmed, click-to-close
          scrim sits behind it; the panel slides from the same side as the burger
          (start side, RTL-aware); the links stagger in. */}
      {/* Scrim */}
      <div
        aria-hidden="true"
        onClick={() => setMobileOpen(false)}
        className={`md:hidden fixed inset-0 z-[55] bg-black/50 backdrop-blur-[2px] transition-opacity duration-300 ${
          mobileOpen ? "opacity-100" : "opacity-0 pointer-events-none"
        }`}
      />
      {/* Drawer panel */}
      <aside
        id="bz-mobile-menu"
        role="dialog"
        aria-modal="true"
        aria-label="Site navigation"
        aria-hidden={!mobileOpen}
        className={`md:hidden fixed inset-y-0 start-0 z-[60] flex w-[80%] max-w-[320px] flex-col bg-[var(--bz-dark)] shadow-2xl transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] ${
          mobileOpen ? "translate-x-0" : "-translate-x-full rtl:translate-x-full"
        }`}
      >
        {/* Drawer header — brand + close */}
        <div className="flex items-center justify-between gap-3 px-5 py-4 border-b border-[var(--bz-amber)]/15">
          <span className="bz-heading text-xl text-[var(--bz-amber)] truncate">
            {brandName}
          </span>
          <button
            type="button"
            onClick={() => setMobileOpen(false)}
            className="-me-1 p-1 text-[var(--bz-amber)] hover:opacity-80 transition-opacity"
            aria-label="Close menu"
          >
            <X size={24} aria-hidden="true" />
          </button>
        </div>
        {/* Nav links — each slides + fades in with a small stagger */}
        <nav className="flex flex-col gap-1 overflow-y-auto px-3 py-4">
          {nav.map((n, i) => (
            <Link
              key={`mob-${n.label}-${n.href}`}
              to={n.href}
              onClick={() => setMobileOpen(false)}
              style={{ transitionDelay: mobileOpen ? `${120 + i * 55}ms` : "0ms" }}
              className={`bz-heading rounded-xl px-3 py-2.5 text-2xl text-[var(--bz-amber)] transition-[opacity,transform,background-color] duration-300 hover:bg-[var(--bz-amber)]/10 ${
                mobileOpen
                  ? "translate-x-0 opacity-100"
                  : "-translate-x-3 rtl:translate-x-3 opacity-0"
              }`}
            >
              {n.label}
            </Link>
          ))}
        </nav>
      </aside>

      {/* Mobile bottom tab bar — V2 parity: primary actions within thumb reach.
          Navy band reads as a continuation of the dark header; amber tints the
          active tab. The WhatsApp FAB is raised clear of this bar (in ThemeApp)
          so it never covers a tab — the reported "icon covers حسابي" bug. */}
      <nav
        aria-label="Primary mobile navigation"
        className="md:hidden fixed bottom-0 inset-x-0 z-40 bg-[var(--bz-navy)] border-t border-[var(--bz-amber)]/20 shadow-[0_-2px_8px_rgba(0,0,0,0.15)]"
        style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
      >
        <div className="flex items-stretch justify-around h-14">
          {bottomTabs.map((tab) => {
            const isCart = tab.action === "cart";
            const on = !isCart && tabActive(tab.to);
            const tabClass = `flex flex-col items-center justify-center gap-0.5 flex-1 min-w-0 h-full transition-colors ${
              on ? "text-[var(--bz-amber)]" : "text-[var(--bz-cream)]"
            }`;
            const inner = (
              <>
                <span className="relative">
                  <tab.icon
                    size={20}
                    className={on ? "stroke-[2.5]" : ""}
                    aria-hidden="true"
                  />
                  {tab.badge && tab.badge > 0 ? (
                    <span className="absolute -top-1 -end-1 w-4 h-4 rounded-full bg-[var(--bz-amber)] text-[var(--bz-dark)] text-[9px] font-bold flex items-center justify-center">
                      {tab.badge}
                    </span>
                  ) : null}
                </span>
                <span className="block w-full text-center text-[9px] tracking-normal uppercase truncate px-0.5 leading-tight font-medium">
                  {tab.label}
                </span>
              </>
            );
            // Cart tab opens the slide-in drawer (a button); the rest navigate.
            return isCart ? (
              <button
                key="cart-tab"
                type="button"
                onClick={() => setCartOpen(true)}
                aria-haspopup="dialog"
                aria-expanded={cartOpen}
                aria-label={tab.label}
                className={tabClass}
              >
                {inner}
              </button>
            ) : (
              <Link
                key={tab.to}
                to={tab.to}
                aria-label={tab.label}
                aria-current={on ? "page" : undefined}
                className={tabClass}
              >
                {inner}
              </Link>
            );
          })}
        </div>
      </nav>

      {/* Slide-in mini-cart — opened by the cart icon / cart tab. */}
      <BzCartDrawer open={cartOpen} onClose={() => setCartOpen(false)} />
    </div>
  );
}
