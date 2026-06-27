"use client";
import { useState } from "react";
import {
  Link,
  useCollections,
  useLocale,
  useNavigation,
  useResolvedSettings,
  useShop,
  useThemeSettings,
  useTranslation,
} from "@numueg/theme-sdk";
import { Facebook, Instagram, Mail, Music2, Phone, Twitter, Youtube } from "lucide-react";
import {
  asString,
  localized,
  readBlockNodes,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * vionne-footer — faithful V3 port of V2 VionneStoreFooter
 * (numu-egyptian-bazaar/src/components/store/vionne/VionneStoreFooter.tsx),
 * re-plumbed onto the V3 SDK seam.
 *
 * A DARK grey footer (`vn-footer` = --vn-surface-dark, white text) with a
 * 4-column grid:
 *   (1) brand   — store wordmark (`vn-heading tracking-[0.32em] uppercase`) +
 *                 editable blurb + social icons (W1 social globals → store);
 *   (2) Shop    — All products + collections;
 *   (3) Help    — Shipping / Returns / Privacy / Terms / FAQ / Contact / Track;
 *   (4) Newsletter — eyebrow title + subtitle + email input.
 * Bottom bar carries the copyright (template) + payment methods + "Powered by".
 *
 * Link columns come from (1) a merchant footer menu (`footer_menu_handle` →
 * useNavigation, §5 hide-page→nav: hidden-page links + empty columns drop),
 * else (2) nested `column`/`link` blocks, else (3) the V2 Shop / Help defaults.
 */

interface FooterLink {
  label: string;
  href: string;
}
interface FooterColumn {
  title: string;
  links: FooterLink[];
}

export default function VionneFooter({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();
  const shop = useShop();
  const { collections } = useCollections();
  const themeSettings = useThemeSettings();
  const { t } = useTranslation();
  const globals = (themeSettings.global_settings ?? {}) as Record<string, unknown>;

  const brandName =
    asString(s.brand_name) ||
    asString(globals.brand_name) ||
    shop?.name ||
    "VIONNE";

  const aboutText =
    asString(s.footer_about_text) ||
    asString(globals.footer_tagline) ||
    t(
      "footer.brand_blurb",
      localized(
        locale,
        "Modest, refined, made to be lived in.",
        "موضة محتشمة وراقية — مصمَّمة تتلبس كل يوم.",
      ),
    );

  const shopTitle =
    asString(s.shop_title) || t("footer.shop_title", localized(locale, "Shop", "تسوّقي"));
  const helpTitle =
    asString(s.help_title) || t("footer.help_title", localized(locale, "Help", "المساعدة"));

  const showNewsletter = (s.show_newsletter as boolean) !== false;
  const newsletterTitle =
    asString(s.newsletter_title) ||
    t("footer.newsletter_title", localized(locale, "Stay in the loop", "ابقي على اطّلاع"));
  const newsletterSubtitle =
    asString(s.newsletter_subtitle) ||
    t(
      "footer.newsletter_subtitle",
      localized(
        locale,
        "Be first to know about new arrivals and exclusive drops.",
        "كوني أول من يعرف بكل جديد وعروضنا الحصرية.",
      ),
    );

  const paymentMethods = (
    asString(s.payment_methods) || "Visa, Mastercard, Cash on Delivery, Fawry"
  )
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean);

  // (1) Merchant footer menu — §5 hide-page→nav. useNavigation drops links to
  // hidden/unpublished pages (target_visible:false); top-level items WITH
  // children become columns, childless items collapse into one leading list,
  // and columns left empty after filtering are pruned.
  const footerMenuHandle = asString(s.footer_menu_handle) || "footer";
  const { items: footerMenuItems } = useNavigation(footerMenuHandle);
  const menuColumns: FooterColumn[] = (() => {
    if (footerMenuItems.length === 0) return [];
    const cols: FooterColumn[] = [];
    const loose: FooterLink[] = [];
    for (const item of footerMenuItems) {
      const kids = item.children ?? [];
      if (kids.length > 0) {
        cols.push({
          title: item.title,
          links: kids.map((c) => ({ label: c.title, href: c.url || "/" })),
        });
      } else {
        loose.push({ label: item.title, href: item.url || "/" });
      }
    }
    if (loose.length > 0) cols.unshift({ title: "", links: loose });
    return cols.filter((c) => c.links.length > 0);
  })();

  // (2) Editor `column` blocks — each holds NESTED `link` blocks (label + href),
  // falling back to legacy link1..5 settings so footers authored before nested
  // blocks still render. Empty columns are dropped.
  const blockColumns: FooterColumn[] = readBlockNodes(instance, "column")
    .map((col) => {
      const nested: FooterLink[] = readBlockNodes(col, "link")
        .map((l) => ({ label: asString(l.settings.label), href: asString(l.settings.href) }))
        .filter((l) => l.label && l.href);
      let links = nested;
      if (links.length === 0) {
        const legacy: FooterLink[] = [];
        for (let i = 1; i <= 5; i++) {
          const label = asString(col.settings[`link${i}_label`]);
          const href = asString(col.settings[`link${i}_href`]);
          if (label && href) legacy.push({ label, href });
        }
        links = legacy;
      }
      return { title: asString(col.settings.title), links };
    })
    .filter((c) => c.title || c.links.length > 0);

  // (3) V2 default Shop / Help columns (preserve the out-of-box V2 footer).
  const shopColumn: FooterColumn = {
    title: shopTitle,
    links: [
      { label: t("nav.all_products", localized(locale, "All products", "كل المنتجات")), href: "/products" },
      ...collections.slice(0, 5).map((cat) => ({
        label: cat.name,
        href: `/collections/${cat.slug}`,
      })),
    ],
  };
  const helpColumn: FooterColumn = {
    title: helpTitle,
    links: [
      { label: t("footer.shipping", localized(locale, "Shipping", "الشحن")), href: "/pages/shipping" },
      { label: t("footer.returns", localized(locale, "Returns", "الإرجاع")), href: "/pages/returns" },
      { label: t("footer.privacy", localized(locale, "Privacy", "الخصوصية")), href: "/pages/privacy" },
      { label: t("footer.terms", localized(locale, "Terms", "الشروط")), href: "/pages/terms" },
      { label: t("footer.faq", localized(locale, "FAQ", "الأسئلة الشائعة")), href: "/pages/faq" },
      { label: t("footer.contact", localized(locale, "Contact", "تواصلي")), href: "/pages/contact" },
      { label: t("footer.track_order", localized(locale, "Track order", "تتبع الطلب")), href: "/track" },
    ],
  };

  const columnsToRender =
    menuColumns.length > 0
      ? menuColumns
      : blockColumns.length > 0
        ? blockColumns
        : [shopColumn, helpColumn];

  // Socials: the W1 social_* globals win; fall back to the live store's
  // social_links so a store configured before W1 still shows its icons.
  const storeSocial = (shop?.social_links as Record<string, string> | undefined) ?? {};
  const socialUrl = (k: string) => asString(globals[`social_${k}`]) || asString(storeSocial[k]);
  const instagram = socialUrl("instagram");
  const facebook = socialUrl("facebook");
  const youtube = socialUrl("youtube");
  const tiktok = socialUrl("tiktok");
  const xUrl = socialUrl("x");
  const whatsapp = socialUrl("whatsapp");
  const emailLink = asString(storeSocial.email);
  const hasSocial = Boolean(
    instagram || facebook || youtube || tiktok || xUrl || whatsapp || emailLink,
  );

  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!email) return;
    setSubmitted(true);
    setEmail("");
    setTimeout(() => setSubmitted(false), 2000);
  };

  const year = new Date().getFullYear();
  const rights = t(
    "footer.rights_reserved",
    localized(locale, "All rights reserved.", "جميع الحقوق محفوظة."),
  );
  // Copyright template: tokens {year} {store} {rights}. Default reproduces the
  // V2 line exactly when unset.
  const copyrightTemplate = asString(s.copyright_template);
  const copyright = copyrightTemplate
    ? copyrightTemplate
        .replace(/\{year\}/g, String(year))
        .replace(/\{store\}/g, brandName)
        .replace(/\{rights\}/g, rights)
    : `© ${year} ${brandName}. ${rights}`;

  return (
    <footer className="vn-footer" data-vionne-section={sectionId}>
      <div className="container mx-auto px-4 pt-14 pb-8">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-10 md:gap-8">
          {/* Brand column */}
          <div className="md:col-span-1">
            <Link to="/" className="vn-heading text-lg tracking-[0.32em] uppercase block mb-4">
              <InlineEditable sectionId={sectionId} settingKey="brand_name" value={brandName} />
            </Link>
            <p className="text-sm text-white/70 leading-relaxed mb-5 max-w-xs">
              <InlineEditable
                sectionId={sectionId}
                settingKey="footer_about_text"
                value={aboutText}
                multiline
              />
            </p>
            {hasSocial && (
              <div className="flex items-center gap-3">
                {instagram && (
                  <a href={instagram} target="_blank" rel="noopener noreferrer" aria-label="Instagram">
                    <Instagram size={18} />
                  </a>
                )}
                {facebook && (
                  <a href={facebook} target="_blank" rel="noopener noreferrer" aria-label="Facebook">
                    <Facebook size={18} />
                  </a>
                )}
                {tiktok && (
                  <a href={tiktok} target="_blank" rel="noopener noreferrer" aria-label="TikTok">
                    <Music2 size={18} />
                  </a>
                )}
                {xUrl && (
                  <a href={xUrl} target="_blank" rel="noopener noreferrer" aria-label="X">
                    <Twitter size={18} />
                  </a>
                )}
                {youtube && (
                  <a href={youtube} target="_blank" rel="noopener noreferrer" aria-label="YouTube">
                    <Youtube size={18} />
                  </a>
                )}
                {emailLink && (
                  <a href={`mailto:${emailLink}`} aria-label="Email">
                    <Mail size={18} />
                  </a>
                )}
                {whatsapp && (
                  <a
                    href={
                      whatsapp.startsWith("http")
                        ? whatsapp
                        : `https://wa.me/${whatsapp.replace(/\D/g, "")}`
                    }
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label="WhatsApp"
                  >
                    <Phone size={18} />
                  </a>
                )}
              </div>
            )}
          </div>

          {/* Shop / Help (or override) columns */}
          {columnsToRender.map((col, ci) => (
            <div key={`${col.title}-${ci}`}>
              {col.title && <h3 className="vn-eyebrow mb-4 text-white">{col.title}</h3>}
              <ul className="space-y-2 text-sm">
                {col.links.map((l, li) => (
                  <li key={`${ci}-${li}-${l.label}`}>
                    <Link to={l.href}>{l.label}</Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}

          {/* Newsletter column */}
          {showNewsletter && (
            <div>
              <h3 className="vn-eyebrow mb-4 text-white">
                <InlineEditable sectionId={sectionId} settingKey="newsletter_title" value={newsletterTitle} />
              </h3>
              <p className="text-sm text-white/70 mb-4 max-w-xs">
                <InlineEditable
                  sectionId={sectionId}
                  settingKey="newsletter_subtitle"
                  value={newsletterSubtitle}
                  multiline
                />
              </p>
              <form onSubmit={submit} className="flex gap-2" noValidate>
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder={t("footer.email_placeholder", localized(locale, "Email address", "البريد الإلكتروني"))}
                  aria-label="Email address"
                  className="flex-1 min-w-0 h-11 px-3 rounded-md bg-white/10 border border-white/20 text-sm text-white placeholder:text-white/55 focus:outline-none focus:ring-2 focus:ring-white/30"
                />
                <button
                  type="submit"
                  className="h-11 px-4 rounded-md bg-white text-[var(--vn-ink)] text-xs font-semibold uppercase tracking-[0.18em] hover:opacity-90 transition-opacity whitespace-nowrap"
                >
                  {t("footer.join", localized(locale, "Join", "اشتركي"))}
                </button>
              </form>
              {submitted && (
                <p className="text-xs text-white/80 mt-2">
                  {t("footer.subscribed", localized(locale, "Thanks for subscribing.", "شكرًا لاشتراكك."))}
                </p>
              )}
            </div>
          )}
        </div>

        <div className="mt-12 pt-6 border-t border-white/10 flex flex-col md:flex-row items-start md:items-center justify-between gap-3 text-xs text-white/55">
          <span>{copyright}</span>
          <div className="flex flex-wrap items-center gap-3">
            {paymentMethods.map((m) => (
              <span key={m}>{m}</span>
            ))}
            {paymentMethods.length > 0 && <span aria-hidden="true">·</span>}
            <span>{t("footer.powered_by", localized(locale, "Powered by NUMU", "مدعوم من NUMU"))}</span>
          </div>
        </div>
      </div>
    </footer>
  );
}
