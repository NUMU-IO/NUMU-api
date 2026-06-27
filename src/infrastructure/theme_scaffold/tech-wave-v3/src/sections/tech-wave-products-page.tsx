"use client";
import { useMemo, useState } from "react";
import { Link, Money, useProducts, useLocale, type Product } from "@numueg/theme-sdk";
import { Search, Grid3X3, LayoutList, ArrowRight, X } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { asNumber, localized, type SectionRenderProps } from "./_shared";

/**
 * Tech Wave products-listing (PLP) section.
 *
 * Ported from the proven Vionne V3 PLP (breadcrumb, title, search, category
 * tab strip, toolbar with count + sort + grid/list toggle, animated grid,
 * empty state) and re-skinned to Tech Wave via the `vn-*` utility classes —
 * which this theme's theme.css re-maps onto the dark/neon palette, plus the
 * `tw-card` look on the cards.
 *
 * V2's tech-wave products page rendered through the shared BaseProductsPage
 * (not available in V3); this self-contained port reads the SSR-prefetched
 * catalog via `useProducts()` and does search/sort/filter client-side.
 */
export default function TechWaveProductsPage({ instance }: SectionRenderProps) {
  const s = instance.settings ?? {};

  const locale = useLocale();
  const colsDesktop = asNumber(s.columns_desktop, 3);
  const colsMobile = asNumber(s.columns_mobile, 2);
  const showViewToggle = s.show_view_toggle ?? true;

  const { products, loading } = useProducts();

  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const [sort, setSort] = useState("default");
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");

  const categories = useMemo(() => {
    const seen = new Set<string>();
    for (const p of products) {
      if (p.category) seen.add(p.category);
    }
    return Array.from(seen);
  }, [products]);

  const filtered = useMemo(() => {
    let list = products.slice();

    if (category) {
      list = list.filter((p) => p.category === category);
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter(
        (p) =>
          p.name.toLowerCase().includes(q) ||
          (p.description ?? "").toLowerCase().includes(q) ||
          (p.tags ?? []).some((t) => t.toLowerCase().includes(q)),
      );
    }

    const priceOf = (p: Product) => p.variants?.[0]?.price ?? p.price ?? 0;
    switch (sort) {
      case "price-asc":
        list.sort((a, b) => priceOf(a) - priceOf(b));
        break;
      case "price-desc":
        list.sort((a, b) => priceOf(b) - priceOf(a));
        break;
      case "name":
        list.sort((a, b) => a.name.localeCompare(b.name));
        break;
      default:
        break;
    }
    return list;
  }, [products, category, search, sort]);

  const gridCols = `repeat(${viewMode === "list" ? 1 : colsDesktop}, minmax(0,1fr))`;
  const gridColsMobile = `repeat(${viewMode === "list" ? 1 : colsMobile}, minmax(0,1fr))`;

  return (
    <div className="bg-background" data-testid="storefront-products-page">
      <div className="container mx-auto px-4 py-8 md:py-12">
        {/* Breadcrumb */}
        <div className="flex items-center gap-2 vn-label text-[10px] text-[var(--vn-muted)] mb-6">
          <Link to="/" className="hover:text-[var(--vn-ink)] transition-colors">
            {localized(locale, "Home", "الرئيسية")}
          </Link>
          <ArrowRight size={10} className="rtl:rotate-180" />
          <span className="text-[var(--vn-ink)]">{category ?? localized(locale, "Shop", "المتجر")}</span>
        </div>

        {/* Title */}
        <h1 className="vn-heading text-2xl md:text-4xl text-[var(--vn-ink)] mb-8">
          {category ?? localized(locale, "All products", "كل المنتجات")}
        </h1>

        {/* Search */}
        <div className="relative mb-6 max-w-md">
          <Search
            size={16}
            className="absolute start-3 top-1/2 -translate-y-1/2 text-[var(--vn-muted)]"
          />
          <input
            type="text"
            placeholder={localized(locale, "Search products", "ابحث عن المنتجات")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full h-11 ps-10 pe-9 text-sm rounded-xl tw-inset placeholder:text-[var(--vn-muted)]"
            data-testid="storefront-products-search"
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch("")}
              aria-label={localized(locale, "Clear search", "مسح البحث")}
              className="absolute end-3 top-1/2 -translate-y-1/2 text-[var(--vn-muted)] hover:text-[var(--vn-ink)] transition-colors"
            >
              <X size={14} />
            </button>
          )}
        </div>

        {/* Category tabs */}
        {categories.length > 0 && (
          <div
            className="flex gap-2 overflow-x-auto pb-2 mb-6 scrollbar-hide"
            data-testid="storefront-products-categories"
          >
            <button
              type="button"
              onClick={() => setCategory(null)}
              className={
                "px-4 py-2 rounded-xl text-sm font-bold whitespace-nowrap transition-all " +
                (!category ? "tw-neon-btn relative z-[1]" : "tw-chip text-[var(--vn-ink)]")
              }
              data-testid="storefront-products-category"
              data-category-id="all"
            >
              {localized(locale, "All", "الكل")}
            </button>
            {categories.map((cat) => (
              <button
                key={cat}
                type="button"
                onClick={() => setCategory(cat)}
                className={
                  "px-4 py-2 rounded-xl text-sm font-bold whitespace-nowrap transition-all " +
                  (category === cat ? "tw-neon-btn relative z-[1]" : "tw-chip text-[var(--vn-ink)]")
                }
                data-testid="storefront-products-category"
                data-category-id={cat}
              >
                {cat}
              </button>
            ))}
          </div>
        )}

        {/* Toolbar */}
        <div
          className="flex items-center justify-between mb-6 rounded-xl p-3 tw-card"
          data-testid="storefront-products-toolbar"
        >
          <span className="vn-label text-[10px] text-[var(--vn-muted)]">
            {filtered.length} {localized(locale, "products", "منتج")}
          </span>
          <div className="flex items-center gap-4">
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value)}
              aria-label={localized(locale, "Sort products", "ترتيب المنتجات")}
              className="text-xs px-3 py-2 rounded-lg tw-inset cursor-pointer"
              data-testid="storefront-products-sort"
            >
              <option value="default">{localized(locale, "Sort by", "ترتيب حسب")}</option>
              <option value="price-asc">{localized(locale, "Price: Low", "السعر: من الأقل")}</option>
              <option value="price-desc">{localized(locale, "Price: High", "السعر: من الأعلى")}</option>
              <option value="name">{localized(locale, "Name", "الاسم")}</option>
            </select>
            {showViewToggle && (
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setViewMode("grid")}
                  aria-label={localized(locale, "Grid view", "عرض شبكي")}
                  className={
                    "p-1.5 transition-colors " +
                    (viewMode === "grid"
                      ? "text-[hsl(var(--primary))]"
                      : "text-[var(--vn-muted)] hover:text-[var(--vn-ink)]")
                  }
                >
                  <Grid3X3 size={15} />
                </button>
                <button
                  type="button"
                  onClick={() => setViewMode("list")}
                  aria-label={localized(locale, "List view", "عرض قائمة")}
                  className={
                    "p-1.5 transition-colors " +
                    (viewMode === "list"
                      ? "text-[hsl(var(--primary))]"
                      : "text-[var(--vn-muted)] hover:text-[var(--vn-ink)]")
                  }
                >
                  <LayoutList size={15} />
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Grid / empty / loading */}
        {loading ? (
          <div
            className="grid gap-4 md:gap-5"
            style={{
              gridTemplateColumns: gridCols,
              ["--cols-mobile" as string]: gridColsMobile,
            }}
          >
            {Array.from({ length: colsDesktop * 2 }).map((_, i) => (
              <div key={i} className="aspect-[3/4] vn-shimmer rounded-xl" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-20">
            <div className="w-12 h-px bg-[var(--vn-border)] mx-auto mb-6" />
            <p className="text-sm text-[var(--vn-muted)] mb-1">{localized(locale, "No results", "مفيش نتائج")}</p>
            <p className="text-xs text-[var(--vn-muted)]">
              {localized(locale, "Try adjusting your search or filter", "جرّب تعدّل البحث أو الفلتر")}
            </p>
          </div>
        ) : (
          <motion.div
            layout
            className="grid gap-4 md:gap-5 grid-cols-[var(--cols-mobile)] sm:grid-cols-[var(--cols-tablet)] md:grid-cols-[var(--cols-desktop)]"
            style={
              {
                ["--cols-mobile" as string]: gridColsMobile,
                ["--cols-tablet" as string]:
                  viewMode === "list"
                    ? "repeat(1,minmax(0,1fr))"
                    : `repeat(${Math.min(3, colsDesktop)},minmax(0,1fr))`,
                ["--cols-desktop" as string]: gridCols,
              } as React.CSSProperties
            }
            data-testid="storefront-products-grid"
          >
            <AnimatePresence>
              {filtered.map((product) => (
                <motion.div
                  key={product.id}
                  layout
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                >
                  <ProductCard product={product} list={viewMode === "list"} />
                </motion.div>
              ))}
            </AnimatePresence>
          </motion.div>
        )}
      </div>
    </div>
  );
}

/** Inline Tech Wave product card — neon glass card with sale badge. */
function ProductCard({ product, list }: { product: Product; list?: boolean }) {
  const locale = useLocale();
  const price = product.variants?.[0]?.price ?? product.price ?? 0;
  const compareAt = product.compare_at_price;
  const hasDiscount = typeof compareAt === "number" && compareAt > price;
  const outOfStock = product.in_stock === false;
  const primary = product.images?.[0]?.url;
  const secondary = product.images?.[1]?.url;

  return (
    <Link
      to={`/product/${product.slug || product.id}`}
      className={
        "vn-product-card tw-card group block rounded-2xl overflow-hidden " +
        (list ? "sm:flex sm:items-center sm:gap-5" : "")
      }
      data-testid="storefront-product-card"
    >
      <div
        className={
          "relative overflow-hidden bg-[var(--vn-band)] aspect-[3/4] " +
          (list ? "sm:w-40 sm:shrink-0" : "")
        }
      >
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
        {secondary && (
          <img
            src={secondary}
            alt=""
            className="vn-product-image-secondary"
            loading="lazy"
          />
        )}

        {product.tags?.[0] && !outOfStock && (
          <span className="absolute top-3 start-3 vn-label px-2.5 py-1 tw-badge rounded-full text-[10px]">
            {product.tags[0]}
          </span>
        )}
        {hasDiscount && !outOfStock && (
          <span className="absolute top-3 start-3 vn-label px-2.5 py-1 bg-[var(--vn-sale)] text-white rounded-full text-[10px]">
            {localized(locale, "Sale", "خصم")}
          </span>
        )}
        {outOfStock && (
          <div className="absolute inset-0 bg-[hsl(220,40%,5%/0.7)] flex items-center justify-center">
            <span className="vn-label text-[var(--vn-ink)] text-[11px] tw-chip px-3 py-1.5 rounded-full">
              {localized(locale, "Sold out", "نفذت الكمية")}
            </span>
          </div>
        )}
      </div>

      <div className={list ? "p-3 sm:p-0 flex-1" : "p-3"}>
        <h3 className="text-sm font-medium text-[var(--vn-ink)] line-clamp-1">
          {product.name}
        </h3>
        <div className="flex items-baseline gap-2 mt-1">
          <span className="text-sm font-semibold tw-neon-text">
            <Money amount={price} currency={product.currency} />
          </span>
          {hasDiscount && (
            <span className="text-xs text-[var(--vn-muted)] line-through">
              <Money amount={compareAt} currency={product.currency} />
            </span>
          )}
        </div>
      </div>
    </Link>
  );
}
