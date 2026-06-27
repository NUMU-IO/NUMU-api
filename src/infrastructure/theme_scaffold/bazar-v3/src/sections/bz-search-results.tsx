"use client";

import { useMemo, useState } from "react";
import {
  Link,
  useLocale,
  useProducts,
  useResolvedSettings,
  type Product,
} from "@numueg/theme-sdk";
import { Search } from "lucide-react";
import { asNumber, asString, localized, usePageData, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";
import { BzProductCard } from "./bz-product-grid";

/**
 * bz-search-results — body for the `search` template. Seeds the query
 * from `usePageData().data.query` (the storefront /search route stashes it),
 * but also renders a live search box so visitors can refine in place.
 * Results are pre-fetched (`page.data.results`) when available, else a
 * client-side filter over `useProducts()` so the customizer preview
 * still shows something. Cards reuse the shared BzProductCard.
 *
 * Settings: placeholder_when_no_query, no_results_text,
 * search_placeholder, columns_desktop.
 */
export default function BzSearchResults({
  instance,
  sectionId,
}: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const pageData = usePageData();
  const { products } = useProducts();
  const locale = useLocale();

  const placeholderEmpty =
    asString(s.placeholder_when_no_query) || localized(locale, "SEARCH THE BAZAR", "ابحث في بازار");
  const noResultsText =
    asString(s.no_results_text) ||
    localized(locale, "No matches. Try a different keyword or browse the full edit.", "مفيش نتايج. جرّب كلمة تانية أو تصفّح كل التشكيلة.");
  const searchPlaceholder =
    asString(s.search_placeholder) || localized(locale, "Search products…", "ابحث عن المنتجات…");
  const cols = Math.max(1, Math.min(5, asNumber(s.columns_desktop, 4)));

  // The storefront /search route stashes the query in `page.data.query`
  // (with `q` as a defensive alias); usePageData() carries the raw host page
  // ctx (the SDK's usePage() is a synthesised record without it).
  const pd = pageData?.data as Record<string, unknown> | undefined;
  const initialQuery = asString(pd?.query) || asString(pd?.q);
  const preFetched = (pd as { results?: Product[] } | undefined)?.results;

  const [query, setQuery] = useState(initialQuery);

  const matches: Product[] = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) {
      // No active query → show something useful instead of a blank page:
      // the route's pre-fetched slice if present, otherwise the full catalog
      // so search defaults to "browse everything".
      if (preFetched && preFetched.length > 0) return preFetched;
      return products;
    }
    return products.filter(
      (p) =>
        p.name?.toLowerCase().includes(needle) ||
        p.description?.toLowerCase().includes(needle),
    );
  }, [query, preFetched, products]);

  const hasQuery = Boolean(query.trim());
  const hasMatches = matches.length > 0;

  const gridStyle = {
    "--bz-cols-mobile": 2,
    "--bz-cols-desktop": cols,
  } as React.CSSProperties;

  const gridClass =
    "grid gap-3 sm:gap-4 md:gap-6 grid-cols-[repeat(var(--bz-cols-mobile),minmax(0,1fr))] md:grid-cols-[repeat(var(--bz-cols-desktop),minmax(0,1fr))]";

  return (
    <section
      className="min-h-[70vh] bg-[var(--bz-cream)]"
      data-bz-section={sectionId}
    >
      {/* Hero strip with the search box */}
      <div className="bg-[var(--bz-dark)] py-10 sm:py-14">
        <div className="container mx-auto px-4 text-center">
          <h1 className="bz-heading text-2xl sm:text-4xl md:text-5xl text-[var(--bz-amber)]">
            {hasQuery ? (
              <>{localized(locale, "RESULTS FOR", "نتايج البحث عن")} &ldquo;{query}&rdquo;</>
            ) : (
              <InlineEditable
                sectionId={sectionId}
                settingKey="placeholder_when_no_query"
                value={placeholderEmpty}
              />
            )}
          </h1>
          <form
            className="relative max-w-md mx-auto mt-6"
            onSubmit={(e) => e.preventDefault()}
            role="search"
          >
            <label htmlFor="bz-search-input" className="sr-only">
              {searchPlaceholder}
            </label>
            <Search
              size={18}
              aria-hidden="true"
              className="absolute start-4 top-1/2 -translate-y-1/2 text-[var(--bz-dark)]/40"
            />
            <input
              id="bz-search-input"
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={searchPlaceholder}
              className="w-full h-12 ps-11 pe-4 rounded-full bg-white border-2 border-[var(--bz-amber)] text-sm text-[var(--bz-dark)] placeholder:text-[var(--bz-gray)] focus:outline-none focus:ring-2 focus:ring-[var(--bz-amber)]"
            />
          </form>
        </div>
      </div>

      <div className="container mx-auto px-4 py-8 md:py-12">
        {hasQuery && !hasMatches ? (
          <div className="text-center py-16">
            <Search size={48} className="mx-auto text-[var(--bz-amber)] mb-4" aria-hidden="true" />
            <p className="bz-heading text-xl text-[var(--bz-dark)]">
              <InlineEditable
                sectionId={sectionId}
                settingKey="no_results_text"
                value={noResultsText}
                multiline
              />
            </p>
            <Link
              to="/products"
              className="bz-btn bz-btn-filled rounded-full text-[11px] mt-6 inline-flex"
            >
              {localized(locale, "BROWSE ALL", "تصفّح الكل")}
            </Link>
          </div>
        ) : hasMatches ? (
          <div className={gridClass} style={gridStyle}>
            {matches.map((p) => (
              <BzProductCard key={p.id} product={p} />
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}
