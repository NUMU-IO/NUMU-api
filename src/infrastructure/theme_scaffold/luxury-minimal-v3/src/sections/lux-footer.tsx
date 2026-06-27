"use client";

import {
  Link,
  useCollections,
  useLocale,
  useResolvedSettings,
  useShop,
  useThemeSettings,
} from "@numueg/theme-sdk";
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

/**
 * lux-footer — faithful V3 port of V2 LuxStoreFooter
 * (numu-egyptian-bazaar/src/components/store/luxury-minimal/LuxStoreFooter.tsx).
 *
 * V2 configures the shared BaseStoreFooter with `showTrustBar:false`,
 * `showNewsletter:false`, `forceLayout:"4-col"`, `hideContactBlock:true`, so
 * the rendered chrome is a MINIMAL dark footer (`lux-footer py-14`):
 *   - a 4-col grid → brand block (`lux-heading text-white text-base` store
 *     name + editable `footer_text` at opacity-50), a Shop column, a Help
 *     column (heading `text-[10px] font-medium uppercase tracking-[0.2em]
 *     text-white/70`, links `text-xs opacity-50 hover:opacity-80`), and one
 *     empty cell where the (hidden) contact block sat.
 *   - a centered bottom bar with the copyright + "Powered by NUMU"
 *     (`text-[10px] uppercase tracking-[0.2em] opacity-30`).
 *   - NO newsletter, NO trust bar.
 *
 * The Shop / Help columns can be overridden via `column` blocks (same seam as
 * the Empire / Gilded footers). Settings: brand_name, logo_url, footer_text.
 */
export default function LuxFooter({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const shop = useShop();
  const { collections } = useCollections();
  const themeSettings = useThemeSettings();
  const locale = useLocale();

  const brandName =
    asString(s.brand_name) ||
    asString(themeSettings.global_settings?.brand_name) ||
    shop?.name ||
    "NUMU";

  const logoUrl =
    asImageUrl(s.logo_url) ||
    asImageUrl(themeSettings.global_settings?.logo_url) ||
    "";

  const footerText =
    asString(s.footer_text) ||
    localized(
      locale,
      "A curated edit of clothing and accessories — contemporary design, exceptional quality.",
      "تشكيلة مميزة من الملابس والإكسسوارات بتصميم عصري وجودة عالية.",
    );

  // Optional override columns from `column` blocks (Empire / Gilded seam).
  const overrideColumns: FooterColumn[] = readBlocks(instance, "column")
    .map((r) => {
      const links: FooterLink[] = [];
      for (let i = 1; i <= 5; i++) {
        const label = asString(r[`link${i}_label`]);
        const href = asString(r[`link${i}_href`]);
        if (label && href) links.push({ label, href });
      }
      return { title: asString(r.title), links };
    })
    .filter((c) => c.title && c.links.length > 0);

  // V2 default Shop / Help columns (the V2 footer hard-codes the Arabic
  // category links; we keep the Shop column live off the store's collections).
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
      title: localized(locale, "Help", "المساعدة"),
      links: [
        { label: localized(locale, "Shipping", "الشحن"), href: "/pages/shipping" },
        { label: localized(locale, "Returns", "الإرجاع"), href: "/pages/returns" },
        { label: localized(locale, "Contact us", "تواصل معنا"), href: "/pages/contact" },
        { label: localized(locale, "Track Order", "تتبع الطلب"), href: "/track" },
      ],
    },
  ];

  const columnsToRender = overrideColumns.length > 0 ? overrideColumns : defaultColumns;

  return (
    <footer className="lux-footer py-14 mt-10" data-lux-section={sectionId}>
      <div className="container mx-auto px-4">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-10">
          {/* Brand block */}
          <div>
            {logoUrl ? (
              <img src={logoUrl} alt={brandName} className="h-7 object-contain mb-4" />
            ) : (
              <h3 className="lux-heading text-white text-base mb-4">
                <InlineEditable sectionId={sectionId} settingKey="brand_name" value={brandName} />
              </h3>
            )}
            <p className="text-xs leading-relaxed opacity-50">
              <InlineEditable
                sectionId={sectionId}
                settingKey="footer_text"
                value={footerText}
                multiline
              />
            </p>
          </div>

          {/* Shop / Help (or override) columns */}
          {columnsToRender.map((col, ci) => (
            <div key={`${col.title}-${ci}`}>
              <h3 className="text-[10px] font-medium uppercase tracking-[0.2em] text-white/70 mb-4">
                {col.title}
              </h3>
              <div className="space-y-2.5">
                {col.links.map((link, idx) => (
                  <Link
                    key={`${col.title}-${idx}`}
                    to={link.href}
                    className="block text-xs opacity-50 hover:opacity-80 transition-opacity"
                  >
                    {link.label}
                  </Link>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Bottom bar */}
        <div className="mt-10 mb-6 flex flex-col items-center gap-2">
          <span className="w-full text-center text-[10px] uppercase tracking-[0.2em] opacity-30">
            &copy; {new Date().getFullYear()} {brandName}
          </span>
          <span className="text-[10px] uppercase tracking-[0.2em] opacity-30">
            {localized(locale, "Powered by NUMU", "مدعوم بواسطة NUMU")}
          </span>
        </div>
      </div>
    </footer>
  );
}
