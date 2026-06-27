"use client";

import { useEffect, useRef, useState } from "react";
import {
  Link,
  logoImgStyle,
  useCart,
  useCollections,
  useCustomer,
  useLocale,
  useNavigation,
  useResolvedSettings,
  useShop,
  useThemeSettings,
  useTranslation,
} from "@numueg/theme-sdk";
import {
  ChevronDown,
  Home,
  Menu,
  Search,
  ShoppingBag,
  Store as StoreIcon,
  User,
  X,
} from "lucide-react";
import {
  asBool,
  asImageUrl,
  asNumber,
  asString,
  localized,
  readBlocks,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

interface NavLink {
  label: string;
  to: string;
}

/**
 * vionne-header — faithful V3 port of V2 VionneStoreHeader
 * (numu-egyptian-bazaar/src/components/store/vionne/VionneStoreHeader.tsx),
 * re-plumbed onto the V3 SDK seam.
 *
 * The erwanaa.com-inspired chrome: a DARK grey (`vn-header` = --vn-surface-dark)
 * sticky bar with white text that CONDENSES (blurred translucent) once the page
 * scrolls past 12px and HIDES on scroll-down (reveal on scroll-up). Layout:
 *   - left cluster  — mobile hamburger + a hover "Collections" dropdown (from
 *     `useCollections`) + a slim nav (Shop / About / Contact) in `vn-label`;
 *   - centered logo — absolutely centered, `vn-heading tracking-[0.32em]
 *     uppercase` (or the merchant's logo image), inline-editable brand name;
 *   - right cluster — Search + Account + Cart (with a white count badge).
 * Below md a slide-in `vn-drawer` and a fixed `vn-dock` bottom bar mirror the
 * V2 mobile experience.
 *
 * Settings: brand_name, logo_url, show_announcement, announcement_text,
 * show_search, show_cart, show_account, show_mobile_dock, enable_hide_on_scroll,
 * nav_shop_label, nav_about_label, nav_contact_label, collections_label.
 * Nav links come from `nav_item` blocks (fallback to the V2 set:
 * Shop / About / Contact).
 */
export default function VionneHeader({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const shop = useShop();
  const { cart } = useCart();
  const { collections } = useCollections();
  const customer = useCustomer();
  const themeSettings = useThemeSettings();
  const locale = useLocale();
  const { t } = useTranslation();
  const globals = (themeSettings.global_settings ?? {}) as Record<string, unknown>;

  const brandName =
    asString(s.brand_name) ||
    asString(globals.brand_name) ||
    shop?.name ||
    "VIONNE";

  // Logo: the W1 global `logo` is the source of truth (one upload drives header
  // + footer). Legacy per-section `logo_url`, the old global key, and the live
  // store logo stay as read-compat fallbacks so a store that set a logo before
  // the W1 hoist still renders unchanged.
  const logoUrl =
    asImageUrl(globals.logo) ||
    asImageUrl(s.logo_url) ||
    asImageUrl(globals.logo_url) ||
    shop?.logo_url ||
    "";
  const logoWidth = asNumber(globals.logo_width, 0);

  // Merchant-chosen logo appearance, now an ENGINE-LEVEL feature: shape + size
  // live in GLOBAL settings and the shaping is computed by the SDK's shared
  // `logoImgStyle` (inline styles, GIF-safe) so every theme renders it the same
  // way. `none` keeps the original artwork (theme's own sizing + logo_width);
  // the shapes crop a 1:1 box.
  const logoShape = asString(globals.logo_shape) || "none";
  const logoSize = asString(globals.logo_size) || "small";
  const logoShaped = logoShape !== "none";
  const logoStyle = logoImgStyle(logoShape, logoSize);

  const announcement = asString(s.announcement_text);
  // Default OFF — V2 Vionne ships no header announcement strip (it was
  // superseded by the standalone marquee section); kept editable but hidden
  // unless the merchant opts in.
  const showAnnouncement = asBool(s.show_announcement, false);
  const showSearch = asBool(s.show_search, true);
  const showCart = asBool(s.show_cart, true);
  const showAccount = asBool(s.show_account, true);
  const showMobileDock = asBool(s.show_mobile_dock, true);
  // Hide-on-scroll: the W1 global wins (theme-wide), falling back to the legacy
  // per-section setting, default true (V2 parity).
  const enableHideOnScroll = asBool(
    globals.enableHideOnScroll,
    asBool(s.enable_hide_on_scroll, true),
  );

  const collectionsLabel =
    asString(s.collections_label) ||
    t("nav.collections", localized(locale, "COLLECTIONS", "التشكيلات"));

  // Nav source precedence (§5 hide-page → nav): (1) the merchant's platform menu
  // via useNavigation(handle) — the SDK drops items whose target CMS page is
  // unpublished/deleted (target_visible:false), so hidden-page links vanish
  // automatically from the header + drawer; (2) else editor `nav_item` blocks;
  // (3) else the V2 Vionne default set (Shop / About / Contact).
  const headerMenuHandle = asString(s.header_menu_handle) || "main-menu";
  const { items: menuItems } = useNavigation(headerMenuHandle);
  const navBlocks = readBlocks(instance, "nav_item");
  const nav: NavLink[] =
    menuItems.length > 0
      ? menuItems.map((it) => ({ label: it.title, to: it.url || "/" }))
      : navBlocks.length > 0
        ? navBlocks
            .map((r) => ({ label: asString(r.label), to: asString(r.href) || "/" }))
            .filter((n) => n.label)
        : [
            { label: asString(s.nav_shop_label) || t("nav.shop", localized(locale, "SHOP", "تسوّقي")), to: "/products" },
            { label: asString(s.nav_about_label) || t("nav.about", localized(locale, "ABOUT", "عن المتجر")), to: "/pages/about" },
            { label: asString(s.nav_contact_label) || t("nav.contact", localized(locale, "CONTACT", "تواصلي")), to: "/pages/contact" },
          ];

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [hidden, setHidden] = useState(false);
  const [condensed, setCondensed] = useState(false);
  const [dockRevealed, setDockRevealed] = useState(false);
  const [collectionsOpen, setCollectionsOpen] = useState(false);
  const lastScroll = useRef(0);

  const cartCount = cart?.items?.reduce((sum, it) => sum + it.quantity, 0) ?? 0;
  const hasCollections = collections.length > 0;

  // Condense + hide-on-scroll (V2 parity).
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onScroll = () => {
      const y = window.scrollY;
      setCondensed(y > 12);
      if (enableHideOnScroll && y > 80) {
        setHidden(y > lastScroll.current);
      } else {
        setHidden(false);
      }
      lastScroll.current = y;
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [enableHideOnScroll]);

  // Reveal the mobile dock after a short delay (Erwanaa-like).
  useEffect(() => {
    if (!showMobileDock) return;
    const t = window.setTimeout(() => setDockRevealed(true), 600);
    return () => window.clearTimeout(t);
  }, [showMobileDock]);

  // Lock body scroll while the drawer is open.
  useEffect(() => {
    if (typeof document === "undefined") return;
    document.body.style.overflow = drawerOpen ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [drawerOpen]);

  const accountHref = customer ? "/account" : "/account";

  return (
    <div data-vionne-section={sectionId}>
      {/* Optional announcement strip (off by default — matches V2). */}
      {showAnnouncement && announcement && (
        <div className="bg-[var(--vn-ink,#050505)] text-white text-center py-2 vn-label">
          <InlineEditable
            sectionId={sectionId}
            settingKey="announcement_text"
            value={announcement}
          />
        </div>
      )}

      {/* Sticky header */}
      <header
        className={`vn-header sticky top-0 z-50 ${hidden ? "is-hidden" : ""} ${condensed ? "is-condensed" : ""}`}
      >
        <div className="container mx-auto px-4 h-14 md:h-16 flex items-center justify-between gap-3 relative">
          {/* Left cluster */}
          <div className="flex items-center gap-3 md:gap-5 shrink-0">
            <button
              type="button"
              onClick={() => setDrawerOpen(true)}
              className="md:hidden vn-label flex items-center hover:opacity-80 transition-opacity"
              aria-label="Open menu"
            >
              <Menu size={20} />
            </button>

            {hasCollections && (
              <div
                className="hidden md:block relative"
                onMouseEnter={() => setCollectionsOpen(true)}
                onMouseLeave={() => setCollectionsOpen(false)}
              >
                <button
                  type="button"
                  className="vn-label flex items-center gap-1 hover:opacity-80 transition-opacity"
                  aria-expanded={collectionsOpen}
                >
                  {collectionsLabel}
                  <ChevronDown
                    size={12}
                    className={`transition-transform ${collectionsOpen ? "rotate-180" : ""}`}
                  />
                </button>
                {collectionsOpen && (
                  <div className="absolute top-full start-0 mt-2 min-w-[200px] bg-white text-[var(--vn-ink)] shadow-lg border border-[var(--vn-border)] overflow-hidden z-50">
                    <Link
                      to="/collections"
                      className="block px-4 py-3 vn-label text-[11px] border-b border-[var(--vn-border)] hover:bg-[var(--vn-band)] transition-colors"
                    >
                      {t("nav.all_collections", localized(locale, "All collections", "كل التشكيلات"))}
                    </Link>
                    {collections.map((cat) => (
                      <Link
                        key={cat.id}
                        to={`/collections/${cat.slug}`}
                        className="block px-4 py-2 text-sm font-medium hover:bg-[var(--vn-band)] transition-colors"
                      >
                        {cat.name}
                      </Link>
                    ))}
                  </div>
                )}
              </div>
            )}

            <nav className="hidden md:flex items-center gap-5">
              {nav.map((link, i) => (
                <Link
                  key={`${link.to}-${link.label}-${i}`}
                  to={link.to}
                  className="vn-label hover:opacity-80 transition-opacity"
                >
                  {link.label}
                </Link>
              ))}
            </nav>
          </div>

          {/* Centered logo */}
          <Link
            to="/"
            className="absolute start-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 max-w-[55%] md:max-w-[35%] truncate text-center"
            aria-label={brandName}
          >
            {logoUrl ? (
              <img
                src={logoUrl}
                alt={brandName}
                className={
                  logoShaped
                    ? "inline-block"
                    : logoWidth
                      ? "inline-block"
                      : "h-7 md:h-8 w-auto inline-block"
                }
                style={
                  logoShaped
                    ? logoStyle
                    : logoWidth
                      ? { width: logoWidth, height: "auto" }
                      : undefined
                }
              />
            ) : (
              <span className="vn-heading text-base md:text-lg tracking-[0.32em] uppercase truncate block">
                <InlineEditable sectionId={sectionId} settingKey="brand_name" value={brandName} />
              </span>
            )}
          </Link>

          {/* Right cluster */}
          <div className="flex items-center gap-3 md:gap-4 shrink-0">
            {showSearch && (
              <Link
                to="/search"
                aria-label="Search"
                className="hidden sm:inline-flex hover:opacity-80 transition-opacity"
              >
                <Search size={18} />
              </Link>
            )}
            {showAccount && (
              <Link
                to={accountHref}
                aria-label="Account"
                className="hidden sm:inline-flex hover:opacity-80 transition-opacity"
              >
                <User size={18} />
              </Link>
            )}
            {showCart && (
              <Link
                to="/cart"
                aria-label={`Cart (${cartCount})`}
                className="relative inline-flex items-center hover:opacity-80 transition-opacity"
              >
                <ShoppingBag size={18} />
                {cartCount > 0 && (
                  <span className="absolute -top-1 -end-2 min-w-[16px] h-[16px] px-1 rounded-full bg-white text-[var(--vn-ink)] text-[9px] font-bold flex items-center justify-center">
                    {cartCount}
                  </span>
                )}
              </Link>
            )}
          </div>
        </div>
      </header>

      {/* Mobile drawer */}
      <div
        className={`vn-drawer ${drawerOpen ? "is-open" : ""}`}
        {...(!drawerOpen ? { inert: "" as unknown as undefined } : {})}
      >
        <div className="vn-drawer-overlay" onClick={() => setDrawerOpen(false)} />
        <aside className="vn-drawer-panel">
          <div className="flex items-center justify-between px-5 h-14 border-b border-[var(--vn-border)]">
            <span className="vn-heading tracking-[0.32em] uppercase text-base">{brandName}</span>
            <button
              type="button"
              onClick={() => setDrawerOpen(false)}
              aria-label="Close menu"
              className="hover:opacity-70 transition-opacity"
            >
              <X size={20} />
            </button>
          </div>
          <nav className="flex flex-col p-5 gap-1">
            {nav.map((link, i) => (
              <Link
                key={`drawer-${link.to}-${link.label}-${i}`}
                to={link.to}
                onClick={() => setDrawerOpen(false)}
                className="vn-drawer-item vn-heading text-xl py-2.5 hover:opacity-70 transition-opacity"
                style={{ transitionDelay: drawerOpen ? `${100 + i * 60}ms` : "0ms" }}
              >
                {link.label}
              </Link>
            ))}
            {hasCollections && (
              <div className="vn-drawer-item mt-3" style={{ transitionDelay: drawerOpen ? "320ms" : "0ms" }}>
                <span className="vn-eyebrow block mb-2">{collectionsLabel}</span>
                <div className="flex flex-col gap-1">
                  <Link
                    to="/collections"
                    onClick={() => setDrawerOpen(false)}
                    className="text-sm py-1.5 hover:opacity-70"
                  >
                    {t("nav.all_collections", localized(locale, "All collections", "كل التشكيلات"))}
                  </Link>
                  {collections.map((cat) => (
                    <Link
                      key={cat.id}
                      to={`/collections/${cat.slug}`}
                      onClick={() => setDrawerOpen(false)}
                      className="text-sm py-1.5 hover:opacity-70"
                    >
                      {cat.name}
                    </Link>
                  ))}
                </div>
              </div>
            )}
          </nav>

          {showAccount && (
            <div className="mt-auto px-5 py-5 border-t border-[var(--vn-border)] flex items-center gap-4">
              <Link
                to={accountHref}
                onClick={() => setDrawerOpen(false)}
                className="vn-label flex items-center gap-2 hover:opacity-70"
              >
                <User size={16} />{" "}
                {customer
                  ? t("nav.account", localized(locale, "Account", "حسابي"))
                  : t("nav.sign_in", localized(locale, "Sign in", "تسجيل الدخول"))}
              </Link>
            </div>
          )}
        </aside>
      </div>

      {/* Mobile bottom dock */}
      {showMobileDock && (
        <div className={`vn-dock md:hidden ${dockRevealed && !drawerOpen ? "is-revealed" : ""}`}>
          <button type="button" onClick={() => setDrawerOpen(true)} className="vn-dock-btn">
            <Menu size={18} />
            {t("nav.menu", localized(locale, "Menu", "القائمة"))}
          </button>
          <Link to="/" className="vn-dock-btn">
            <Home size={18} />
            {t("nav.home", localized(locale, "Home", "الرئيسية"))}
          </Link>
          <Link to="/search" className="vn-dock-btn">
            <Search size={18} />
            {t("nav.search", localized(locale, "Search", "بحث"))}
          </Link>
          <Link to="/products" className="vn-dock-btn">
            <StoreIcon size={18} />
            {t("nav.shop", localized(locale, "Shop", "تسوّقي"))}
          </Link>
          <Link to="/cart" className="vn-dock-btn">
            <ShoppingBag size={18} />
            {t("nav.cart", localized(locale, "Cart", "السلة"))}
            {cartCount > 0 && <span className="vn-dock-badge">{cartCount}</span>}
          </Link>
        </div>
      )}
    </div>
  );
}
