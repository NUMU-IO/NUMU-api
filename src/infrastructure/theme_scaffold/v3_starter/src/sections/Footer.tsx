import {
  useEffect,
  useState } from "react";
import { useShop,
  useCollections,
} from "@numueg/theme-sdk";
import { EditableText } from "../lib/EditableText";
import type { EmpSectionProps } from "../lib/section";
import { useT } from "../lib/i18n";

interface FooterSettings {
  brand_name?: string;
  description?: string;
  ticker_text?: string;
  instagram?: string;
  facebook?: string;
  twitter?: string;
  copyright?: string;
}

export default function Footer({ id, settings }: EmpSectionProps) {
  const s = settings as FooterSettings;
  const t = useT();
  const shop = useShop();
  const { collections } = useCollections({ limit: 5 });
  // Prefer the real store name; the theme placeholder ("STORE") is treated as
  // unset so it never overrides the store name baked in at activation.
  const brand =
    s.brand_name && s.brand_name !== "STORE"
      ? s.brand_name
      : shop?.name || s.brand_name || "STORE";
  const ticker = s.ticker_text || "100% مستقل";

  // Year is computed client-side to keep the SSR render path deterministic.
  const [year, setYear] = useState<number | null>(null);
  useEffect(() => setYear(new Date().getFullYear()), []);

  const socials = [
    { name: "Instagram", url: s.instagram, icon: <IconInstagram /> },
    { name: "X", url: s.twitter, icon: <IconX /> },
    { name: "Facebook", url: s.facebook, icon: <IconFacebook /> },
  ].filter((x) => x.url);

  const tickerItems = Array.from({ length: 10 });

  return (
    <footer className="nt-footer">
      {/* Ticker */}
      <div className="nt-footer__ticker">
        <div className="nt-marquee__track">
          {tickerItems.concat(tickerItems).map((_, i) => (
            <span className="nt-marquee__item" key={i}>
              <span className="nt-marquee__text">{ticker}</span>
              <span className="nt-marquee__dot">●</span>
              <span className="nt-marquee__sub">{brand}</span>
              <span className="nt-marquee__dot">●</span>
            </span>
          ))}
        </div>
      </div>

      <div className="nt-container">
        <div className="nt-footer__grid">
          {/* Brand */}
          <div className="nt-footer__brand">
            <EditableText
              as="h3"
              sectionId={id}
              settingId="brand_name"
              value={brand}
            />
            <EditableText
              as="p"
              className="nt-footer__desc"
              sectionId={id}
              settingId="description"
              value={
                s.description ||
                "متجر مستقل يقدّم تشكيلة مختارة بعناية. تصميم نظيف، جودة تدوم."
              }
            />
            <div className="nt-footer__social">
              {socials.length > 0
                ? socials.map((so) => (
                    <a
                      key={so.name}
                      href={so.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label={so.name}
                    >
                      {so.icon}
                    </a>
                  ))
                : [<IconInstagram key="i" />, <IconX key="x" />, <IconFacebook key="f" />].map(
                    (ic, i) => (
                      <span key={i} style={{ opacity: 0.3 }}>
                        {ic}
                      </span>
                    ),
                  )}
            </div>
          </div>

          {/* Shop links */}
          <div>
            <p className="nt-footer__heading">{t("Shop", "المتجر")}</p>
            <div className="nt-footer__links">
              <a href="/products">{t("All products", "كل المنتجات")}</a>
              {collections.slice(0, 5).map((c) => (
                <a key={c.id} href={`/collections/${c.slug}`}>
                  {c.name}
                </a>
              ))}
            </div>
          </div>

          {/* Help links */}
          <div>
            <p className="nt-footer__heading">{t("Help", "المساعدة")}</p>
            <div className="nt-footer__links">
              <a href="/pages/contact">{t("Contact us", "تواصل معنا")}</a>
              <a href="/pages/shipping">{t("Shipping", "الشحن")}</a>
              <a href="/pages/returns">{t("Returns", "الإرجاع")}</a>
            </div>
          </div>
        </div>

        <div className="nt-footer__bottom">
          <p className="nt-footer__copy">
            {s.copyright || `© ${year ?? ""} ${brand}`.trim()}
          </p>
          <div className="nt-paybadges">
            <span className="nt-paybadge">VISA</span>
            <span className="nt-paybadge">Mastercard</span>
            <span className="nt-paybadge">mada</span>
            <span className="nt-paybadge">Apple Pay</span>
          </div>
        </div>
      </div>
    </footer>
  );
}

const IconInstagram = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <rect x="2" y="2" width="20" height="20" rx="5" />
    <circle cx="12" cy="12" r="5" />
    <circle cx="17.5" cy="6.5" r="1.5" fill="currentColor" stroke="none" />
  </svg>
);
const IconX = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
  </svg>
);
const IconFacebook = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z" />
  </svg>
);
