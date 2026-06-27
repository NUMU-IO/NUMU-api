"use client";
import { useMemo, useState } from "react";
import { Link, Money, useProducts, useLocale, type Product } from "@numueg/theme-sdk";
import { Search, Grid3X3, LayoutList, ArrowRight, X } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { asNumber, localized, type SectionRenderProps } from "./_shared";

/**
 * Modern products-listing (PLP) section.
 *
 * Copied from the proven Vionne V3 PLP and re-identified for Modern. The
 * `vn-*` classes + `--vn-*` tokens are defined in Modern's src/theme.css
 * (re-mapped to Modern's teal palette) so the listing renders in Modern's
 * identity: breadcrumb, page title, underlined search, category tab strip,
 * toolbar (count + sort + grid/list toggle), animated grid, empty state.
 *
 * Data: `useProducts()` returns the SSR-prefetched catalog (the host passes
 * `page.data.products` on the products route). Category chips are derived
 * client-side from `product.category`; search, sort and the grid/list toggle
 * are all client-side.
 *
 * Empty-safe: zero products → the empty state (hairline + message), never a
 * blank page; a still-loading list shows shimmer placeholders.
 */
export default function ModernProductsPage({ instance }: SectionRenderProps) {
  const s = instance.settings ?? {};
  const locale = useLocale();

  const colsDesktop = asNumber(s.columns_desktop, 4);
  const colsMobile = asNumber(s.columns_mobile, 2);
  const showViewToggle = s.show_view_toggle ?? true;

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
            className="absolute start-0 top-1/2 -translate-y-1/2 text-[var(--vn-muted)]"
          />
          <input
            type="text"
            placeholder={localized(locale, "Search products", "ابحث عن منتجات")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full h-10 ps-7 pe-7 text-sm bg-transparent border-b border-[var(--vn-border)] focus:border-[var(--vn-ink)] focus:outline-none transition-colors placeholder:text-[var(--vn-muted)]"
            data-testid="storefront-products-search"
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

        {/* Category tabs */}
        {categories.length > 0 && (
          <div
            className="flex gap-6 overflow-x-auto pb-0 mb-6 border-b border-[var(--vn-border)]"
            data-testid="storefront-products-categories"
          >
            <button
              type="button"
              onClick={() => setCategory(null)}
              className={
                "pb-2 text-sm whitespace-nowrap transition-colors " +
                (!category
                  ? "text-[var(--vn-ink)] border-b-2 border-[var(--vn-ink)] font-medium"
                  : "text-[var(--vn-muted)] hover:text-[var(--vn-ink)]")
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
                    ? "text-[var(--vn-ink)] border-b-2 border-[var(--vn-ink)] font-medium"
                    : "text-[var(--vn-muted)] hover:text-[var(--vn-ink)]")
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
          className="flex items-center justify-between mb-6 py-3 border-b border-[var(--vn-border)]"
          data-testid="storefront-products-toolbar"
        >
          <span className="vn-label text-[10px] text-[var(--vn-muted)]">
            {filtered.length} {localized(locale, "products", "منتج")}
          </span>
          <div className="flex items-center gap-4">
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value)}
              aria-label="Sort products"
              className="text-xs px-2 py-1.5 bg-transparent border-b border-[var(--vn-border)] focus:border-[var(--vn-ink)] focus:outline-none transition-colors cursor-pointer"
              data-testid="storefront-products-sort"
            >
              <option value="default">{localized(locale, "Sort by", "ترتيب حسب")}</option>
              <option value="price-asc">{localized(locale, "Price: Low", "السعر: الأقل")}</option>
              <option value="price-desc">{localized(locale, "Price: High", "السعر: الأعلى")}</option>
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
                      ? "text-[var(--vn-ink)]"
                      : "text-[var(--vn-muted)] hover:text-[var(--vn-ink)]")
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
                      ? "text-[var(--vn-ink)]"
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
              <div key={i} className="aspect-[3/4] vn-shimmer rounded-2xl" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-20">
            <div className="w-12 h-px bg-[var(--vn-border)] mx-auto mb-6" />
            <p className="text-sm text-[var(--vn-muted)] mb-1">{localized(locale, "No results", "لا توجد نتائج")}</p>
            <p className="text-xs text-[var(--vn-muted)]">
              {localized(locale, "Try adjusting your search or filter", "جرّب تعديل البحث أو الفلتر")}
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

/** Inline Modern product card — same markup the home grid uses (vn-* classes). */
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
        "vn-product-card group block " +
        (list ? "sm:flex sm:items-center sm:gap-5" : "")
      }
      data-testid="storefront-product-card"
    >
      <div
        className={
          "relative overflow-hidden rounded-2xl bg-[var(--vn-band)] aspect-[3/4] " +
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
          <span className="absolute top-3 start-3 vn-label px-2.5 py-1 bg-white/95 text-[var(--vn-ink)] rounded-full text-[10px]">
            {product.tags[0]}
          </span>
        )}
        {hasDiscount && !outOfStock && (
          <span className="absolute top-3 start-3 vn-label px-2.5 py-1 bg-[var(--vn-sale)] text-white rounded-full text-[10px]">
            {localized(locale, "Sale", "خصم")}
          </span>
        )}
        {outOfStock && (
          <div className="absolute inset-0 bg-white/65 flex items-center justify-center">
            <span className="vn-label text-[var(--vn-ink)] text-[11px] bg-white px-3 py-1.5 rounded-full border border-[var(--vn-border)]">
              {localized(locale, "Sold out", "نفذت الكمية")}
            </span>
          </div>
        )}
      </div>

      <div className={list ? "mt-3 sm:mt-0 flex-1" : "mt-3 px-1"}>
        <h3 className="text-sm font-medium text-[var(--vn-ink)] line-clamp-1">
          {product.name}
        </h3>
        <div className="flex items-baseline gap-2 mt-1">
          <span className="text-sm font-semibold text-[var(--vn-ink)]">
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
