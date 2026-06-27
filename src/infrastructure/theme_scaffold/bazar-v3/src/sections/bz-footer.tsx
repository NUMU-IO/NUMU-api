"use client";

import { useState } from "react";
import {
  Link,
  useLocale,
  useNavigation,
  useResolvedSettings,
  useShop,
  useThemeSettings,
} from "@numueg/theme-sdk";
import { Facebook, MessageCircle, Music2, Send, Twitter, Youtube } from "lucide-react";
import { asString, localized, readBlocks, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

interface FooterLink {
  label: string;
  href: string;
}

interface FooterColumn {
  title: string;
  links: FooterLink[];
}

const defaultColumns = (locale: string | undefined): FooterColumn[] => [
  {
    title: localized(locale, "SHOP", "تسوّق"),
    links: [
      { label: localized(locale, "All Products", "كل المنتجات"), href: "/products" },
      { label: localized(locale, "New Arrivals", "وصل حديثًا"), href: "/products?sort=newest" },
      { label: localized(locale, "Search", "بحث"), href: "/search" },
    ],
  },
  {
    title: localized(locale, "HELP", "المساعدة"),
    links: [
      { label: localized(locale, "Shipping", "الشحن"), href: "/shipping" },
      { label: localized(locale, "Returns", "الإرجاع"), href: "/returns" },
      { label: localized(locale, "Contact", "تواصل معنا"), href: "/contact" },
    ],
  },
];

/** Inline Instagram glyph (lucide dropped its named export). */
const InstagramIcon = ({ size = 16 }: { size?: number }) => (
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

const SOCIAL_ICONS: Record<
  string,
  ({ size }: { size?: number }) => JSX.Element
> = {
  instagram: InstagramIcon,
  facebook: (p) => <Facebook size={p.size} aria-hidden="true" />,
  twitter: (p) => <Twitter size={p.size} aria-hidden="true" />,
  tiktok: (p) => <Music2 size={p.size} aria-hidden="true" />,
  whatsapp: (p) => <MessageCircle size={p.size} aria-hidden="true" />,
  youtube: (p) => <Youtube size={p.size} aria-hidden="true" />,
};

/**
 * bz-footer — Bazar's chrome footer. Ported from V2 BzStoreFooter:
 * dark base with a wave divider on top, a centered newsletter CTA, a
 * brand block + social row, and configurable link columns (from
 * `column` blocks → fall back to Shop / Help defaults). Socials are
 * read from `store.social_links`. The newsletter is a local
 * subscribe-confirmation (no API), matching the other bazar sections.
 */
export default function BzFooter({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const shop = useShop();
  const themeSettings = useThemeSettings();
  const locale = useLocale();

  const brandName =
    asString(s.brand_name) ||
    asString(themeSettings.global_settings?.brand_name) ||
    shop?.name ||
    "BAZAR";

  const newsletterTitle = asString(s.newsletter_title) || localized(locale, "JOIN THE BAZAR", "انضم لبازار");
  const newsletterCopy =
    asString(s.newsletter_copy) ||
    localized(
      locale,
      "Early access to limited drops, exclusive offers, and the seasonal edit.",
      "وصول مبكر للإصدارات المحدودة والعروض الحصرية وتشكيلة الموسم.",
    );
  const newsletterButton = asString(s.newsletter_button_label) || localized(locale, "SUBSCRIBE", "اشترك");
  const newsletterButtonSuccess =
    asString(s.newsletter_button_success) || localized(locale, "SUBSCRIBED", "تم الاشتراك");
  const footerText =
    asString(s.footer_text) ||
    localized(
      locale,
      "Handpicked essentials, made in Egypt. Bold design, honest prices.",
      "أساسيات مختارة بعناية، صناعة مصرية. تصميم جريء وأسعار صادقة.",
    );

  const configuredColumns: FooterColumn[] = readBlocks(instance, "column")
    .map((r) => {
      const links: FooterLink[] = [];
      for (let i = 1; i <= 4; i++) {
        const label = asString(r[`link${i}_label`]);
        const href = asString(r[`link${i}_href`]);
        if (label && href) links.push({ label, href });
      }
      return { title: asString(r.title), links };
    })
    .filter((c) => c.title && c.links.length > 0);

  // §5: when the merchant builds a NESTED `footer` menu (column parents with
  // child links) in the hub Navigation manager, render those columns — the SDK
  // has already dropped any link whose target CMS page is hidden, and we prune
  // columns left empty. A flat footer menu (the default seed) has no children,
  // so we keep the theme's faithful default columns instead of collapsing them.
  const footerMenu = useNavigation("footer");
  const menuColumns: FooterColumn[] = footerMenu.items.some(
    (i) => (i.children?.length ?? 0) > 0,
  )
    ? footerMenu.items
        .filter((i) => (i.children?.length ?? 0) > 0)
        .map((i) => ({
          title: i.title,
          links: i.children
            .map((c) => ({ label: c.title, href: c.url || "/" }))
            .filter((l) => l.label),
        }))
        .filter((c) => c.title && c.links.length > 0)
    : [];

  const columns =
    menuColumns.length > 0
      ? menuColumns
      : configuredColumns.length > 0
        ? configuredColumns
        : defaultColumns(locale);

  // Social links: the W1 Brand→Social globals win; fall back to the store's
  // `social_links`. `social_x` maps onto the twitter glyph.
  const gs = (themeSettings.global_settings ?? {}) as Record<string, unknown>;
  const globalSocials: Record<string, string> = {
    instagram: asString(gs.social_instagram),
    facebook: asString(gs.social_facebook),
    tiktok: asString(gs.social_tiktok),
    twitter: asString(gs.social_x),
    whatsapp: asString(gs.social_whatsapp),
    youtube: asString(gs.social_youtube),
  };
  const shopSocials =
    (shop?.social_links as Record<string, string> | undefined) ?? {};
  const mergedSocials: Record<string, string> = { ...shopSocials };
  for (const [name, url] of Object.entries(globalSocials)) {
    if (url) mergedSocials[name] = url;
  }
  const socials: Array<{ name: string; url: string }> = Object.entries(
    mergedSocials,
  )
    .map(([name, url]) => ({ name, url: asString(url) }))
    .filter(({ url }) => Boolean(url));

  const formatSocialHref = (name: string, url: string) =>
    name.toLowerCase() === "whatsapp"
      ? `https://wa.me/${url.replace(/\D/g, "")}`
      : url;

  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const handleSubscribe = (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim()) return;
    setSubmitted(true);
    setEmail("");
  };

  return (
    <footer
      className="bg-[var(--bz-dark)] text-[var(--bz-cream)] relative overflow-hidden pb-20 md:pb-0"
      data-bz-section={sectionId}
    >
      {/* Wave top divider */}
      <svg
        viewBox="0 0 1440 60"
        className="w-full block -mt-px"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <path
          d="M0,60 C240,0 480,50 720,20 C960,-10 1200,40 1440,15 L1440,60 Z"
          fill="var(--bz-dark)"
        />
      </svg>

      {/* Newsletter */}
      <div className="container mx-auto px-4 py-12 text-center border-b border-white/10">
        <h3 className="bz-heading text-2xl md:text-3xl text-[var(--bz-amber)] mb-3">
          <InlineEditable
            sectionId={sectionId}
            settingKey="newsletter_title"
            value={newsletterTitle}
          />
        </h3>
        <p className="text-sm opacity-60 mb-6 max-w-md mx-auto">
          <InlineEditable
            sectionId={sectionId}
            settingKey="newsletter_copy"
            value={newsletterCopy}
            multiline
          />
        </p>
        {submitted ? (
          <div
            role="status"
            aria-live="polite"
            className="inline-flex items-center justify-center bz-btn bz-btn-amber h-11 px-6 text-[11px]"
          >
            {newsletterButtonSuccess}
          </div>
        ) : (
          <form
            className="flex gap-2 max-w-sm mx-auto"
            onSubmit={handleSubscribe}
            noValidate
          >
            <label htmlFor="bz-footer-email" className="sr-only">
              {localized(locale, "Email address", "البريد الإلكتروني")}
            </label>
            <input
              id="bz-footer-email"
              type="email"
              required
              placeholder={localized(locale, "Your email", "بريدك الإلكتروني")}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              dir="ltr"
              // min-w-0 lets flex-1 shrink the input below its intrinsic width
              // so the SUBSCRIBE button never gets pushed off-screen on narrow
              // phones (the input otherwise refuses to shrink past ~200px).
              className="min-w-0 flex-1 h-11 px-4 rounded-full bg-white/10 border border-white/20 text-sm placeholder:text-white/30 focus:outline-none focus:border-[var(--bz-amber)] transition-colors"
            />
            <button type="submit" className="bz-btn bz-btn-amber h-11 px-6 text-[11px]">
              {newsletterButton}
            </button>
          </form>
        )}
      </div>

      {/* Links grid */}
      <div className="container mx-auto px-4 py-10">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-8">
          {/* Brand */}
          <div className="col-span-2 md:col-span-1">
            <span className="bz-heading text-xl text-[var(--bz-amber)]">
              <InlineEditable
                sectionId={sectionId}
                settingKey="brand_name"
                value={brandName}
              />
            </span>
            <p className="text-sm opacity-50 mt-3 leading-relaxed">
              <InlineEditable
                sectionId={sectionId}
                settingKey="footer_text"
                value={footerText}
                multiline
              />
            </p>
            {socials.length > 0 && (
              <div className="flex gap-3 mt-4" aria-label="Social media">
                {socials.map(({ name, url }) => {
                  const Icon = SOCIAL_ICONS[name.toLowerCase()] ?? null;
                  return (
                    <a
                      key={name}
                      href={formatSocialHref(name, url)}
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label={name}
                      title={name}
                      className="w-9 h-9 rounded-full bg-[var(--bz-amber)] text-[var(--bz-dark)] flex items-center justify-center hover:opacity-80 transition-opacity"
                    >
                      {Icon ? <Icon size={16} /> : <Send size={16} aria-hidden="true" />}
                    </a>
                  );
                })}
              </div>
            )}
          </div>

          {/* Columns */}
          {columns.map((col) => (
            <div key={col.title}>
              <h4 className="bz-label text-[var(--bz-amber)] mb-4">
                {col.title}
              </h4>
              <ul className="space-y-2.5">
                {col.links.map((l) => (
                  <li key={`${col.title}-${l.label}`}>
                    <Link
                      to={l.href}
                      className="text-sm opacity-60 hover:opacity-100 hover:text-[var(--bz-amber)] transition-all"
                    >
                      {l.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>

      {/* Bottom bar */}
      <div className="border-t border-white/10">
        <div className="container mx-auto px-4 py-4 flex flex-col md:flex-row items-center justify-center gap-1 md:gap-3 text-center text-xs opacity-30">
          <p>
            © {new Date().getFullYear()} {brandName}.{" "}
            {localized(locale, "All rights reserved.", "كل الحقوق محفوظة.")}
          </p>
          <span className="hidden md:inline">·</span>
          <p>{localized(locale, "Powered by NUMU", "مدعوم بواسطة NUMU")}</p>
        </div>
      </div>
    </footer>
  );
}
