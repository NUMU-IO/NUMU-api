"use client";

import {
  Link,
  useCollections,
  useLocale,
  useResolvedSettings,
  useShop,
  useThemeSettings,
} from "@numueg/theme-sdk";
import type { ComponentType } from "react";
import { Facebook, MessageCircle, Music2, Twitter, Youtube } from "lucide-react";
import {
  asImageUrl,
  asString,
  localized,
  readBlocks,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

interface FooterLink {
  label: string;
  href: string;
}
interface FooterColumn {
  title: string;
  links: FooterLink[];
}

/** Inline Instagram SVG (lucide-react dropped the named export) — ported
 *  verbatim from V2 GildedFooter. */
const InstagramIcon = ({ size = 14 }: { size?: string | number }) => (
  <svg
    viewBox="0 0 24 24"
    width={size}
    height={size}
    fill="none"
    stroke="currentColor"
    strokeWidth={2}
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <rect width="20" height="20" x="2" y="2" rx="5" ry="5" />
    <path d="M16 11.37A4 4 0 1 1 12.63 8 4 4 0 0 1 16 11.37z" />
    <line x1="17.5" x2="17.51" y1="6.5" y2="6.5" />
  </svg>
);

type IconComponent = ComponentType<{ size?: string | number }>;

/**
 * gilded-footer — faithful V3 port of V2 GildedFooter
 * (numu-egyptian-bazaar/src/components/store/gilded-glamour-boutique/GildedFooter.tsx).
 *
 * A PURE-BLACK footer (`bg-[#000000] text-white`, `pt-8 pb-20 md:pt-12 md:pb-8`
 * — the extra mobile bottom padding clears the fixed mobile bottom nav) with a
 * 4-column grid:
 *   (1) brand — engine logo / gold wordmark + editable blurb + social chips
 *       (gold-bordered circles that fill gold on hover) read from the GLOBAL
 *       social settings (instagram/facebook/tiktok/x/whatsapp/youtube), with a
 *       legacy fallback to `shop.social_links`; whatsapp → wa.me/<digits>;
 *   (2) Shop — All Products + collections;
 *   (3) Info — About / Shipping / Returns / Privacy / Terms / Contact / Track;
 *   (4) Newsletter — gold heading + subtitle + underline-style email input.
 * Bottom bar (`border-t border-[#1A1A1A]`) carries the copyright + "Powered by
 * NUMU".
 *
 * The Shop / Info columns can be overridden via `column` blocks (same seam as
 * the Empire / Lux footers). Settings: brand_name, logo_url, footer_text,
 * show_newsletter, newsletter_title, newsletter_subtitle.
 */
export default function GildedFooter({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const shop = useShop();
  const { collections } = useCollections();
  const themeSettings = useThemeSettings();
  const locale = useLocale();

  const globals = themeSettings.global_settings ?? {};

  const brandName =
    asString(s.brand_name) ||
    asString(globals.brand_name) ||
    shop?.name ||
    "SAW SAW";

  const logoUrl =
    asImageUrl(s.logo_url) ||
    asImageUrl(globals.logo_url) ||
    shop?.logo_url ||
    "";

  const footerText =
    asString(s.footer_text) ||
    localized(
      locale,
      "The Gilded Curator — curated excellence and timeless precision since 2024.",
      "Gilded Curator — تميّز مختار ودقّة خالدة منذ 2024.",
    );

  const showNewsletter = (s.show_newsletter as boolean) !== false;
  const newsletterTitle =
    asString(s.newsletter_title) || localized(locale, "Newsletter", "النشرة البريدية");
  const newsletterSubtitle =
    asString(s.newsletter_subtitle) ||
    localized(
      locale,
      "Join the empire. Be first to know.",
      "انضم إلى الإمبراطورية وكن أول من يعرف.",
    );

  // Socials — read from GLOBAL settings first (Online Store → social_*), then
  // fall back to the legacy store-level `shop.social_links`. Anything the
  // merchant types in the theme editor's Social group flows straight here.
  const storeSocial = (shop?.social_links as Record<string, string> | undefined) ?? {};
  const pickSocial = (globalKey: string, legacyKey: string): string =>
    asString(globals[globalKey]) || asString(storeSocial[legacyKey]);

  const socialIcons: {
    key: string;
    url: string;
    Icon: IconComponent;
    label: string;
  }[] = [
    {
      key: "instagram",
      url: pickSocial("social_instagram", "instagram"),
      Icon: InstagramIcon,
      label: "Instagram",
    },
    {
      key: "facebook",
      url: pickSocial("social_facebook", "facebook"),
      Icon: Facebook,
      label: "Facebook",
    },
    {
      key: "x",
      url: pickSocial("social_x", "twitter"),
      Icon: Twitter,
      label: "X (Twitter)",
    },
    {
      key: "tiktok",
      url: pickSocial("social_tiktok", "tiktok"),
      Icon: Music2,
      label: "TikTok",
    },
    {
      key: "youtube",
      url: pickSocial("social_youtube", "youtube"),
      Icon: Youtube,
      label: "YouTube",
    },
    {
      key: "whatsapp",
      url: pickSocial("social_whatsapp", "whatsapp"),
      Icon: MessageCircle,
      label: "WhatsApp",
    },
  ].filter((soc) => Boolean(soc.url));

  const formatSocialHref = (key: string, url: string): string => {
    if (key === "whatsapp") {
      // Accept either a raw phone ("+20…") or an already-formed wa.me URL.
      if (url.startsWith("http")) return url;
      return `https://wa.me/${url.replace(/\D/g, "")}`;
    }
    return url;
  };

  // Optional override columns from `column` blocks (Empire / Lux seam).
  const overrideColumns: FooterColumn[] = readBlocks(instance, "column")
    .map((r) => {
      const links: FooterLink[] = [];
      for (let i = 1; i <= 7; i++) {
        const label = asString(r[`link${i}_label`]);
        const href = asString(r[`link${i}_href`]);
        if (label && href) links.push({ label, href });
      }
      return { title: asString(r.title), links };
    })
    .filter((c) => c.title && c.links.length > 0);

  // V2 default Shop / Info columns (Shop stays live off the store's
  // collections, exactly like the V2 footer).
  const defaultColumns: FooterColumn[] = [
    {
      title: localized(locale, "Shop", "تسوّق"),
      links: [
        { label: localized(locale, "All Products", "كل المنتجات"), href: "/products" },
        ...collections.slice(0, 3).map((cat) => ({
          label: cat.name,
          href: `/collections/${cat.slug}`,
        })),
      ],
    },
    {
      title: localized(locale, "Info", "معلومات"),
      links: [
        { label: localized(locale, "About", "عن المتجر"), href: "/pages/about" },
        { label: localized(locale, "Shipping", "الشحن"), href: "/pages/shipping" },
        { label: localized(locale, "Returns", "الإرجاع"), href: "/pages/returns" },
        { label: localized(locale, "Privacy", "الخصوصية"), href: "/pages/privacy" },
        { label: localized(locale, "Terms", "الشروط"), href: "/pages/terms" },
        { label: localized(locale, "Contact", "تواصل"), href: "/pages/contact" },
        { label: localized(locale, "Track Order", "تتبع الطلب"), href: "/track" },
      ],
    },
  ];

  const columnsToRender = overrideColumns.length > 0 ? overrideColumns : defaultColumns;

  const goldHeadingClass = "text-[11px] font-bold tracking-[0.2em] uppercase mb-3 md:mb-4 text-gold";

  return (
    <footer
      id="contact"
      // Extra mobile bottom padding (`pb-20`) reserves space for the fixed
      // mobile bottom nav (≈56px + safe-area inset) so the last lines of the
      // footer aren't hidden under it. Desktop has no bottom nav (`md:pb-8`).
      className="bg-[#000000] text-white pt-8 pb-20 md:pt-12 md:pb-8 font-sans"
      data-gilded-section={sectionId}
    >
      <div className="container mx-auto px-4 max-w-6xl">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-6 md:gap-10 mb-6 md:mb-10">
          {/* Brand */}
          <div className="md:pe-8">
            {logoUrl ? (
              <img
                src={logoUrl}
                alt={brandName}
                className="h-7 md:h-8 w-auto object-contain mb-3 md:mb-4"
              />
            ) : (
              <h4 className="text-base md:text-lg font-bold tracking-[0.15em] uppercase mb-3 md:mb-4 text-gold">
                <InlineEditable sectionId={sectionId} settingKey="brand_name" value={brandName} />
              </h4>
            )}
            <p className="text-xs md:text-[13px] leading-relaxed text-muted-foreground">
              <InlineEditable
                sectionId={sectionId}
                settingKey="footer_text"
                value={footerText}
                multiline
              />
            </p>

            {/* Social icons */}
            {socialIcons.length > 0 && (
              <div className="flex gap-2 mt-4 md:mt-6">
                {socialIcons.map(({ key, url, Icon, label }) => (
                  <a
                    key={key}
                    href={formatSocialHref(key, url)}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label={label}
                    title={label}
                    className="w-8 h-8 md:w-9 md:h-9 rounded-full border border-gold/40 flex items-center justify-center text-gold hover:bg-[var(--gilded-gold)] hover:text-black transition-colors"
                  >
                    <Icon size={14} />
                  </a>
                ))}
              </div>
            )}
          </div>

          {/* Shop / Info (or override) columns */}
          {columnsToRender.map((col, ci) => (
            <div key={`${col.title}-${ci}`}>
              <h5 className={goldHeadingClass}>{col.title}</h5>
              <ul className="space-y-2 md:space-y-2.5 text-xs md:text-[13px] text-muted-foreground">
                {col.links.map((link, idx) => (
                  <li key={`${col.title}-${idx}`}>
                    <Link to={link.href} className="hover:text-white transition-colors">
                      {link.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}

          {/* Newsletter */}
          {showNewsletter && (
            <div>
              <h5 className={goldHeadingClass}>
                <InlineEditable
                  sectionId={sectionId}
                  settingKey="newsletter_title"
                  value={newsletterTitle}
                />
              </h5>
              <p className="text-xs md:text-[13px] mb-3 md:mb-4 text-muted-foreground">
                <InlineEditable
                  sectionId={sectionId}
                  settingKey="newsletter_subtitle"
                  value={newsletterSubtitle}
                  multiline
                />
              </p>
              <form
                className="flex border-b border-white/20 pb-2"
                onSubmit={(e) => e.preventDefault()}
              >
                <input
                  type="email"
                  placeholder={localized(locale, "Your email", "بريدك الإلكتروني")}
                  dir="ltr"
                  className="flex-1 bg-transparent border-none text-xs md:text-[13px] text-white placeholder:text-white/40 focus:outline-none focus:ring-0 px-0"
                />
              </form>
            </div>
          )}
        </div>

        {/* Bottom bar */}
        <div className="border-t border-[#1A1A1A] pt-4 md:pt-6 flex flex-col md:flex-row items-start md:items-center justify-between gap-2 md:gap-4">
          <p className="text-[10px] md:text-[11px] text-muted-foreground">
            &copy; {new Date().getFullYear()} {brandName}.{" "}
            {localized(locale, "All rights reserved.", "جميع الحقوق محفوظة.")}
          </p>
          <p className="text-[10px] md:text-[11px] text-muted-foreground">
            {localized(locale, "Powered by NUMU", "مدعوم بواسطة NUMU")}
          </p>
        </div>
      </div>
    </footer>
  );
}
