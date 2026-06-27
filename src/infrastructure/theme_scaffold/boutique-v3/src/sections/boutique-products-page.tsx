"use client";
import { useMemo, useState } from "react";
import { Link, Money, useProducts, useLocale, type Product } from "@numueg/theme-sdk";
import { Search, Grid3X3, LayoutList, ArrowLeft, X } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { asNumber, localized, type SectionRenderProps } from "./_shared";

/**
 * Boutique products-listing (PLP) section.
 *
 * Ported from the proven Vionne V3 PLP (breadcrumb, search, category tab strip,
 * sort + grid/list toggle, animated grid, empty state) with the grayscale
 * `vn-*` tokens translated to Boutique's pink palette via shadcn semantic
 * Tailwind classes. Search/sort/toggle are all client-side, matching V2.
 *
 * Data: useProducts() returns the SSR-prefetched catalog. Category chips are
 * derived client-side from product.category. Empty-safe: zero products → the
 * empty state, never a blank page.
 */
export default function BoutiqueProductsPage({ instance }: SectionRenderProps) {
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
        <div className="flex items-center gap-2 text-[10px] uppercase tracking-widest text-muted-foreground mb-6">
          <Link to="/" className="hover:text-foreground transition-colors">
            {localized(locale, "Home", "الرئيسية")}
          </Link>
          <ArrowLeft size={10} className="rtl:rotate-180" />
          <span className="text-foreground">{category ?? localized(locale, "Shop", "المتجر")}</span>
        </div>

        {/* Title */}
        <h1 className="text-2xl md:text-4xl font-bold text-foreground mb-8">
          {category ?? localized(locale, "All products", "كل المنتجات")}
        </h1>

        {/* Search */}
        <div className="relative mb-6 max-w-md">
          <Search
            size={16}
            className="absolute start-0 top-1/2 -translate-y-1/2 text-muted-foreground"
          />
          <input
            type="text"
            placeholder={localized(locale, "Search products", "ابحثي عن منتجات")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full h-10 ps-7 pe-7 text-sm bg-transparent border-b border-border focus:border-primary focus:outline-none transition-colors placeholder:text-muted-foreground"
            data-testid="storefront-products-search"
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch("")}
              aria-label={localized(locale, "Clear search", "مسح البحث")}
              className="absolute end-0 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
            >
              <X size={14} />
            </button>
          )}
        </div>

        {/* Category tabs */}
        {categories.length > 0 && (
          <div
            className="flex gap-6 overflow-x-auto pb-0 mb-6 border-b border-border"
            data-testid="storefront-products-categories"
          >
            <button
              type="button"
              onClick={() => setCategory(null)}
              className={
                "pb-2 text-sm whitespace-nowrap transition-colors " +
                (!category
                  ? "text-foreground border-b-2 border-primary font-medium"
                  : "text-muted-foreground hover:text-foreground")
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
                    ? "text-foreground border-b-2 border-primary font-medium"
                    : "text-muted-foreground hover:text-foreground")
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
          className="flex items-center justify-between mb-6 py-3 border-b border-border"
          data-testid="storefront-products-toolbar"
        >
          <span className="text-[10px] uppercase tracking-widest text-muted-foreground">
            {filtered.length} {localized(locale, "products", "منتج")}
          </span>
          <div className="flex items-center gap-4">
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value)}
              aria-label={localized(locale, "Sort products", "ترتيب المنتجات")}
              className="text-xs px-2 py-1.5 bg-transparent border-b border-border focus:border-primary focus:outline-none transition-colors cursor-pointer"
              data-testid="storefront-products-sort"
            >
              <option value="default">{localized(locale, "Sort by", "الترتيب")}</option>
              <option value="price-asc">{localized(locale, "Price: Low", "السعر: الأقل")}</option>
              <option value="price-desc">{localized(locale, "Price: High", "السعر: الأعلى")}</option>
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
                      ? "text-primary"
                      : "text-muted-foreground hover:text-foreground")
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
                      ? "text-primary"
                      : "text-muted-foreground hover:text-foreground")
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
              <div key={i} className="aspect-[3/4] bg-muted animate-pulse rounded-2xl" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-20">
            <div className="w-12 h-px bg-border mx-auto mb-6" />
            <p className="text-sm text-muted-foreground mb-1">{localized(locale, "No results", "لا توجد نتائج")}</p>
            <p className="text-xs text-muted-foreground">
              {localized(locale, "Try adjusting your search or filter", "جرّبي تعديل البحث أو الفلتر")}
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

/** Inline Boutique product card — pink palette, rounded cards. */
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
        "group block rounded-2xl overflow-hidden bg-card border border-border/50 transition-all hover:shadow-md " +
        (list ? "sm:flex sm:items-center sm:gap-5" : "")
      }
      data-testid="storefront-product-card"
    >
      <div
        className={
          "relative overflow-hidden bg-accent/30 aspect-[3/4] " +
          (list ? "sm:w-40 sm:shrink-0" : "")
        }
      >
        {primary ? (
          <img
            src={primary}
            alt={product.name}
            className="absolute inset-0 w-full h-full object-cover transition-transform duration-500 group-hover:scale-105"
            loading="lazy"
          />
        ) : (
          <div className="absolute inset-0 bg-muted animate-pulse" />
        )}
        {secondary && (
          <img
            src={secondary}
            alt=""
            className="absolute inset-0 w-full h-full object-cover opacity-0 group-hover:opacity-100 transition-opacity duration-300"
            loading="lazy"
          />
        )}

        {product.tags?.[0] && !outOfStock && (
          <span className="absolute top-3 start-3 px-2.5 py-1 bg-white/95 text-foreground rounded-full text-[10px] font-bold">
            {product.tags[0]}
          </span>
        )}
        {hasDiscount && !outOfStock && (
          <span className="absolute top-3 start-3 px-2.5 py-1 bg-primary text-primary-foreground rounded-full text-[10px] font-bold">
            {localized(locale, "Sale", "تخفيض")}
          </span>
        )}
        {outOfStock && (
          <div className="absolute inset-0 bg-white/65 flex items-center justify-center">
            <span className="text-foreground text-[11px] font-bold bg-white px-3 py-1.5 rounded-full border border-border">
              {localized(locale, "Sold out", "نفذت الكمية")}
            </span>
          </div>
        )}
      </div>

      <div className={list ? "p-3 sm:p-0 flex-1" : "p-3 text-center"}>
        <h3 className="text-sm font-medium text-foreground line-clamp-1 group-hover:text-primary transition-colors">
          {product.name}
        </h3>
        <div className={"flex items-baseline gap-2 mt-1 " + (list ? "" : "justify-center")}>
          <span className="text-sm font-bold text-primary">
            <Money amount={price} currency={product.currency} />
          </span>
          {hasDiscount && (
            <span className="text-xs text-muted-foreground line-through">
              <Money amount={compareAt} currency={product.currency} />
            </span>
          )}
        </div>
      </div>
    </Link>
  );
}
