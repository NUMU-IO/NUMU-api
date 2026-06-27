import {
  useState } from "react";
import {
  usePage,
  useProducts,
  type Product,
} from "@numueg/theme-sdk";
import { EditableText } from "../lib/EditableText";
import type { EmpSectionProps } from "../lib/section";
import { useT } from "../lib/i18n";
import { ProductCard } from "../lib/ProductCard";

interface FeaturedSettings {
  title?: string;
  view_all_link?: string;
  view_all_label?: string;
  max_items?: number;
  tab_trending_label?: string;
  tab_bestseller_label?: string;
  tab_new_label?: string;
}

const TABS: Array<{
  key: string;
  settingId: keyof FeaturedSettings;
  en: string;
  ar: string;
  tags: string[];
}> = [
  { key: "trending", settingId: "tab_trending_label", en: "Trending", ar: "الأكثر رواجاً", tags: ["رائج", "trending", "مميز", "featured"] },
  { key: "bestseller", settingId: "tab_bestseller_label", en: "Bestsellers", ar: "الأكثر مبيعاً", tags: ["مبيع", "bestseller", "best", "الأكثر مبيعاً"] },
  { key: "new", settingId: "tab_new_label", en: "New", ar: "وصل حديثاً", tags: ["جديد", "new", "حديث"] },
];

/** Filter products for a tab by their `tags` (case-insensitive substring),
 *  falling back to the full list so a tab never renders empty. */
function filterByTab(products: Product[], key: string): Product[] {
  const tab = TABS.find((t) => t.key === key);
  if (!tab || key === "trending") return products;
  const matched = products.filter((p) =>
    (p.tags ?? []).some((t) =>
      tab.tags.some((needle) => t.toLowerCase().includes(needle.toLowerCase())),
    ),
  );
  return matched.length > 0 ? matched : products;
}

/**
 * Featured rail — large uppercase title with pill filter tabs on one row, then
 * a horizontally-scrollable snap rail of product cards (Empire's signature
 * layout). Reads the SSR-forwarded catalog from `page.data.products`, falling
 * back to a client fetch when the route didn't pre-fetch.
 */
export default function FeaturedCollection({ id, settings }: EmpSectionProps) {
  const s = settings as FeaturedSettings;
  const tr = useT();
  const title = s.title ?? tr("Shop", "المتجر");
  const viewAll = s.view_all_link || "/products";
  const max = Math.max(4, Math.min(12, s.max_items ?? 8));

  const page = usePage();
  const ssrProducts = (page?.data?.products as Product[] | undefined) ?? [];
  const fallback = useProducts({ fetchIfMissing: ssrProducts.length === 0 });
  const products = ssrProducts.length > 0 ? ssrProducts : fallback.products;

  const [tab, setTab] = useState("trending");

  const filtered = filterByTab(products, tab).slice(0, max);

  if (products.length === 0) return null;

  return (
    <section className="nt-section nt-bg-white">
      <div className="nt-container">
        <div className="nt-shop__head">
          <EditableText
            as="h2"
            className="nt-display-sm"
            sectionId={id}
            settingId="title"
            value={title}
          />
          <div className="nt-tabs">
            {TABS.map((item) => (
              <button
                key={item.key}
                type="button"
                className="nt-chip"
                aria-pressed={tab === item.key}
                onClick={() => setTab(item.key)}
              >
                {(s[item.settingId] as string) || tr(item.en, item.ar)}
              </button>
            ))}
            <a className="nt-chip" href={viewAll}>
              {s.view_all_label || tr("View all", "عرض الكل")}
            </a>
          </div>
        </div>

        <div className="nt-rail">
          {filtered.map((p) => (
            <ProductCard key={p.id} product={p} />
          ))}
        </div>
      </div>
    </section>
  );
}
