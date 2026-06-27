import {
  useMemo,
  useState } from "react";
import {
  usePage,
  useProducts,
  type Product,
} from "@numueg/theme-sdk";
import { EditableText } from "../lib/EditableText";
import type { EmpSectionProps } from "../lib/section";
import { ProductCard } from "../lib/ProductCard";

interface GridSettings {
  title?: string;
  columns_desktop?: number;
  show_search?: boolean;
  show_sort?: boolean;
  show_categories?: boolean;
}

type SortKey = "featured" | "price_asc" | "price_desc" | "name";

/**
 * Products listing — page title, optional search box, category chip bar
 * (derived from the catalog), a count + sort toolbar and a responsive grid.
 * All filtering/sorting is client-side over the SSR-forwarded catalog so the
 * page stays fast and works without extra round-trips.
 */
export default function ProductGrid({ id, settings }: EmpSectionProps) {
  const s = settings as GridSettings;
  const cols = Math.max(2, Math.min(5, s.columns_desktop ?? 4));
  const title = s.title ?? "كل المنتجات";

  const page = usePage();
  const ssr = (page?.data?.products as Product[] | undefined) ?? [];
  const fallback = useProducts({ fetchIfMissing: ssr.length === 0 });
  const products = ssr.length > 0 ? ssr : fallback.products;

  const [query, setQuery] = useState("");
  const [cat, setCat] = useState<string>("all");
  const [sort, setSort] = useState<SortKey>("featured");

  const categories = useMemo(() => {
    const set = new Set<string>();
    for (const p of products) if (p.category) set.add(p.category);
    return Array.from(set);
  }, [products]);

  const visible = useMemo(() => {
    let list = products;
    if (cat !== "all") list = list.filter((p) => p.category === cat);
    if (query.trim()) {
      const q = query.trim().toLowerCase();
      list = list.filter((p) => p.name.toLowerCase().includes(q));
    }
    const sorted = [...list];
    switch (sort) {
      case "price_asc":
        sorted.sort((a, b) => a.price - b.price);
        break;
      case "price_desc":
        sorted.sort((a, b) => b.price - a.price);
        break;
      case "name":
        sorted.sort((a, b) => a.name.localeCompare(b.name, "ar"));
        break;
    }
    return sorted;
  }, [products, cat, query, sort]);

  return (
    <section className="nt-container" style={{ paddingBlock: "2.5rem" }}>
      <EditableText
        as="h1"
        className="nt-display-sm"
        sectionId={id}
        settingId="title"
        value={title}
        style={{ marginBottom: "1.5rem" }}
      />

      {s.show_search !== false ? (
        <input
          className="nt-input"
          type="search"
          value={query}
          placeholder="ابحث عن منتج..."
          onChange={(e) => setQuery(e.target.value)}
          style={{ marginBottom: "1.25rem" }}
        />
      ) : null}

      {s.show_categories !== false && categories.length > 0 ? (
        <div className="nt-catbar">
          <button
            type="button"
            className="nt-chip"
            aria-pressed={cat === "all"}
            onClick={() => setCat("all")}
          >
            الكل
          </button>
          {categories.map((c) => (
            <button
              key={c}
              type="button"
              className="nt-chip"
              aria-pressed={cat === c}
              onClick={() => setCat(c)}
            >
              {c}
            </button>
          ))}
        </div>
      ) : null}

      <div className="nt-toolbar">
        <span className="nt-label">{visible.length} منتج</span>
        {s.show_sort !== false ? (
          <select
            className="nt-select"
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
            aria-label="ترتيب"
          >
            <option value="featured">مميز</option>
            <option value="price_asc">السعر: من الأقل</option>
            <option value="price_desc">السعر: من الأعلى</option>
            <option value="name">الاسم</option>
          </select>
        ) : null}
      </div>

      {visible.length === 0 ? (
        <p className="nt-placeholder" style={{ paddingBlock: "3rem" }}>
          لا توجد منتجات مطابقة.
        </p>
      ) : (
        <div className="nt-grid" style={{ ["--cols" as string]: cols }}>
          {visible.map((p) => (
            <ProductCard key={p.id} product={p} />
          ))}
        </div>
      )}
    </section>
  );
}
