"use client";

import { useMemo, useState } from "react";
import {
  Image,
  Link,
  Money,
  useLocale,
  useProducts,
  useResolvedSettings,
  type Product,
} from "@numueg/theme-sdk";
import { Search, ShoppingBag, SlidersHorizontal } from "lucide-react";
import { asNumber, asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/** First image URL for a product, strict-null safe. */
function firstImage(p: Product): string | null {
  const img = p.images?.[0];
  return img?.url ?? null;
}

/**
 * Bazar product card — the souk-print look from V2 BzProductCard:
 * hard 2px dark border, offset hover lift (`bz-card-hover`), image
 * zoom on hover, a SALE tag when discounted, an OUT OF STOCK overlay
 * when sold out, the name in the heading face, and an amber price chip.
 * Reused by the grid, related rail, search results, and featured
 * collection so the catalogue feels consistent everywhere.
 */
export function BzProductCard({
  product,
  eager,
}: {
  product: Product;
  /** Load this card's image eagerly (above-the-fold first row) so it paints
   *  immediately instead of waiting for the lazy/intersection trigger. */
  eager?: boolean;
}) {
  const locale = useLocale();
  const slugOrId = product.slug || product.id;
  const image = firstImage(product);
  const price = typeof product.price === "number" ? product.price : 0;
  const compareAt =
    typeof product.compare_at_price === "number"
      ? product.compare_at_price
      : null;
  const hasDiscount = compareAt != null && compareAt > price;
  const outOfStock = product.in_stock === false;

  return (
    <Link
      to={`/products/${slugOrId}`}
      className="group block bz-card-hover rounded-[var(--radius)] overflow-hidden border-[length:var(--bz-card-border)] border-[var(--bz-dark)] bg-[var(--bz-cream)]"
    >
      <div className="relative overflow-hidden bg-white aspect-[3/4] border-b-[length:var(--bz-card-border)] border-[var(--bz-dark)]">
        {image ? (
          <Image
            src={image}
            alt={product.name}
            loading={eager ? "eager" : "lazy"}
            className="w-full h-full object-cover bz-img-zoom"
            sizes="(min-width: 1024px) 25vw, (min-width: 640px) 50vw, 100vw"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center bg-[var(--bz-cream)]">
            <ShoppingBag size={32} className="text-[var(--bz-dark)]/20" aria-hidden="true" />
          </div>
        )}
        {product.tags?.[0] && (
          <span className="absolute top-3 start-3 bz-label px-3 py-1 bg-[var(--bz-amber)] text-[var(--bz-dark)] border-2 border-[var(--bz-dark)] rounded-full text-[10px] shadow-[2px_2px_0_var(--bz-dark)]">
            {product.tags[0]}
          </span>
        )}
        {hasDiscount && !outOfStock && (
          <span className="absolute top-3 end-3 bz-label px-3 py-1 bg-red-500 text-white border-2 border-[var(--bz-dark)] rounded-full text-[10px] shadow-[2px_2px_0_var(--bz-dark)]">
            {localized(locale, "SALE", "تخفيض")}
          </span>
        )}
        {outOfStock && (
          <div className="absolute inset-0 bg-[var(--bz-dark)]/40 flex items-center justify-center">
            <span className="bz-label text-white text-xs bg-[var(--bz-dark)]/80 px-4 py-2 rounded-full">
              {localized(locale, "OUT OF STOCK", "غير متوفر")}
            </span>
          </div>
        )}
      </div>
      <div className="px-3 sm:px-4 py-3 space-y-2">
        <h3 className="bz-heading text-sm sm:text-base text-[var(--bz-dark)] line-clamp-2 leading-tight">
          {product.name}
        </h3>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="bz-label text-[11px] px-2.5 py-1 bg-[var(--bz-amber)] text-[var(--bz-dark)] rounded-full border-2 border-[var(--bz-dark)] whitespace-nowrap">
            <Money amount={price} currency={product.currency} />
          </span>
          {hasDiscount && (
            <span className="text-[11px] text-[var(--bz-gray)] line-through">
              <Money amount={compareAt} currency={product.currency} />
            </span>
          )}
        </div>
      </div>
    </Link>
  );
}

type SortKey = "newest" | "price-asc" | "price-desc";

/**
 * bz-product-grid — listing-page body for the `products` / `collection`
 * templates. Reads `useProducts()` (storefront pre-fetches the catalogue
 * into page context) and renders the souk card grid under a dark hero
 * strip, ported from V2 BzProductsPage — including the search /
 * category-pills / sort filter bar and Load-more pagination.
 *
 * Settings: title, subtitle, columns_desktop, columns_mobile,
 * max_items (0 = all), page_size, show_filters, empty_state_text.
 */
export default function BzProductGrid({
  instance,
  sectionId,
}: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const { products, loading } = useProducts();
  const locale = useLocale();

  const title = asString(s.title) || localized(locale, "SHOP ALL", "تسوّق الكل");
  const subtitle = asString(s.subtitle) || localized(locale, "The full edit, in one place.", "كل التشكيلة في مكان واحد.");
  const colsDesktop = Math.max(1, Math.min(6, asNumber(s.columns_desktop, 4)));
  const colsMobile = Math.max(1, Math.min(3, asNumber(s.columns_mobile, 2)));
  const maxItems = asNumber(s.max_items, 0);
  const pageSize = Math.max(4, asNumber(s.page_size, 12));
  const showFilters = s.show_filters !== false;
  const emptyState =
    asString(s.empty_state_text) || localized(locale, "Nothing here yet — check back soon.", "لسه مفيش حاجة هنا — ارجع تاني قريب.");

  // The full set this section is allowed to show (max_items caps the listing
  // for a "featured" use; 0 = all).
  const pool: Product[] = maxItems > 0 ? products.slice(0, maxItems) : products;

  // ── Filter state (V2 parity) — search + category pills + sort + load-more.
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState<SortKey>("newest");
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [visibleCount, setVisibleCount] = useState(pageSize);

  // Category options derived from the catalogue itself (the storefront pre-
  // fetches products; there's no separate categories context to consult).
  const categories = useMemo(() => {
    const set = new Set<string>();
    for (const p of pool) if (p.category) set.add(p.category);
    return Array.from(set);
  }, [pool]);

  const filtered = useMemo(() => {
    let result = [...pool];
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      result = result.filter(
        (p) =>
          p.name.toLowerCase().includes(q) ||
          (p.tags ?? []).some((t) => t.toLowerCase().includes(q)),
      );
    }
    if (selectedCategory) {
      result = result.filter((p) => p.category === selectedCategory);
    }
    if (sortBy === "price-asc") result.sort((a, b) => (a.price ?? 0) - (b.price ?? 0));
    if (sortBy === "price-desc") result.sort((a, b) => (b.price ?? 0) - (a.price ?? 0));
    // "newest" keeps the backend's incoming order (newest-first).
    return result;
  }, [pool, search, selectedCategory, sortBy]);

  const visible = filtered.slice(0, visibleCount);
  const hasMore = visibleCount < filtered.length;
  const hasActiveFilters = Boolean(search) || Boolean(selectedCategory) || sortBy !== "newest";

  const clearFilters = () => {
    setSearch("");
    setSortBy("newest");
    setSelectedCategory(null);
    setVisibleCount(pageSize);
  };

  const gridStyle = {
    "--bz-cols-mobile": colsMobile,
    "--bz-cols-desktop": colsDesktop,
  } as React.CSSProperties;

  const gridClass =
    "grid gap-3 sm:gap-4 md:gap-6 grid-cols-[repeat(var(--bz-cols-mobile),minmax(0,1fr))] md:grid-cols-[repeat(var(--bz-cols-desktop),minmax(0,1fr))]";

  return (
    <section
      className="bg-[var(--bz-cream)] min-h-screen"
      data-bz-section={sectionId}
    >
      {/* Hero strip */}
      <div className="bg-[var(--bz-dark)] py-8 sm:py-12 md:py-16">
        <div className="container mx-auto px-4 text-center">
          <h1 className="bz-heading text-2xl sm:text-4xl md:text-6xl text-[var(--bz-amber)]">
            <InlineEditable
              sectionId={sectionId}
              settingKey="title"
              value={title}
            />
          </h1>
          <p className="text-[var(--bz-cream)]/50 text-xs sm:text-sm mt-2 md:mt-3">
            <InlineEditable
              sectionId={sectionId}
              settingKey="subtitle"
              value={subtitle}
              multiline
            />
          </p>
        </div>
      </div>

      <div className="container mx-auto px-4 py-8 md:py-12">
        {/* Filters bar (V2 parity) */}
        {showFilters && !loading && pool.length > 0 && (
          <div className="flex flex-col sm:flex-row sm:flex-wrap sm:items-center gap-3 mb-6 md:mb-8">
            <div className="relative w-full sm:flex-1 sm:min-w-[200px] sm:max-w-sm">
              <label htmlFor={`${sectionId}-search`} className="sr-only">
                {localized(locale, "Search products", "ابحث عن المنتجات")}
              </label>
              <Search size={16} aria-hidden="true" className="absolute start-3 top-1/2 -translate-y-1/2 text-[var(--bz-gray)]" />
              <input
                id={`${sectionId}-search`}
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={localized(locale, "Search products…", "ابحث عن المنتجات…")}
                className="w-full h-10 ps-9 pe-4 rounded-full bg-white border border-[var(--bz-dark)]/10 text-sm focus:outline-none focus:border-[var(--bz-amber)] transition-colors"
              />
            </div>

            {categories.length > 0 && (
              <div className="flex gap-2 flex-wrap overflow-x-auto -mx-4 px-4 sm:mx-0 sm:px-0 sm:overflow-visible" role="group" aria-label={localized(locale, "Category filter", "تصفية حسب الفئة")}>
                <button
                  type="button"
                  onClick={() => setSelectedCategory(null)}
                  aria-pressed={!selectedCategory}
                  className={`bz-label px-4 py-2 rounded-full text-[10px] transition-colors ${
                    !selectedCategory
                      ? "bg-[var(--bz-dark)] text-[var(--bz-amber)]"
                      : "bg-white text-[var(--bz-dark)] hover:bg-[var(--bz-dark)]/5"
                  }`}
                >
                  {localized(locale, "ALL", "الكل")}
                </button>
                {categories.map((cat) => (
                  <button
                    type="button"
                    key={cat}
                    onClick={() => setSelectedCategory(cat)}
                    aria-pressed={selectedCategory === cat}
                    className={`bz-label px-4 py-2 rounded-full text-[10px] transition-colors whitespace-nowrap ${
                      selectedCategory === cat
                        ? "bg-[var(--bz-dark)] text-[var(--bz-amber)]"
                        : "bg-white text-[var(--bz-dark)] hover:bg-[var(--bz-dark)]/5"
                    }`}
                  >
                    {cat}
                  </button>
                ))}
              </div>
            )}

            <label htmlFor={`${sectionId}-sort`} className="sr-only">
              {localized(locale, "Sort", "ترتيب")}
            </label>
            <select
              id={`${sectionId}-sort`}
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as SortKey)}
              className="w-full sm:w-auto h-10 px-4 rounded-full bg-white border border-[var(--bz-dark)]/10 text-sm focus:outline-none focus:border-[var(--bz-amber)] cursor-pointer"
            >
              <option value="newest">{localized(locale, "Newest", "الأحدث")}</option>
              <option value="price-asc">{localized(locale, "Price: Low to High", "السعر: من الأقل")}</option>
              <option value="price-desc">{localized(locale, "Price: High to Low", "السعر: من الأعلى")}</option>
            </select>
          </div>
        )}

        {loading && products.length === 0 ? (
          <div className={gridClass} style={gridStyle}>
            {[...Array(colsDesktop * 2)].map((_, i) => (
              <div
                key={i}
                className="rounded-[var(--radius)] bg-[var(--bz-dark)]/5 aspect-[3/4] animate-pulse"
              />
            ))}
          </div>
        ) : visible.length === 0 ? (
          <div className="text-center py-20">
            <SlidersHorizontal
              size={48}
              className="mx-auto text-[var(--bz-amber)] mb-4"
              aria-hidden="true"
            />
            <p className="bz-heading text-xl text-[var(--bz-dark)]">
              {hasActiveFilters ? (
                localized(locale, "No products match your filters.", "لا توجد منتجات مطابقة.")
              ) : (
                <InlineEditable
                  sectionId={sectionId}
                  settingKey="empty_state_text"
                  value={emptyState}
                  multiline
                />
              )}
            </p>
            {hasActiveFilters && (
              <button
                type="button"
                onClick={clearFilters}
                className="bz-btn bz-btn-filled rounded-full text-[11px] mt-6"
              >
                {localized(locale, "CLEAR FILTERS", "مسح التصفية")}
              </button>
            )}
          </div>
        ) : (
          <>
            <div className={gridClass} style={gridStyle}>
              {visible.map((p, i) => (
                <BzProductCard key={p.id} product={p} eager={i < colsDesktop} />
              ))}
            </div>
            {hasMore && (
              <div className="text-center mt-10 md:mt-12">
                <button
                  type="button"
                  onClick={() => setVisibleCount((prev) => prev + pageSize)}
                  className="bz-btn rounded-full text-[11px]"
                >
                  {localized(locale, "LOAD MORE", "عرض المزيد")}
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}
