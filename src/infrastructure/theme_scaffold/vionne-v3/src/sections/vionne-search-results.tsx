"use client";

import { useMemo, useState } from "react";
import {
  Link,
  Money,
  useLocale,
  useProducts,
  useResolvedSettings,
  type Product,
} from "@numueg/theme-sdk";
import { Search, X } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { asNumber, asString, localized, usePageData, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * vionne-search-results — body for the `search` template. Seeds its query from
 * the storefront /search route (`usePageData().data.query`) and renders a live,
 * underlined search field in the Vionne voice plus an animated results grid.
 * Filters the SSR-prefetched catalog (`useProducts()`) by name/description/tag.
 * Without it, vionne's /search rendered blank (the host ships no search
 * backstop). Cards mirror the Vionne product card.
 */
export default function VionneSearchResults({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();
  const { products } = useProducts();
  const pageData = usePageData();

  const colsDesktop = asNumber(s.columns_desktop, 4);
  const colsMobile = asNumber(s.columns_mobile, 2);
  const noResultsText =
    asString(s.no_results_text) ||
    localized(
      locale,
      "No matches. Try a different keyword or browse the full collection.",
      "لا توجد نتائج. جرّبي كلمة أخرى أو تصفّحي كامل التشكيلة.",
    );

  const initialQuery =
    asString(pageData?.data?.query) || asString(pageData?.data?.q);
  const [search, setSearch] = useState(initialQuery);

  const matches = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return [] as Product[];
    return products.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        (p.description ?? "").toLowerCase().includes(q) ||
        (p.tags ?? []).some((t) => t.toLowerCase().includes(q)),
    );
  }, [products, search]);

  const hasQuery = Boolean(search.trim());

  return (
    <div className="bg-background min-h-[70vh]" data-testid="storefront-search-page">
      <div className="container mx-auto px-4 py-10 md:py-16">
        <h1 className="vn-heading text-2xl md:text-4xl text-[var(--vn-ink)] mb-8 text-center">
          {hasQuery ? (
            <>
              {localized(locale, "Results for", "نتائج البحث عن")}{" "}
              <span className="text-[var(--vn-muted)]">“{search}”</span>
            </>
          ) : (
            localized(locale, "Search", "البحث")
          )}
        </h1>

        {/* Underlined search field — Vionne signature */}
        <div className="relative mb-10 max-w-md mx-auto">
          <Search
            size={16}
            className="absolute start-0 top-1/2 -translate-y-1/2 text-[var(--vn-muted)]"
          />
          <input
            type="search"
            placeholder={localized(locale, "Search products", "ابحثي عن منتج")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full h-10 ps-7 pe-7 text-sm bg-transparent border-b border-[var(--vn-border)] focus:border-[var(--vn-ink)] focus:outline-none transition-colors placeholder:text-[var(--vn-muted)]"
            data-testid="storefront-search-input"
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch("")}
              aria-label="Clear search"
              className="absolute end-0 top-1/2 -translate-y-1/2 text-[var(--vn-muted)] hover:text-[var(--vn-ink)] transition-colors"
            >
              <X size={14} />
            </button>
          )}
        </div>

        {hasQuery && matches.length === 0 ? (
          <div className="text-center py-16">
            <div className="w-12 h-px bg-[var(--vn-border)] mx-auto mb-6" />
            <p className="text-sm text-[var(--vn-muted)] max-w-sm mx-auto">
              <InlineEditable sectionId={sectionId} settingKey="no_results_text" value={noResultsText} multiline />
            </p>
            <Link
              to="/products"
              className="vn-label inline-block mt-6 text-[var(--vn-ink)] border-b border-[var(--vn-ink)] pb-0.5 text-xs"
            >
              {localized(locale, "Browse all", "تصفّحي الكل")}
            </Link>
          </div>
        ) : matches.length > 0 ? (
          <motion.div
            layout
            className="grid gap-4 md:gap-5"
            style={
              {
                gridTemplateColumns: `repeat(${colsDesktop},minmax(0,1fr))`,
                ["--cols-mobile" as string]: `repeat(${colsMobile},minmax(0,1fr))`,
              } as React.CSSProperties
            }
            data-testid="storefront-search-grid"
          >
            <AnimatePresence>
              {matches.map((product) => (
                <motion.div
                  key={product.id}
                  layout
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                >
                  <SearchCard product={product} />
                </motion.div>
              ))}
            </AnimatePresence>
          </motion.div>
        ) : null}
      </div>
    </div>
  );
}

/** Compact Vionne product card — mirrors vn-product-card classes. */
function SearchCard({ product }: { product: Product }) {
  const price = product.variants?.[0]?.price ?? product.price ?? 0;
  const primary = product.images?.[0]?.url;
  return (
    <Link
      to={`/product/${product.slug || product.id}`}
      className="vn-product-card group block"
      data-testid="storefront-product-card"
    >
      <div className="relative overflow-hidden bg-[var(--vn-band)] aspect-[3/4]">
        {primary ? (
          <img
            src={primary}
            alt={product.name}
            className="vn-product-image absolute inset-0 w-full h-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="absolute inset-0 vn-shimmer" />
        )}
      </div>
      <div className="mt-3 px-1">
        <h3 className="text-sm font-medium text-[var(--vn-ink)] line-clamp-1">
          {product.name}
        </h3>
        <span className="text-sm font-semibold text-[var(--vn-ink)] mt-1 block">
          <Money amount={price} currency={product.currency} />
        </span>
      </div>
    </Link>
  );
}
