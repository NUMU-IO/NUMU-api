"use client";
import { useMemo, useState } from "react";
import { Link, Money, useProducts, useLocale, type Product } from "@numueg/theme-sdk";
import { Search, Grid3X3, LayoutList, ArrowRight, X } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { asNumber, localized, type SectionRenderProps } from "./_shared";

/**
 * Elegant products-listing (PLP) section.
 *
 * Ported from the proven vionne V3 PLP and re-skinned in elegant's
 * warm-brown + serif aesthetic via the `eg-*` design vocabulary defined
 * in src/theme.css.
 *
 * Data: `useProducts()` returns the SSR-prefetched catalog (the host passes
 * `page.data.products` on the products route). Category chips are derived
 * client-side from `product.category`; search, sort and the grid/list toggle
 * are all client-side.
 *
 * Empty-safe: zero products → an empty state (hairline + message), never a
 * blank page; a still-loading list shows shimmer placeholders.
 */
export default function ElegantProductsPage({ instance }: SectionRenderProps) {
  const s = instance.settings ?? {};

  const colsDesktop = asNumber(s.columns_desktop, 4);
  const colsMobile = asNumber(s.columns_mobile, 2);
  const showViewToggle = s.show_view_toggle ?? true;
  const locale = useLocale();

  const { products, loading } = useProducts();

  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const [sort, setSort] = useState("default");
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");

  // Derive the category chips from the catalog.
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
        <div className="flex items-center gap-2 eg-label text-[10px] text-[var(--eg-muted)] mb-6">
          <Link to="/" className="hover:text-[var(--eg-ink)] transition-colors">
            {localized(locale, "Home", "الرئيسية")}
          </Link>
          <ArrowRight size={10} className="rtl:rotate-180" />
          <span className="text-[var(--eg-ink)]">{category ?? localized(locale, "Shop", "المتجر")}</span>
        </div>

        {/* Title */}
        <h1 className="eg-heading text-2xl md:text-4xl text-[var(--eg-ink)] mb-8">
          {category ?? localized(locale, "All products", "جميع المنتجات")}
        </h1>

        {/* Search */}
        <div className="relative mb-6 max-w-md">
          <Search
            size={16}
            className="absolute start-0 top-1/2 -translate-y-1/2 text-[var(--eg-muted)]"
          />
          <input
            type="text"
            placeholder={localized(locale, "Search products", "ابحث عن المنتجات")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full h-10 ps-7 pe-7 text-sm bg-transparent border-b border-[var(--eg-border)] focus:border-[var(--eg-ink)] focus:outline-none transition-colors placeholder:text-[var(--eg-muted)]"
            data-testid="storefront-products-search"
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch("")}
              aria-label="Clear search"
              className="absolute end-0 top-1/2 -translate-y-1/2 text-[var(--eg-muted)] hover:text-[var(--eg-ink)] transition-colors"
            >
              <X size={14} />
            </button>
          )}
        </div>

        {/* Category tabs */}
        {categories.length > 0 && (
          <div
            className="flex gap-6 overflow-x-auto pb-0 mb-6 border-b border-[var(--eg-border)]"
            data-testid="storefront-products-categories"
          >
            <button
              type="button"
              onClick={() => setCategory(null)}
              className={
                "pb-2 text-sm whitespace-nowrap transition-colors " +
                (!category
                  ? "text-[var(--eg-ink)] border-b-2 border-[var(--eg-ink)] font-medium"
                  : "text-[var(--eg-muted)] hover:text-[var(--eg-ink)]")
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
                  "pb-2 text-sm whitespace-nowrap transition-colors " +
                  (category === cat
                    ? "text-[var(--eg-ink)] border-b-2 border-[var(--eg-ink)] font-medium"
                    : "text-[var(--eg-muted)] hover:text-[var(--eg-ink)]")
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
          className="flex items-center justify-between mb-6 py-3 border-b border-[var(--eg-border)]"
          data-testid="storefront-products-toolbar"
        >
          <span className="eg-label text-[10px] text-[var(--eg-muted)]">
            {filtered.length} {localized(locale, "products", "منتج")}
          </span>
          <div className="flex items-center gap-4">
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value)}
              aria-label="Sort products"
              className="text-xs px-2 py-1.5 bg-transparent border-b border-[var(--eg-border)] focus:border-[var(--eg-ink)] focus:outline-none transition-colors cursor-pointer"
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
                  aria-label="Grid view"
                  className={
                    "p-1.5 transition-colors " +
                    (viewMode === "grid"
                      ? "text-[var(--eg-ink)]"
                      : "text-[var(--eg-muted)] hover:text-[var(--eg-ink)]")
                  }
                >
                  <Grid3X3 size={15} />
                </button>
                <button
                  type="button"
                  onClick={() => setViewMode("list")}
                  aria-label="List view"
                  className={
                    "p-1.5 transition-colors " +
                    (viewMode === "list"
                      ? "text-[var(--eg-ink)]"
                      : "text-[var(--eg-muted)] hover:text-[var(--eg-ink)]")
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
              <div key={i} className="aspect-[3/4] eg-shimmer rounded" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-20">
            <div className="w-12 h-px bg-[var(--eg-border)] mx-auto mb-6" />
            <p className="text-sm text-[var(--eg-muted)] mb-1">{localized(locale, "No results", "لا توجد نتائج")}</p>
            <p className="text-xs text-[var(--eg-muted)]">
              {localized(locale, "Try adjusting your search or filter", "حاول تعديل البحث أو الفلتر")}
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
                  <ProductCard product={product} list={viewMode === "list"} locale={locale} />
                </motion.div>
              ))}
            </AnimatePresence>
          </motion.div>
        )}
      </div>
    </div>
  );
}

