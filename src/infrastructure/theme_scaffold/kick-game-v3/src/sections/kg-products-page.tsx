"use client";
import { useMemo, useState } from "react";
import { Link, Money, useProducts, useLocale, type Product } from "@numueg/theme-sdk";
import { Search, Grid3X3, LayoutList, ArrowRight, X } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { asNumber, localized, type SectionRenderProps } from "./_shared";

/**
 * Kick Game products-listing (PLP) section.
 *
 * Ported from the proven vionne V3 products-page (breadcrumb, page title,
 * underlined search, scrollable category strip, toolbar with count + sort +
 * grid/list toggle, animated grid, empty state) re-plumbed on the V3 SDK and
 * dressed in the Kick Game look (uppercase headings, dense grid, square cards
 * on #f0efe9 with the black category badge). Empty-safe: zero products → V2
 * empty state, never a blank page.
 */
export default function KGProductsPage({ instance }: SectionRenderProps) {
  const s = instance.settings ?? {};
  const locale = useLocale();

  const colsDesktop = asNumber(s.columns_desktop, 4);
  const colsMobile = asNumber(s.columns_mobile, 2);
  const showViewToggle = s.show_view_toggle ?? false;

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
      <div className="container mx-auto px-4 py-6 md:py-10">
        {/* Breadcrumb */}
        <div className="flex items-center gap-2 vn-label text-[10px] text-[var(--vn-muted)] mb-6">
          <Link to="/" className="hover:text-[var(--vn-ink)] transition-colors">
            {localized(locale, "Home", "الرئيسية")}
          </Link>
          <ArrowRight size={10} className="rtl:rotate-180" />
          <span className="text-[var(--vn-ink)]">{category ?? localized(locale, "Shop", "المتجر")}</span>
        </div>

        {/* Title */}
        <h1 className="kg-heading text-2xl md:text-4xl text-[var(--vn-ink)] mb-8">
          {category ?? localized(locale, "All products", "كل المنتجات")}
        </h1>

        {/* Search */}
        <div className="relative mb-5 max-w-md">
          <Search
            size={16}
            className="absolute start-0 top-1/2 -translate-y-1/2 text-[var(--vn-muted)]"
          />
          <input
            type="text"
            placeholder={localized(locale, "Search products", "ابحث عن منتجات")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full h-10 ps-7 pe-7 text-xs uppercase tracking-[0.03em] font-bold bg-transparent border-b border-[var(--vn-border)] focus:border-[var(--vn-ink)] focus:outline-none transition-colors placeholder:text-[var(--vn-muted)]"
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
            className="flex gap-3 overflow-x-auto pb-4 mb-6 scrollbar-hide"
            data-testid="storefront-products-categories"
          >
            <button
              type="button"
              onClick={() => setCategory(null)}
              className={
                "shrink-0 px-4 py-2 text-[10px] uppercase tracking-[0.03em] font-bold whitespace-nowrap transition-opacity " +
                (!category
                  ? "text-[var(--vn-ink)] opacity-100"
                  : "text-[var(--vn-ink)]/50 hover:text-[var(--vn-ink)]")
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
                  "shrink-0 px-4 py-2 text-[10px] uppercase tracking-[0.03em] font-bold whitespace-nowrap transition-opacity " +
                  (category === cat
                    ? "text-[var(--vn-ink)] opacity-100"
                    : "text-[var(--vn-ink)]/50 hover:text-[var(--vn-ink)]")
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
          className="flex flex-wrap items-center gap-2 justify-between mb-5"
          data-testid="storefront-products-toolbar"
        >
          <span className="text-[11px] uppercase tracking-[0.03em] font-bold text-[var(--vn-ink)] opacity-50">
            {filtered.length} {localized(locale, "products", "منتج")}
          </span>
          <div className="flex items-center gap-3">
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value)}
              aria-label="Sort products"
              className="text-[11px] uppercase tracking-[0.03em] px-3 h-9 bg-transparent outline-none cursor-pointer font-bold text-[var(--vn-ink)] border border-[var(--vn-border)] rounded"
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
            className="grid gap-2"
            style={{
              gridTemplateColumns: gridCols,
              ["--cols-mobile" as string]: gridColsMobile,
            }}
          >
            {Array.from({ length: colsDesktop * 2 }).map((_, i) => (
              <div key={i} className="aspect-square vn-shimmer" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-20">
            <p className="text-lg uppercase tracking-[-0.02em] font-bold text-[var(--vn-ink)] mb-2">
              {localized(locale, "No results", "مفيش نتايج")}
            </p>
            <p className="text-sm uppercase text-[var(--vn-ink)] opacity-40">
              {localized(locale, "Try adjusting your search or filter", "جرّب تعدّل البحث أو الفلتر")}
            </p>
          </div>
        ) : (
          <motion.div
            layout
            className="grid gap-2 grid-cols-[var(--cols-mobile)] sm:grid-cols-[var(--cols-tablet)] md:grid-cols-[var(--cols-desktop)]"
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

/** Inline Kick Game product card — mirrors KGProductCard's markup/classes. */
function ProductCard({ product, list }: { product: Product; list?: boolean }) {
  const locale = useLocale();
  const price = product.variants?.[0]?.price ?? product.price ?? 0;
  const compareAt = product.compare_at_price;
  const hasDiscount = typeof compareAt === "number" && compareAt > price;
  const discountPct = hasDiscount
    ? Math.round(((compareAt - price) / compareAt) * 100)
    : 0;
  const outOfStock = product.in_stock === false;
  const categoryBadge = product.tags?.[0] || product.category;
  const brandTag = product.tags?.[1] || product.tags?.[0];
  const primary = product.images?.[0]?.url;

  return (
    <Link
      to={`/product/${product.slug || product.id}`}
      className={
        "kg-product-card group block " +
        (list ? "sm:flex sm:items-center sm:gap-5" : "")
      }
      style={{ textDecoration: "none" }}
      data-testid="storefront-product-card"
    >
      <div
        className={
          "relative overflow-hidden bg-[#f0efe9] aspect-square " +
          (list ? "sm:w-40 sm:shrink-0" : "")
        }
      >
        {primary ? (
          <img
            src={primary}
            alt={product.name}
            className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-[1.03]"
            loading="lazy"
          />
        ) : (
          <div className="absolute inset-0 vn-shimmer" />
        )}

        {/* Left badges */}
        <div className="absolute top-2 start-2 flex flex-col items-start gap-1">
          {hasDiscount && !outOfStock && (
            <span className="text-[10px] font-bold uppercase tracking-wider px-2.5 py-1 text-white bg-[#c0392b]">
              -{discountPct}%
            </span>
          )}
          {categoryBadge && !outOfStock && (
            <span className="text-[10px] font-bold uppercase tracking-wider px-2.5 py-1 text-white bg-[#121212]">
              {categoryBadge}
            </span>
          )}
        </div>
        {outOfStock && (
          <div className="absolute inset-0 bg-[#fcfbf7]/65 flex items-center justify-center">
            <span className="text-[11px] font-bold uppercase tracking-wider text-[var(--vn-ink)] bg-[#fcfbf7] px-3 py-1.5 border border-[var(--vn-border)]">
              {localized(locale, "Sold out", "نفدت الكمية")}
            </span>
          </div>
        )}
      </div>

      <div className={list ? "mt-3 sm:mt-0 flex-1" : "mt-3 text-start"}>
        {brandTag && (
          <p className="text-[10px] font-bold uppercase tracking-[0.08em] mb-1 text-[#121212aa]">
            {brandTag}
          </p>
        )}
        <h3 className="font-medium text-sm leading-snug line-clamp-2 text-[var(--vn-ink)]">
          {product.name}
        </h3>
        <div className="flex items-baseline gap-2 mt-1.5">
          <span className="font-bold text-sm text-[var(--vn-ink)]">
            <Money amount={price} currency={product.currency} />
          </span>
          {hasDiscount && (
            <span className="text-xs text-[#12121260] line-through">
              <Money amount={compareAt} currency={product.currency} />
            </span>
          )}
        </div>
      </div>
    </Link>
  );
}
