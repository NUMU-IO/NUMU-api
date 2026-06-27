"use client";

import { useEffect, useState } from "react";
import {
  Link,
  Logo,
  useCart,
  useCollections,
  useCustomer,
  useLocale,
  useNavigation,
  useResolvedSettings,
  useShop,
  useThemeSettings,
} from "@numueg/theme-sdk";
import { motion, AnimatePresence } from "framer-motion";
import { ChevronDown, Home, Menu, Search, ShoppingBag, User, X } from "lucide-react";
import {
  asImageUrl,
  asString,
  localized,
  readBlocks,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

interface NavItem {
  label: string;
  href: string;
  hasDropdown?: boolean;
}

/**
 * gilded-header — faithful V3 port of V2 GildedHeader
 * (numu-egyptian-bazaar/src/components/store/gilded-glamour-boutique/GildedHeader.tsx).
 *
 * A STICKY, frosted-white header (`bg-card/95 backdrop-blur-sm border-b z-50`)
 * that slides down on load. Optional BLACK announcement bar (`bg-foreground
 * text-card`, centered uppercase tracking-[0.15em]). Row `h-16 container`:
 *   - logo on the START — the SDK engine `<Logo>` (reads `logo_url`/`logo_shape`/
 *     `logo_size` GLOBAL settings) with a GOLD wordmark fallback (uppercase,
 *     tracking-[0.2em], `text-gold`);
 *   - desktop nav (gap-8) — links `text-[13px] tracking-[0.2em] hover:text-gold`;
 *     the COLLECTIONS link reveals a hover dropdown (`bg-card border shadow-xl`)
 *     listing the store's collections;
 *   - end icons (desktop): language toggle, search → /search, cart → /cart with
 *     a count badge (`bg-primary`), account → /profile (signed-in) | /auth.
 * On mobile: a hamburger toggles an animated gilded dropdown menu, and a FIXED
 * bottom tab bar (Home / Search / Cart / Account, active tab tinted gold) gives
 * the customer thumb-reachable actions.
 *
 * Settings: brand_name, logo_url (engine logo), show_announcement,
 * announcement_text, show_search, show_cart. Nav links come from the merchant's
 * `main-menu` (Navigation manager) → `nav_item` blocks → the V2 fallback set
 * (Home / Shop / Collections / Lookbook / About / FAQ / Contact).
 */
export default function GildedHeader({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const shop = useShop();
  const { cart } = useCart();
  const { collections } = useCollections();
  const customer = useCustomer();
  const themeSettings = useThemeSettings();
  const locale = useLocale();

  const brandName =
    asString(s.brand_name) ||
    asString(themeSettings.global_settings?.brand_name) ||
    shop?.name ||
    "SAW SAW";

  // Engine logo: explicit setting → global → shop. Shape/size from GLOBAL
  // settings are handled by the SDK <Logo>; we just pass the URL + fallback.
  const logoUrl =
    asImageUrl(s.logo_url) ||
    asImageUrl(themeSettings.global_settings?.logo_url) ||
    shop?.logo_url ||
    "";

  const announcement =
    asString(s.announcement_text) ||
    localized(
      locale,
      "Complimentary shipping on all orders",
      "شحن مجاني على كل الطلبات",
    );
  const showAnnouncement = (s.show_announcement as boolean) !== false;
  const showSearch = (s.show_search as boolean) !== false;
  const showCart = (s.show_cart as boolean) !== false;

  const accountHref = customer ? "/profile" : "/auth";

  // Nav source priority: merchant `main-menu` (Navigation manager) → theme
  // `nav_item` blocks → the V2 Gilded fallback set. The /collections entry
  // gets the hover dropdown.
  const mainMenu = useNavigation("main-menu");
  const navBlocks = readBlocks(instance, "nav_item");
  const nav: NavItem[] =
    mainMenu.items.length > 0
      ? mainMenu.items
          .map((i) => ({
            label: i.title,
            href: i.url || "/",
            hasDropdown: (i.url || "") === "/collections",
          }))
          .filter((n) => n.label)
      : navBlocks.length > 0
        ? navBlocks
            .map((r) => ({
              label: asString(r.label),
              href: asString(r.href) || "/",
              hasDropdown: asString(r.href) === "/collections",
            }))
            .filter((n) => n.label)
        : [
            { label: localized(locale, "HOME", "الرئيسية"), href: "/" },
            { label: localized(locale, "SHOP", "المتجر"), href: "/products" },
            {
              label: localized(locale, "COLLECTIONS", "التشكيلات"),
              href: "/collections",
              hasDropdown: true,
            },
            { label: localized(locale, "LOOKBOOK", "الكتالوج"), href: "/pages/lookbook" },
            { label: localized(locale, "ABOUT", "عن المتجر"), href: "/pages/about" },
            { label: localized(locale, "FAQ", "الأسئلة الشائعة"), href: "/pages/faq" },
            { label: localized(locale, "CONTACT", "تواصل"), href: "/pages/contact" },
          ];

  const [menuOpen, setMenuOpen] = useState(false);
  const [collectionsOpen, setCollectionsOpen] = useState(false);
  const [mobileCollectionsOpen, setMobileCollectionsOpen] = useState(false);
  const [pathname, setPathname] = useState("");

  // Active-tab detection for the bottom bar. The SDK has no location hook, so
  // we read window.location.pathname client-side (SSR renders no active tint).
  useEffect(() => {
    if (typeof window === "undefined") return;
    setPathname(window.location.pathname);
  }, []);

  const itemCount = cart?.items?.reduce((n, it) => n + it.quantity, 0) ?? 0;

  const isAr = (locale || "").toLowerCase().startsWith("ar");

  const navLinkClass =
    "text-[13px] font-medium tracking-[0.2em] text-foreground hover:text-gold transition-colors";

  const tabActive = (to: string) => {
    if (!pathname) return false;
    if (to === "/") return pathname === "/";
    return pathname === to || pathname.startsWith(`${to}/`);
  };

  /** Gold wordmark fallback (uppercase, wide-tracked) when no logo image. */
  const brandFallback = (
    <span className="text-lg font-bold tracking-[0.2em] uppercase text-gold">
      <InlineEditable sectionId={sectionId} settingKey="brand_name" value={brandName} />
    </span>
  );

  return (
    <>
      <motion.header
        initial={{ y: -80, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 0.5, ease: "easeOut" }}
        className="sticky top-0 left-0 right-0 z-50 bg-card/95 backdrop-blur-sm border-b"
        data-gilded-section={sectionId}
      >
        {/* Announcement bar */}
        {showAnnouncement && announcement && (
          <div className="bg-foreground text-card text-center py-2 text-xs font-medium tracking-[0.15em] uppercase">
            <InlineEditable
              sectionId={sectionId}
              settingKey="announcement_text"
              value={announcement}
            />
          </div>
        )}

        {/* Main header */}
        <div>
          <div className="container mx-auto flex items-center justify-between h-16 px-4">
            {/* Logo */}
            <Link to="/" className="flex items-center gap-3" aria-label={brandName}>
              <Logo
                src={logoUrl || null}
                alt={brandName}
                className="h-10 w-auto object-contain"
                fallback={brandFallback}
              />
            </Link>

            {/* Desktop Nav */}
            <nav className="hidden md:flex items-center gap-8">
              {nav.map((link, i) => (
                <div key={`${link.label}-${i}`} className="relative">
                  {link.hasDropdown ? (
                    <button
                      type="button"
                      onMouseEnter={() => setCollectionsOpen(true)}
                      onMouseLeave={() => setCollectionsOpen(false)}
                      className={`${navLinkClass} inline-flex items-center gap-1`}
                    >
                      {link.label}
                      <ChevronDown
                        size={12}
                        className={`transition-transform ${collectionsOpen ? "rotate-180" : ""}`}
                      />
                    </button>
                  ) : (
                    <Link to={link.href} className={navLinkClass}>
                      {link.label}
                    </Link>
                  )}

                  {/* Collections Dropdown */}
                  {link.hasDropdown && (
                    <div
                      onMouseEnter={() => setCollectionsOpen(true)}
                      onMouseLeave={() => setCollectionsOpen(false)}
                      className={`absolute top-full start-0 pt-4 transition-opacity ${
                        collectionsOpen ? "opacity-100 visible" : "opacity-0 invisible"
                      }`}
                    >
                      <div className="bg-card border border-border shadow-xl py-4 min-w-[220px]">
                        <div className="px-4 pb-2 mb-2 border-b border-border">
                          <Link
                            to="/collections"
                            className="text-xs font-bold tracking-[0.15em] uppercase text-gold hover:underline"
                          >
                            {localized(locale, "All Collections", "كل التشكيلات")}
                          </Link>
                        </div>
                        {collections.length > 0 ? (
                          collections.map((cat) => (
                            <Link
                              key={cat.id}
                              to={`/collections/${cat.slug}`}
                              className="block px-4 py-2 text-xs tracking-[0.1em] uppercase text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
                            >
                              {cat.name}
                            </Link>
                          ))
                        ) : (
                          <p className="px-4 py-2 text-xs text-muted-foreground">
                            {localized(locale, "No collections yet", "لا توجد تشكيلات بعد")}
                          </p>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </nav>

            {/* Icons — desktop. Same actions live in the mobile bottom bar. */}
            <div className="hidden md:flex items-center gap-4">
              <Link
                to={isAr ? "/?locale=en" : "/?locale=ar"}
                className="text-foreground hover:text-gold transition-colors text-[11px] font-semibold tracking-[0.15em] uppercase"
                aria-label="Language"
                dir="ltr"
              >
                {isAr ? "EN" : "AR"}
              </Link>
              {showSearch && (
                <Link
                  to="/search"
                  className="text-foreground hover:text-gold transition-colors"
                  aria-label={localized(locale, "Search", "بحث")}
                >
                  <Search size={20} />
                </Link>
              )}
              {showCart && (
                <Link
                  to="/cart"
                  className="text-foreground hover:text-gold transition-colors relative"
                  aria-label={`${localized(locale, "Cart", "السلة")} (${itemCount})`}
                >
                  <ShoppingBag size={20} />
                  {itemCount > 0 && (
                    <span className="absolute -top-1 -end-1 w-4 h-4 bg-primary text-primary-foreground text-[10px] flex items-center justify-center font-bold">
                      {itemCount}
                    </span>
                  )}
                </Link>
              )}
              <Link
                to={accountHref}
                className="text-foreground hover:text-gold transition-colors"
                aria-label={localized(locale, "Account", "حسابي")}
              >
                <User size={20} />
              </Link>
            </div>

            {/* Mobile: menu button next to the logo */}
            <button
              type="button"
              className="md:hidden text-foreground"
              onClick={() => setMenuOpen(!menuOpen)}
              aria-label="Menu"
              aria-expanded={menuOpen}
            >
              {menuOpen ? <X size={24} /> : <Menu size={24} />}
            </button>
          </div>
        </div>

        {/* Mobile Menu */}
        <AnimatePresence>
          {menuOpen && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              className="md:hidden bg-card border-b overflow-hidden"
            >
              <nav className="flex flex-col items-center gap-4 py-8">
                {nav.map((link, i) =>
                  link.hasDropdown ? (
                    <div key={`mob-${link.label}-${i}`} className="w-full text-center">
                      <button
                        type="button"
                        dir="ltr"
                        onClick={() => setMobileCollectionsOpen(!mobileCollectionsOpen)}
                        className={`${navLinkClass} inline-flex items-center justify-center gap-1`}
                      >
                        {link.label}
                        <ChevronDown
                          size={12}
                          className={`transition-transform ${mobileCollectionsOpen ? "rotate-180" : ""}`}
                        />
                      </button>
                      <AnimatePresence>
                        {mobileCollectionsOpen && (
                          <motion.div
                            initial={{ opacity: 0, height: 0 }}
                            animate={{ opacity: 1, height: "auto" }}
                            exit={{ opacity: 0, height: 0 }}
                            className="mt-2"
                          >
                            <Link
                              to="/collections"
                              onClick={() => setMenuOpen(false)}
                              className="block py-2 text-xs font-bold tracking-[0.1em] uppercase text-gold"
                            >
                              {localized(locale, "All Collections", "كل التشكيلات")}
                            </Link>
                            {collections.map((cat) => (
                              <Link
                                key={cat.id}
                                to={`/collections/${cat.slug}`}
                                onClick={() => setMenuOpen(false)}
                                className="block py-2 text-xs tracking-[0.1em] uppercase text-muted-foreground"
                              >
                                {cat.name}
                              </Link>
                            ))}
                          </motion.div>
                        )}
                      </AnimatePresence>
                    </div>
                  ) : (
                    <Link
                      key={`mob-${link.label}-${i}`}
                      to={link.href}
                      onClick={() => setMenuOpen(false)}
                      className={navLinkClass}
                    >
                      {link.label}
                    </Link>
                  ),
                )}
                <Link
                  to={accountHref}
                  onClick={() => setMenuOpen(false)}
                  className={navLinkClass}
                >
                  {localized(locale, "ACCOUNT", "حسابي")}
                </Link>
                <Link
                  to={isAr ? "/?locale=en" : "/?locale=ar"}
                  onClick={() => setMenuOpen(false)}
                  className={`${navLinkClass} mt-2 inline-flex items-center gap-2`}
                  dir="ltr"
                >
                  {isAr ? "EN · English" : "AR · العربية"}
                </Link>
              </nav>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.header>

      {/* Mobile bottom nav — sibling of the motion.header (not a child), so the
          header's framer transform doesn't trap this fixed bar. Active-tab tint
          is the gold accent. */}
      <nav
        aria-label="Primary mobile navigation"
        className="md:hidden fixed bottom-0 inset-x-0 z-50 bg-card border-t border-border shadow-[0_-2px_8px_rgba(0,0,0,0.06)]"
        style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
      >
        <div className="flex items-stretch justify-around h-14 px-0">
          {[
            { label: localized(locale, "Home", "الرئيسية"), icon: Home, to: "/" },
            ...(showSearch
              ? [{ label: localized(locale, "Search", "بحث"), icon: Search, to: "/search" }]
              : []),
            ...(showCart
              ? [
                  {
                    label: localized(locale, "Cart", "السلة"),
                    icon: ShoppingBag,
                    to: "/cart",
                    badge: itemCount,
                  },
                ]
              : []),
            { label: localized(locale, "Account", "حسابي"), icon: User, to: accountHref },
          ].map((item, idx) => {
            const active = tabActive(item.to);
            const Icon = item.icon;
            return (
              <Link
                key={`tab-${item.label}-${idx}`}
                to={item.to}
                aria-label={item.label}
                aria-current={active ? "page" : undefined}
                className={`flex flex-col items-center justify-center gap-0.5 flex-1 min-w-0 h-full transition-colors ${
                  active ? "text-gold" : "text-foreground"
                }`}
              >
                <div className="relative">
                  <Icon size={20} className={active ? "stroke-[2.5]" : ""} />
                  {"badge" in item && item.badge && item.badge > 0 ? (
                    <span className="absolute -top-1 -end-1 w-4 h-4 bg-primary text-primary-foreground text-[10px] flex items-center justify-center font-bold rounded-full">
                      {item.badge}
                    </span>
                  ) : null}
                </div>
                <span
                  className={`block w-full text-center text-[9px] uppercase truncate px-0.5 leading-tight ${
                    active ? "font-semibold" : "font-medium"
                  }`}
                >
                  {item.label}
                </span>
              </Link>
            );
          })}
        </div>
      </nav>
    </>
  );
}