/** Inline elegant product card. */
function ProductCard({ product, list, locale }: { product: Product; list?: boolean; locale?: string }) {
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
        "eg-product-card group block " +
        (list ? "sm:flex sm:items-center sm:gap-5" : "")
      }
      data-testid="storefront-product-card"
    >
      <div
        className={
          "relative overflow-hidden rounded-lg bg-[var(--eg-band)] aspect-[3/4] " +
          (list ? "sm:w-40 sm:shrink-0" : "")
        }
      >
        {primary ? (
          <img
            src={primary}
            alt={product.name}
            className="eg-product-image absolute inset-0 w-full h-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="absolute inset-0 eg-shimmer" />
        )}
        {secondary && (
          <img
            src={secondary}
            alt=""
            className="eg-product-image-secondary"
            loading="lazy"
          />
        )}

        {product.tags?.[0] && !outOfStock && (
          <span className="absolute top-3 start-3 eg-label px-2.5 py-1 bg-white/95 text-[var(--eg-ink)] rounded-full text-[10px]">
            {product.tags[0]}
          </span>
        )}
        {hasDiscount && !outOfStock && (
          <span className="absolute top-3 start-3 eg-label px-2.5 py-1 bg-[var(--eg-sale)] text-white rounded-full text-[10px]">
            {localized(locale, "Sale", "تخفيض")}
          </span>
        )}
        {outOfStock && (
          <div className="absolute inset-0 bg-white/65 flex items-center justify-center">
            <span className="eg-label text-[var(--eg-ink)] text-[11px] bg-white px-3 py-1.5 rounded-full border border-[var(--eg-border)]">
              {localized(locale, "Sold out", "نفذت الكمية")}
            </span>
          </div>
        )}
      </div>

      <div className={list ? "mt-3 sm:mt-0 flex-1" : "mt-3 px-1"}>
        <h3 className="text-sm font-medium text-[var(--eg-ink)] line-clamp-1">
          {product.name}
        </h3>
        <div className="flex items-baseline gap-2 mt-1">
          <span className="text-sm font-semibold text-[var(--eg-ink)]">
            <Money amount={price} currency={product.currency} />
          </span>
          {hasDiscount && (
            <span className="text-xs text-[var(--eg-muted)] line-through">
              <Money amount={compareAt} currency={product.currency} />
            </span>
          )}
        </div>
      </div>
    </Link>
  );
}
