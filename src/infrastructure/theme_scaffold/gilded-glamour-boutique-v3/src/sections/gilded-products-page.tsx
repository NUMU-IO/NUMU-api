"use client";

import { useMemo, useState } from "react";
import {
  useCollectionOptional,
  useCurrentTemplate,
  useLocale,
  useProducts,
  useResolvedSettings,
  type Product,
} from "@numueg/theme-sdk";
import { Search, SlidersHorizontal } from "lucide-react";
import { motion } from "framer-motion";
import { asNumber, asString, localized, usePageData, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";
import { GildedProductCard } from "./_product-card";

/**
 * gilded-products-page — faithful V3 port of the V2 GildedProductsPage
 * (numu-egyptian-bazaar/src/components/store/gilded-glamour-boutique/GildedProductsPage.tsx)
 * + its GildedProductCard (shared here via ./_product-card).
 *
 * Serves BOTH the `products` and `collection` templates. On a collection route
 * `useCollectionOptional()` returns the active collection (its name → hero
 * title, its `.products` → the grid source); on the all-products route
 * `useProducts()` supplies the catalog and the category chips.
 *
 * Layout kept VERBATIM from V2:
 *  - Black hero strip `bg-foreground py-12 md:py-20`, gold Montserrat title
 *    `text-4xl md:text-6xl font-bold tracking-[0.08em] uppercase`, product-count
 *    subtitle `text-card/50 tracking-[0.2em] uppercase`.
 *  - Filters bar `flex flex-wrap items-center gap-3 mb-10`: search input
 *    (`gld-input h-10 ps-9` + Search icon), category pills (`gld-chip` /
 *    `gld-chip-active`, `text-[10px] tracking-[0.15em] uppercase px-4 py-2`)
 *    built from the catalog's product categories, sort select (`gld-input` —
 *    Newest / Price asc / Price desc).
 *  - Grid `grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-2 md:gap-6` driven by
 *    `columns_desktop` / `columns_mobile` CSS vars; renders the shared
 *    GildedProductCard. `gld-shimmer` skeletons while loading; empty state =
 *    SlidersHorizontal + "No Products Found".
 *
 * Engine-wired: `useResolvedSettings` (global tokens + dynamic sources + draft
 * preview), `useLocale` for bilingual defaults, `InlineEditable` on the hero
 * title. The brand gold reads `--gilded-gold` so the merchant's Accent picker
 * repaints it. Default colours are byte-identical to V2.
 */
export default function GildedProductsPage({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();
  const template = useCurrentTemplate();
  const isCollection = template === "collection";

  // On a collection route the host hands us the active collection (name +
  // its products). `useCollectionOptional()` only resolves when a
  // CollectionProvider wraps the section; the storefront instead forwards the
  // resolved collection in the mount page data, so fall back to that so the
  // hero title shows the real collection name and the grid uses its products.
  const pageData = usePageData();
  const collection =
    useCollectionOptional() ??
    ((pageData?.data as { collection?: { name?: string; products?: unknown[] } } | undefined)
      ?.collection ??
      null);
  const { products: catalogProducts, loading } = useProducts();

  // Source of products: a collection's own list when present, else the catalog.
  const collectionProducts = collection?.products as Product[] | undefined;
  const sourceProducts: Product[] =
    isCollection && collectionProducts?.length ? collectionProducts : catalogProducts;

  const colsDesktop = Math.max(2, Math.min(5, asNumber(s.columns_desktop, 5)));
  const colsMobile = Math.max(1, Math.min(3, asNumber(s.columns_mobile, 2)));

  // Optional static title override; otherwise the collection name / "ALL PRODUCTS".
  const titleOverride = asString(s.title);
  const heroTitle =
    titleOverride ||
    (isCollection && collection?.name) ||
    localized(locale, "ALL PRODUCTS", "كل المنتجات");

  const [search, setSearch] = useState("");
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState("newest");

  // Category chips only on the all-products page (a collection IS the filter).
  const categories = useMemo(() => {
    if (isCollection) return [] as string[];
    const seen = new Set<string>();
    for (const p of sourceProducts) {
      if (p.category) seen.add(p.category);
    }
    return Array.from(seen);
  }, [sourceProducts, isCollection]);

  const filtered = useMemo(() => {
    let result = sourceProducts.slice();

    if (search.trim()) {
      const q = search.trim().toLowerCase();
      result = result.filter(
        (p) =>
          p.name.toLowerCase().includes(q) ||
          (p.description ?? "").toLowerCase().includes(q),
      );
    }
    if (!isCollection && selectedCategory) {
      result = result.filter((p) => p.category === selectedCategory);
    }

    const priceOf = (p: Product) => p.variants?.[0]?.price ?? p.price ?? 0;
    if (sortBy === "price-asc") result.sort((a, b) => priceOf(a) - priceOf(b));
    if (sortBy === "price-desc") result.sort((a, b) => priceOf(b) - priceOf(a));
    return result;
  }, [sourceProducts, isCollection, search, selectedCategory, sortBy]);

  // Grid columns via CSS vars so the merchant's column settings flow through
  // (mobile = columns_mobile, tablet = min(3,desktop), desktop = columns_desktop).
  const gridStyle = {
    ["--cols-mobile" as string]: `repeat(${colsMobile},minmax(0,1fr))`,
    ["--cols-tablet" as string]: `repeat(${Math.min(3, colsDesktop)},minmax(0,1fr))`,
    ["--cols-desktop" as string]: `repeat(${colsDesktop},minmax(0,1fr))`,
  } as React.CSSProperties;
  const gridClass =
    "grid gap-2 md:gap-6 -mx-2 md:mx-0 grid-cols-[var(--cols-mobile)] md:grid-cols-[var(--cols-tablet)] lg:grid-cols-[var(--cols-desktop)]";

  const fadeUp = {
    hidden: { opacity: 0, y: 40 },
    visible: { opacity: 1, y: 0, transition: { duration: 0.6, ease: "easeOut" } },
  };

  const countLabel = `${sourceProducts.length} ${localized(locale, "items", "منتج")}`;

  return (
    <div className="bg-background min-h-screen" data-testid="storefront-products-page">
      {/* Hero strip */}
      <motion.div
        initial="hidden"
        animate="visible"
        variants={fadeUp}
        className="bg-foreground py-12 md:py-20"
      >
        <div className="container mx-auto px-4 text-center">
          <h1 className="gld-heading text-4xl md:text-6xl font-bold tracking-[0.08em] uppercase text-[var(--gilded-gold)]">
            {isCollection && collection?.name ? (
              heroTitle
            ) : (
              <InlineEditable sectionId={sectionId} settingKey="title" value={heroTitle} />
            )}
          </h1>
          <p className="text-card/50 text-sm mt-3 tracking-[0.2em] uppercase">{countLabel}</p>
        </div>
      </motion.div>

      <div className="container mx-auto px-4 py-8">
        {/* Filters Bar */}
        <motion.div
          variants={fadeUp}
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true }}
          className="flex flex-wrap items-center gap-3 mb-10"
        >
          {/* Search */}
          <div className="relative flex-1 min-w-[200px] max-w-sm">
            <Search
              size={16}
              className="absolute start-3 top-1/2 -translate-y-1/2 text-muted-foreground"
            />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={localized(locale, "Search products...", "ابحث عن المنتجات...")}
              className="gld-input w-full h-10 ps-9 pe-4"
              data-testid="storefront-products-search"
            />
          </div>

          {/* Category pills */}
          {categories.length > 0 && (
            <div className="flex gap-2 flex-wrap" data-testid="storefront-products-categories">
              <button
                type="button"
                onClick={() => setSelectedCategory(null)}
                className={`gld-chip px-4 py-2 ${!selectedCategory ? "gld-chip-active" : ""}`}
                data-testid="storefront-products-category"
                data-category-id="all"
              >
                {localized(locale, "ALL", "الكل")}
              </button>
              {categories.map((cat) => (
                <button
                  key={cat}
                  type="button"
                  onClick={() => setSelectedCategory(cat)}
                  className={`gld-chip px-4 py-2 ${selectedCategory === cat ? "gld-chip-active" : ""}`}
                  data-testid="storefront-products-category"
                  data-category-id={cat}
                >
                  {cat}
                </button>
              ))}
            </div>
          )}

          {/* Sort */}
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            aria-label={localized(locale, "Sort products", "ترتيب المنتجات")}
            className="gld-input h-10 px-4 cursor-pointer"
            data-testid="storefront-products-sort"
          >
            <option value="newest">{localized(locale, "Newest", "الأحدث")}</option>
            <option value="price-asc">
              {localized(locale, "Price: Low → High", "السعر: من الأقل")}
            </option>
            <option value="price-desc">
              {localized(locale, "Price: High → Low", "السعر: من الأعلى")}
            </option>
          </select>
        </motion.div>

        {/* Product Grid */}
        {loading ? (
          <div
            className={gridClass}
            style={gridStyle}
            aria-busy="true"
            aria-label={localized(locale, "Loading products", "جارٍ تحميل المنتجات")}
          >
            {Array.from({ length: colsDesktop * 2 }).map((_, i) => (
              <div key={i} className="space-y-3">
                <div className="aspect-[3/4] gld-shimmer" />
                <div className="h-3 gld-shimmer w-3/4" />
                <div className="h-3 gld-shimmer w-1/3" />
              </div>
            ))}
          </div>
        ) : (
          <motion.div
            variants={{
              visible: { transition: { staggerChildren: 0.1, delayChildren: 0.1 } },
            }}
            initial="hidden"
            whileInView="visible"
            viewport={{ once: true }}
            className={gridClass}
            style={gridStyle}
            data-testid="storefront-products-grid"
          >
            {filtered.map((product) => (
              <GildedProductCard key={product.id} product={product} />
            ))}
          </motion.div>
        )}

        {!loading && filtered.length === 0 && (
          <div className="text-center py-20">
            <SlidersHorizontal size={48} className="mx-auto text-[var(--gilded-gold)] mb-4" />
            <p className="text-xl font-bold tracking-[0.08em] uppercase text-foreground">
              {localized(locale, "No Products Found", "لا توجد منتجات")}
            </p>
            <p className="text-sm text-muted-foreground mt-2">
              {localized(locale, "Try adjusting your filters", "جرّب تعديل الفلاتر")}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
