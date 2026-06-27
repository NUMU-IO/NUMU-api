"use client";

import { useState } from "react";
import { Link, useLocale, useNavigation, useResolvedSettings, useShop, useThemeSettings } from "@numueg/theme-sdk";
import {
  Facebook,
  Instagram,
  MapPin,
  Phone,
  Send,
  Twitter,
} from "lucide-react";
import {
  asBool,
  asString,
  localized,
  resolveBlockNodes,
  useBlockResolveContext,
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

const SOCIAL_ICONS: Record<string, typeof Facebook> = {
  facebook: Facebook,
  instagram: Instagram,
  twitter: Twitter,
};

export default function ByFooter({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const blkCtx = useBlockResolveContext();
  const shop = useShop();
  const themeSettings = useThemeSettings();
  const locale = useLocale();

  // brand_name keeps a real-store fallback (shop name / global setting) — that's
  // a live value, not baked marketing copy. Every other footer string is empty
  // when unset, so an unconfigured field renders nothing instead of demo text.
  const brandName =
    asString(s.brand_name) ||
    asString(themeSettings.global_settings?.brand_name) ||
    shop?.name ||
    "";

  const tagline = asString(s.tagline);
  const address = asString(s.address);
  const phone = asString(s.phone);
  const showNewsletter = asBool(s.show_newsletter, true);
  const newsletterTitle = asString(s.newsletter_title);
  const newsletterCopy = asString(s.newsletter_copy);
  const newsletterButton = asString(s.newsletter_button_label) || localized(locale, "Subscribe", "اشترك");
  const newsletterButtonSuccess =
    asString(s.newsletter_button_success) || localized(locale, "Subscribed", "تم الاشتراك");
  const copyrightText = asString(s.copyright_text);
  const creditText = asString(s.credit_text);
  const creditLinkLabel = asString(s.credit_link_label);

  // Footer columns are `column` blocks; each column holds NESTED `link`
  // blocks (label + href) — blocks-in-blocks, so a column can carry any
  // number of links instead of the old fixed 4 pairs. Falls back to the
  // legacy `link1_label/link1_href …` settings so footers authored before
  // nested blocks still render unchanged. resolveBlockNodes resolves dynamic
  // -source bindings ({__numu_source:"store.name"}) so bound text isn't blank.
  const configuredColumns: FooterColumn[] = resolveBlockNodes(
    instance,
    "column",
    blkCtx,
  )
    .map((col) => {
      const nested: FooterLink[] = resolveBlockNodes(col, "link", blkCtx)
        .map((l) => ({
          label: asString(l.settings.label),
          href: asString(l.settings.href),
        }))
        .filter((l) => l.label && l.href);
      let links = nested;
      if (links.length === 0) {
        const legacy: FooterLink[] = [];
        for (let i = 1; i <= 4; i++) {
          const label = asString(col.settings[`link${i}_label`]);
          const href = asString(col.settings[`link${i}_href`]);
          if (label && href) legacy.push({ label, href });
        }
        links = legacy;
      }
      return { title: asString(col.settings.title), links };
    })
    // Show a column when it has a title OR at least one complete link — don't
    // silently drop a titled column whose links are still being filled in.
    .filter((c) => c.title || c.links.length > 0);

  // Phase 2.4 — a merchant-managed footer menu (chosen via the
  // `footer_menu_handle` link_list setting) takes precedence over the
  // legacy `column` blocks. Top-level items WITH children become columns
  // (heading + child links); childless top-level items collapse into one
  // leading, heading-less link column — so a flat footer menu (e.g. the
  // seeded Shipping/Returns/FAQ/Track) renders as a single tidy list.
  // No "footer" default here: an empty handle yields no menu, so an
  // unconfigured footer renders no link columns rather than a phantom menu.
  const footerMenuHandle = asString(s.footer_menu_handle);
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
    return cols;
  })();

  // No baked-in links: a menu (if picked) wins, else the configured `column`
  // blocks, else nothing. An unconfigured footer shows zero link columns
  // instead of hardcoded /collections/beans etc. that would 404.
  const columns = menuColumns.length > 0 ? menuColumns : configuredColumns;

  const socialLinks =
    (shop?.social_links as Record<string, string> | undefined) ?? {};
  const socials: Array<{ name: string; url: string }> = Object.entries(
    socialLinks,
  )
    .map(([name, url]) => ({ name, url: asString(url) }))
    .filter(({ url }) => Boolean(url));

  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const handleSubscribe = (e: React.FormEvent) => {
    e.preventDefault();
    if (!email) return;
    setSubmitted(true);
    setEmail("");
  };

  return (
    <footer className="by-footer" data-by-section={sectionId}>
      <div className="by-shell">
        <div className="by-footer-grid">
          <div className="by-footer-brand">
            <h3>
              <InlineEditable
                sectionId={sectionId}
                settingKey="brand_name"
                value={brandName}
              />
            </h3>
            {tagline && (
              <p>
                <InlineEditable
                  sectionId={sectionId}
                  settingKey="tagline"
                  value={tagline}
                  multiline
                />
              </p>
            )}
            {(address || phone) && (
              <div style={{ display: "flex", flexDirection: "column", gap: "0.4rem", marginTop: "0.5rem" }}>
                {address && (
                  <span style={{ display: "inline-flex", alignItems: "center", gap: "0.5rem", color: "rgba(247,241,232,0.7)", fontSize: "0.85rem" }}>
                    <MapPin size={14} aria-hidden="true" /> {address}
                  </span>
                )}
                {phone && (
                  <a
                    href={`tel:${phone.replace(/\s+/g, "")}`}
                    style={{ display: "inline-flex", alignItems: "center", gap: "0.5rem", color: "rgba(247,241,232,0.7)", fontSize: "0.85rem" }}
                  >
                    <Phone size={14} aria-hidden="true" /> {phone}
                  </a>
                )}
              </div>
            )}
            {socials.length > 0 && (
              <div className="by-footer-socials" aria-label="Social media">
                {socials.map(({ name, url }) => {
                  const Icon = SOCIAL_ICONS[name.toLowerCase()] ?? Send;
                  return (
                    <a
                      key={name}
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="by-footer-social"
                      aria-label={name}
                    >
                      <Icon size={16} />
                    </a>
                  );
                })}
              </div>
            )}
          </div>

          {columns.map((col, ci) => (
            <div key={`${col.title}-${ci}`}>
              {col.title && <h4>{col.title}</h4>}
              <div className="by-footer-links">
                {col.links.map((l, li) => (
                  <Link key={`${ci}-${li}-${l.label}`} to={l.href}>
                    {l.label}
                  </Link>
                ))}
              </div>
            </div>
          ))}

          {showNewsletter && newsletterTitle && (
            <div className="by-footer-newsletter">
              <h4>
                <InlineEditable
                  sectionId={sectionId}
                  settingKey="newsletter_title"
                  value={newsletterTitle}
                />
              </h4>
              {newsletterCopy && (
                <p
                  style={{
                    fontSize: "0.85rem",
                    color: "rgba(247,241,232,0.7)",
                    margin: "0 0 0.75rem",
                    lineHeight: 1.55,
                  }}
                >
                  <InlineEditable
                    sectionId={sectionId}
                    settingKey="newsletter_copy"
                    value={newsletterCopy}
                    multiline
                  />
                </p>
              )}
              {/* No subscribe endpoint exists yet — keep the optimistic
                  "subscribed" UI; the email is not persisted server-side. */}
              <form onSubmit={handleSubscribe} noValidate>
                <input
                  type="email"
                  required
                  placeholder="you@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  aria-label="Email address"
                />
                <button type="submit">
                  {submitted ? newsletterButtonSuccess : newsletterButton}
                </button>
              </form>
            </div>
          )}
        </div>

        <div className="by-footer-base">
          <span>
            © {new Date().getFullYear()}
            {brandName ? ` ${brandName}` : ""}
            {copyrightText ? `. ${copyrightText}` : ""}
          </span>
          {creditText && (
            <span>
              {creditText}
              {creditLinkLabel && (
                <>
                  {" "}
                  <a
                    href="/"
                    style={{
                      color: "var(--by-caramel-light, #d9a877)",
                      textDecoration: "underline",
                    }}
                  >
                    {creditLinkLabel}
                  </a>
                </>
              )}
              .
            </span>
          )}
        </div>
      </div>
    </footer>
  );
}
