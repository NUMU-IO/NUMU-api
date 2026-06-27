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

interface SearchSettings {
  title?: string;
  search_placeholder?: string;
  no_results_text?: string;
  columns_desktop?: number;
}

/**
 * Search results — body for the `search` template. Seeds the query from
 * `page.data.query` (the storefront /search route stashes it) and lets the
 * visitor refine in a live box. Uses the host's pre-fetched `page.data.results`
 * when present, else filters `useProducts()` client-side (customizer preview).
 */
export default function SearchResults({ id, settings }: EmpSectionProps) {
  const s = settings as SearchSettings;
  const cols = Math.max(2, Math.min(5, s.columns_desktop ?? 4));
  const placeholder = s.search_placeholder ?? "ابحث عن المنتجات…";

  const page = usePage();
  const pd = page?.data as Record<string, unknown> | undefined;
  const initialQuery =
    (pd?.query as string | undefined) ?? (pd?.q as string | undefined) ?? "";
  const preFetched = (pd?.results as Product[] | undefined) ?? [];

  const { products } = useProducts({ fetchIfMissing: true });
  const [query, setQuery] = useState(initialQuery);

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return preFetched;
    const pool = preFetched.length > 0 ? preFetched : products;
    return pool.filter(
      (p) =>
        p.name?.toLowerCase().includes(needle) ||
        p.description?.toLowerCase().includes(needle),
    );
  }, [query, preFetched, products]);

  return (
    <section className="nt-container" style={{ paddingBlock: "2.5rem" }}>
      <EditableText
        as="h1"
        className="nt-display-sm"
        sectionId={id}
        settingId="title"
        value={s.title ?? "البحث"}
        style={{ marginBottom: "1.25rem" }}
      />

      <input
        className="nt-input"
        type="search"
        value={query}
        placeholder={placeholder}
        onChange={(e) => setQuery(e.target.value)}
        style={{ marginBottom: "1.5rem" }}
      />

      {query.trim() ? (
        <p className="nt-label" style={{ marginBottom: "1.5rem" }}>
          {matches.length} نتيجة لـ "{query.trim()}"
        </p>
      ) : null}

      {matches.length > 0 ? (
        <div className="nt-grid" style={{ ["--cols" as string]: cols }}>
          {matches.map((p) => (
            <ProductCard key={p.id} product={p} />
          ))}
        </div>
      ) : (
        <p className="nt-placeholder" style={{ paddingBlock: "3rem" }}>
          {query.trim()
            ? s.no_results_text ||
              "لا توجد نتائج. جرّب كلمة أخرى أو تصفّح كل التشكيلة."
            : "اكتب كلمة للبحث في المتجر."}
        </p>
      )}
    </section>
  );
}
