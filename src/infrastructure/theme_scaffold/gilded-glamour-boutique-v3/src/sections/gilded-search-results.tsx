"use client";

import { useMemo, useState } from "react";
import {
  Link,
  useLocale,
  useProducts,
  useResolvedSettings,
  type Product,
} from "@numueg/theme-sdk";
import { Search, SlidersHorizontal } from "lucide-react";
import { motion } from "framer-motion";
import {
  asBool,
  asNumber,
  asString,
  localized,
  usePageData,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";
import { GildedProductCard } from "./_product-card";

/**
 * gilded-search-results — body for the `search` template, a FAITHFUL Gilded
 * port. No dedicated V2 search component exists, so this reuses the V2
 * GildedProductsPage visual language verbatim
 * (numu-egyptian-bazaar/src/components/store/gilded-glamour-boutique/GildedProductsPage.tsx):
 *   - Optional black hero strip `bg-foreground py-12 md:py-20`, gold Montserrat
 *     title `text-4xl md:text-6xl font-bold tracking-[0.08em] uppercase`, plus a
 *     `text-card/50 tracking-[0.2em] uppercase` result-count subtitle.
 *   - Filters bar `flex flex-wrap items-center gap-3 mb-10` with the gilded
 *     search input (`gld-input h-10 ps-9` + Search icon, `start-3`) and the V2
 *     category pills (`bg-foreground text-gold` active / bordered inactive),
 *     built from the matched products' own categories.
 *   - Grid `grid-cols-2 md:grid-cols-3 lg:grid-cols-<columns_desktop> gap-2
 *     md:gap-6 -mx-2 md:mx-0` of the SHARED GildedProductCard, with the V2
 *     shimmer skeletons (`aspect-[3/4] bg-muted animate-pulse`) while loading.
 *   - Empty / no-results states use the V2 gilded treatment (SlidersHorizontal
 *     glyph + bold uppercase headline + muted hint + gold-outline CTA).
 *
 * Data-wiring (mirrors lux-search-results): the visitor's query is seeded from
 * the host page descriptor (`usePageData().data.query` / `.q`, stashed by the
 * storefront /search route) and a live in-place box lets visitors refine without
 * a round-trip. Matching runs over the catalog (`useProducts({ fetchIfMissing:
 * true })` — the /search route does not SSR-prefetch the full catalog) by name /
 * category / description / tag, so the customizer preview and the live store
 * both show results.
 *
 * Engine-wired: `useResolvedSettings` (global tokens + dynamic sources + draft
 * preview — NEVER raw instance.settings), `useLocale` for bilingual defaults,
 * `InlineEditable` on the editable empty-query heading. The brand gold reads
 * `--gilded-gold` so the merchant's Accent picker repaints it. Defaults are
 * byte-identical to V2.
 */
export default function GildedSearchResults({
  instance,
  sectionId,
}: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();
  const { products, loading } = useProducts({ fetchIfMissing: true });
  const pageData = usePageData();

  const emptyHeading =
    asString(s.empty_heading) || localized(locale, "SEARCH", "البحث");
  const searchPlaceholder =
    asString(s.search_placeholder) ||
    localized(locale, "Search products...", "ابحث عن المنتجات...");
  const noResultsText =
    asString(s.no_results_text) ||
    localized(
      locale,
      "No matches. Try a different keyword or browse the full collection.",
      "لا توجد نتائج. جرّب كلمة أخرى أو تصفّح كامل التشكيلة.",
    );
  const showHero = asBool(s.show_hero, true);
  const colsDesktop = Math.max(2, Math.min(5, asNumber(s.columns_desktop, 5)));
  const colsMobile = 2;

  const initialQuery =
    asString(pageData?.data?.query) || asString(pageData?.data?.q);
  const [query, setQuery] = useState(initialQuery);
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);

  // Match over the catalog by name / category / description / tag (V2 search +
  // category filter on GildedProductsPage).
  const matches: Product[] = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return [];
    return products.filter((p) => {
      const cat = (p.category ?? "").toLowerCase();
      return (
        p.name?.toLowerCase().includes(needle) ||
        cat.includes(needle) ||
        (p.description ?? "").toLowerCase().includes(needle) ||
        (p.tags ?? []).some((t) => t.toLowerCase().includes(needle))
      );
    });
  }, [query, products]);

  // Category pills drawn from the matched products (V2 PLP pill treatment).
  const categories = useMemo(() => {
    const seen = new Set<string>();
    for (const p of matches) {
      if (p.category) seen.add(p.category);
    }
    return Array.from(seen);
  }, [matches]);

  const filtered = useMemo(() => {
    if (!selectedCategory) return matches;
    return matches.filter((p) => p.category === selectedCategory);
  }, [matches, selectedCategory]);

  const hasQuery = Boolean(query.trim());
  const hasMatches = filtered.length > 0;
  const isLoading = hasQuery && loading && products.length === 0;

  // Grid columns via CSS vars so the merchant's column setting flows through
  // (V2 is fixed lg:grid-cols-5; here it honours columns_desktop, default 5).
  const gridStyle = {
    ["--cols-mobile" as string]: `repeat(${colsMobile},minmax(0,1fr))`,
    ["--cols-tablet" as string]: `repeat(${Math.min(3, colsDesktop)},minmax(0,1fr))`,
    ["--cols-desktop" as string]: `repeat(${colsDesktop},minmax(0,1fr))`,
  } as React.CSSProperties;
  const gridClass =
    "grid gap-2 md:gap-6 -mx-2 md:mx-0 grid-cols-[var(--cols-mobile)] md:grid-cols-[var(--cols-tablet)] lg:grid-cols-[var(--cols-desktop)]";

  const fadeUp = {
    hidden: { opacity: 0, y: 20 },
    visible: { opacity: 1, y: 0 },
  };

  const countLabel = `${filtered.length} ${localized(locale, "items", "منتج")}`;

  return (
    <div
      className="bg-background min-h-screen"
      data-gilded-section={sectionId}
      data-testid="storefront-search-page"
    >
      {/* Black hero strip — gold Montserrat title + (with a query) result count. */}
      {showHero && (
        <motion.div
          initial="hidden"
          animate="visible"
          variants={fadeUp}
          className="bg-foreground py-12 md:py-20"
        >
          <div className="container mx-auto px-4 text-center">
            <h1
              className="gld-heading text-4xl md:text-6xl font-bold tracking-[0.08em] uppercase text-[var(--gilded-gold)]"
              style={{
                fontFamily:
                  "var(--gilded-heading-font, 'Montserrat', sans-serif)",
              }}
            >
              {hasQuery ? (
                <>
                  {localized(locale, "Results for", "نتائج البحث عن")}{" "}
                  <span className="text-card/60" dir="auto">
                    “{query}”
                  </span>
                </>
              ) : (
                <InlineEditable
                  sectionId={sectionId}
                  settingKey="empty_heading"
                  value={emptyHeading}
                />
              )}
            </h1>
            {hasQuery && (
              <p className="text-card/50 text-sm mt-3 tracking-[0.2em] uppercase">
                {countLabel}
              </p>
            )}
          </div>
        </motion.div>
      )}

      <div className="container mx-auto px-4 py-8">
        {/* Filters Bar — gilded search box + V2 category pills. */}
        <motion.div
          variants={fadeUp}
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true }}
          className="flex flex-wrap items-center gap-3 mb-10"
        >
          <form
            className="relative flex-1 min-w-[200px] max-w-sm"
            onSubmit={(e) => e.preventDefault()}
            role="search"
          >
            <label htmlFor="gilded-search-input" className="sr-only">
              {searchPlaceholder}
            </label>
            <Search
              size={16}
              aria-hidden="true"
              className="absolute start-3 top-1/2 -translate-y-1/2 text-muted-foreground"
            />
            <input
              id="gilded-search-input"
              type="search"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setSelectedCategory(null);
              }}
              placeholder={searchPlaceholder}
              className="gld-input w-full h-10 ps-9 pe-4"
              data-testid="storefront-search-input"
            />
          </form>

          {/* Category pills — only when a query yields more than one category.
              V2 GildedProductsPage pill: active = bg-foreground text-gold,
              inactive = bg-card bordered, hover gold border. */}
          {hasQuery && categories.length > 1 && (
            <div
              className="flex gap-2 flex-wrap"
              data-testid="storefront-search-categories"
            >
              <button
                type="button"
                onClick={() => setSelectedCategory(null)}
                className={`text-[10px] tracking-[0.15em] uppercase font-semibold px-4 py-2 transition-colors ${
                  !selectedCategory
                    ? "bg-foreground text-[var(--gilded-gold)]"
                    : "bg-card text-foreground border border-border hover:border-[var(--gilded-gold)]"
                }`}
                data-category-id="all"
              >
                {localized(locale, "ALL", "الكل")}
              </button>
              {categories.map((cat) => (
                <button
                  key={cat}
                  type="button"
                  onClick={() => setSelectedCategory(cat)}
                  className={`text-[10px] tracking-[0.15em] uppercase font-semibold px-4 py-2 transition-colors ${
                    selectedCategory === cat
                      ? "bg-foreground text-[var(--gilded-gold)]"
                      : "bg-card text-foreground border border-border hover:border-[var(--gilded-gold)]"
                  }`}
                  data-category-id={cat}
                >
                  {cat}
                </button>
              ))}
            </div>
          )}
        </motion.div>

        {/* Results / states */}
        {isLoading ? (
          /* V2 shimmer skeletons while the catalog loads. */
          <div
            className={gridClass}
            style={gridStyle}
            aria-busy="true"
            aria-label={localized(
              locale,
              "Loading products",
              "جارٍ تحميل المنتجات",
            )}
          >
            {Array.from({ length: colsDesktop * 2 }).map((_, i) => (
              <div key={i} className="space-y-3">
                <div className="aspect-[3/4] bg-muted animate-pulse" />
                <div className="h-3 bg-muted animate-pulse w-3/4" />
                <div className="h-3 bg-muted animate-pulse w-1/3" />
              </div>
            ))}
          </div>
        ) : hasMatches ? (
          <motion.div
            variants={{
              hidden: {},
              visible: { transition: { staggerChildren: 0.05 } },
            }}
            initial="hidden"
            whileInView="visible"
            viewport={{ once: true }}
            className={gridClass}
            style={gridStyle}
            data-testid="storefront-search-grid"
          >
            {filtered.map((product) => (
              <GildedProductCard key={product.id} product={product} />
            ))}
          </motion.div>
        ) : hasQuery ? (
          /* No matches for an active query — V2 "No Products Found" treatment. */
          <div className="text-center py-20">
            <SlidersHorizontal
              size={48}
              className="mx-auto text-[var(--gilded-gold)] mb-4"
              aria-hidden="true"
            />
            <p className="text-xl font-bold tracking-[0.08em] uppercase text-foreground">
              {localized(locale, "No Products Found", "لا توجد منتجات")}
            </p>
            <p className="text-sm text-muted-foreground mt-2 max-w-sm mx-auto">
              {noResultsText}
            </p>
            <Link to="/products" className="gld-btn-outline mt-8">
              {localized(locale, "Browse All", "تصفّح الكل")}
            </Link>
          </div>
        ) : (
          /* Idle — no query yet. */
          <div className="text-center py-20">
            <Search
              size={48}
              className="mx-auto text-[var(--gilded-gold)] mb-4"
              aria-hidden="true"
            />
            <p className="text-xl font-bold tracking-[0.08em] uppercase text-foreground">
              {localized(locale, "Start Your Search", "ابدأ البحث")}
            </p>
            <p className="text-sm text-muted-foreground mt-2 max-w-sm mx-auto">
              {localized(
                locale,
                "Find products by name, category, or keyword.",
                "ابحث عن المنتجات بالاسم أو الفئة أو الكلمة المفتاحية.",
              )}
            </p>
            <Link to="/products" className="gld-btn-outline mt-8">
              {localized(locale, "Browse All", "تصفّح الكل")}
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
