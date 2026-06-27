"use client";

import { useMemo, useState } from "react";
import {
  AddToCartButton,
  Link,
  Money,
  useCollectionOptional,
  useCurrentTemplate,
  useLocale,
  useProducts,
  useResolvedSettings,
  type Product,
} from "@numueg/theme-sdk";
import { Search, Grid3X3, LayoutList, ShoppingCart, X } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import {
  asBool,
  asNumber,
  asString,
  localized,
  asImageUrl,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * lux-product-grid — faithful V3 port of the V2 LuxProductsPageSection /
 * LuxProductCard (numu-egyptian-bazaar/src/themes/luxury-minimal/sections/
 * products-page + components/store/luxury-minimal/LuxProductCard).
 *
 * Serves BOTH the `products` and `collection` templates. On a collection route
 * `useCollectionOptional()` returns the active collection (its name → title,
 * its description → optional subtitle, its `.products` → the grid source); on
 * the all-products route `useProducts()` supplies the catalog and the category
 * chips. All V2 className strings kept VERBATIM: container `py-10` shell,
 * `[10px]/0.2em` breadcrumb, `lux-heading text-2xl md:text-3xl` title,
 * underlined `lux-input` search, `lux-chip` / `lux-chip-active` category strip,
 * `[10px]/0.15em` count + `lux-input` sort toolbar, the `aspect-[3/4]` /
 * `bg-[hsl(var(--lux-gray))]` `lux-card` product card with the 700ms 1.03 hover
 * zoom and hairline divider empty state. Engine-wired: useResolvedSettings (so
 * global tokens + dynamic sources resolve) and InlineEditable on the title when
 * a static override is set. View toggle OFF by default (V2 parity).
 */
export default function LuxProductGrid({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();
  const template = useCurrentTemplate();
  const isCollection = template === "collection";

  // On a collection route the host hands us the active collection (name +
  // description + its products). Null on the all-products route.
  const collection = useCollectionOptional();
  const { products: catalogProducts, loading } = useProducts();

  // Source of products: a collection's own list when present, else the catalog.
  const sourceProducts: Product[] =
    isCollection && collection?.products ? collection.products : catalogProducts;

  const colsDesktop = asNumber(s.columns_desktop, 4);
  const colsMobile = asNumber(s.columns_mobile, 2);
  const showViewToggle = asBool(s.show_view_toggle, false);
  const showCategorySubtitle = asBool(s.show_category_subtitle, false);
  const perPage = asNumber(s.products_per_page, 24);

  // Optional static title override; otherwise the collection name / generic.
  const titleOverride = asString(s.title);
  const pageTitle =
    titleOverride ||
    (isCollection && collection?.name) ||
    localized(locale, "All Products", "جميع المنتجات");
  const collectionSubtitle =
    isCollection && showCategorySubtitle ? asString(collection?.description) : "";

  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const [sort, setSort] = useState("default");
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");

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
    let list = sourceProducts.slice();

    if (!isCollection && category) {
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
    if (perPage > 0) list = list.slice(0, perPage);
    return list;
  }, [sourceProducts, isCollection, category, search, sort, perPage]);

  const gridCols = `repeat(${viewMode === "list" ? 1 : colsDesktop}, minmax(0,1fr))`;
  const gridColsMobile = `repeat(${viewMode === "list" ? 1 : colsMobile}, minmax(0,1fr))`;

  return (
    <div className="bg-background" data-lux-section={sectionId} data-testid="storefront-products-page">
      <div className="container mx-auto px-4 py-10">
        {/* Breadcrumb */}
        <div className="flex items-center gap-2 text-[10px] uppercase tracking-[0.2em] text-muted-foreground mb-8">
          <Link to="/" className="hover:text-foreground transition-colors">
            {localized(locale, "Home", "الرئيسية")}
          </Link>
          <span>/</span>
          <span className="text-foreground">
            {category ?? pageTitle}
          </span>
        </div>

        {/* Title */}
        <h1 className="lux-heading text-2xl md:text-3xl mb-10 text-foreground">
          {titleOverride ? (
            <InlineEditable sectionId={sectionId} settingKey="title" value={pageTitle} />
          ) : (
            category ?? pageTitle
          )}
        </h1>

        {/* Collection subtitle (V2 show_category_subtitle) */}
        {collectionSubtitle && (
          <p className="text-sm text-muted-foreground leading-relaxed max-w-xl -mt-6 mb-10">
            {collectionSubtitle}
          </p>
        )}

        {/* Search */}
        <div className="relative mb-6">
          <Search size={16} className="absolute end-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            type="text"
            placeholder={localized(locale, "Search products", "ابحث عن المنتجات")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full h-10 pe-9 ps-4 text-xs lux-input"
            data-testid="storefront-products-search"
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch("")}
              aria-label="Clear search"
              className="absolute start-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
            >
              <X size={14} />
            </button>
          )}
        </div>

        {/* Category tabs */}
        {categories.length > 0 && (
          <div
            className="flex gap-2 overflow-x-auto pb-4 mb-8 scrollbar-hide"
            data-testid="storefront-products-categories"
          >
            <button
              type="button"
              onClick={() => setCategory(null)}
              className={"px-4 py-2 whitespace-nowrap lux-chip " + (!category ? "lux-chip-active" : "")}
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
                className={"px-4 py-2 whitespace-nowrap lux-chip " + (category === cat ? "lux-chip-active" : "")}
                data-testid="storefront-products-category"
                data-category-id={cat}
              >
                {cat}
              </button>
            ))}
          </div>
        )}

        {/* Toolbar */}
        <div className="flex items-center justify-between mb-6" data-testid="storefront-products-toolbar">
          <span className="text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
            {filtered.length} {localized(locale, "products", "منتج")}
          </span>
          <div className="flex items-center gap-4">
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value)}
              aria-label="Sort products"
              className="text-[10px] uppercase tracking-[0.1em] px-3 py-2 lux-input cursor-pointer"
              data-testid="storefront-products-sort"
            >
              <option value="default">{localized(locale, "Sort by", "ترتيب حسب")}</option>
              <option value="price-asc">{localized(locale, "Price: Low to High", "السعر: من الأقل")}</option>
              <option value="price-desc">{localized(locale, "Price: High to Low", "السعر: من الأعلى")}</option>
              <option value="name">{localized(locale, "Name", "الاسم")}</option>
            </select>
            {showViewToggle && (
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setViewMode("grid")}
                  aria-label="Grid view"
                  className={"p-1.5 transition-colors " + (viewMode === "grid" ? "text-foreground" : "text-muted-foreground hover:text-foreground")}
                >
                  <Grid3X3 size={15} />
                </button>
                <button
                  type="button"
                  onClick={() => setViewMode("list")}
                  aria-label="List view"
                  className={"p-1.5 transition-colors " + (viewMode === "list" ? "text-foreground" : "text-muted-foreground hover:text-foreground")}
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
            className="grid gap-4 grid-cols-[var(--cols-mobile)] sm:grid-cols-[var(--cols-tablet)] md:grid-cols-[var(--cols-desktop)]"
            style={
              {
                ["--cols-mobile" as string]: gridColsMobile,
                ["--cols-tablet" as string]: `repeat(${Math.min(3, colsDesktop)},minmax(0,1fr))`,
                ["--cols-desktop" as string]: gridCols,
              } as React.CSSProperties
            }
          >
            {Array.from({ length: colsDesktop * 2 }).map((_, i) => (
              <div key={i} className="aspect-[3/4] lux-shimmer" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-20">
            <div className="w-12 h-px bg-border mx-auto mb-6" />
            <p className="text-sm mb-2 text-foreground">{localized(locale, "No results", "لا توجد نتائج")}</p>
            <p className="text-xs text-muted-foreground">{localized(locale, "Try adjusting your search or filter", "حاول تعديل البحث أو الفلتر")}</p>
          </div>
        ) : (
          <motion.div
            layout
            className="grid gap-4 grid-cols-[var(--cols-mobile)] sm:grid-cols-[var(--cols-tablet)] md:grid-cols-[var(--cols-desktop)]"
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
                <motion.div key={product.id} layout initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                  <LuxProductCard product={product} list={viewMode === "list"} locale={locale} />
                </motion.div>
              ))}
            </AnimatePresence>
          </motion.div>
        )}
      </div>
    </div>
  );
}

/**
 * Inline luxury-minimal product card — faithful V2 LuxProductCard
 * (numu-egyptian-bazaar/src/components/store/luxury-minimal/LuxProductCard +
 * the shared BaseProductCard it configures). Every V2 className kept VERBATIM:
 * the `lux-card` `group` wrapper (card bg + 0.06 hover shadow), the
 * `overflow-hidden` image link, the inner `aspect-[3/4]`
 * `bg-[hsl(var(--lux-gray))]` frame with the `object-contain`
 * `group-hover:scale-[1.03] transition-transform duration-700 ease-out` zoom,
 * the desktop-hover slide-up `py-3 lux-btn` quick-add bar (ShoppingCart 13),
 * the `p-4 pt-3` info block with `text-sm line-clamp-1 mb-1.5 hover:opacity-50`
 * name + `text-sm` / `text-[11px] line-through` price row, and the md:hidden
 * `w-8 h-8 border border-border hover:border-foreground` mobile add button
 * (ShoppingCart 13). Add-to-cart routed through the SDK's AddToCartButton.
 */
function LuxProductCard({
  product,
  list,
  locale,
}: {
  product: Product;
  list?: boolean;
  locale?: string;
}) {
  const price = product.variants?.[0]?.price ?? product.price ?? 0;
  const compareAt = product.compare_at_price;
  const hasDiscount = typeof compareAt === "number" && compareAt > price;
  const primary = asImageUrl(product.images?.[0]);
  const href = `/product/${product.slug || product.id}`;

  return (
    <div
      className={"lux-card group " + (list ? "sm:flex sm:items-center sm:gap-5" : "")}
      data-testid="storefront-product-card"
    >
      {/* Image area — the link wraps ONLY the image; the quick-add button is a
          sibling (V2 BaseProductCard keeps the button out of the anchor so the
          add-to-cart click doesn't also navigate to the PDP). */}
      <div className={"relative overflow-hidden " + (list ? "sm:w-40 sm:shrink-0" : "")}>
        <Link to={href} className="block">
          <div className="overflow-hidden aspect-[3/4] bg-[hsl(var(--lux-gray))]">
            {primary ? (
              <img
                src={primary}
                alt={product.name}
                className="w-full h-full object-contain group-hover:scale-[1.03] transition-transform duration-700 ease-out"
                loading="lazy"
              />
            ) : (
              <div className="w-full h-full lux-shimmer" />
            )}
          </div>
        </Link>

        {/* Desktop quick-add — slide-up hover bar (V2 `py-3 lux-btn`). */}
        <div className="absolute bottom-0 left-0 right-0 p-4 opacity-0 group-hover:opacity-100 translate-y-full group-hover:translate-y-0 transition-all duration-300 hidden md:block">
          <AddToCartButton
            product={product}
            className="w-full py-3 lux-btn"
            label={
              <>
                <ShoppingCart size={13} />
                {localized(locale, "Add to Cart", "أضف إلى السلة")}
              </>
            }
            soldOutLabel={localized(locale, "Sold out", "نفذت الكمية")}
          />
        </div>
      </div>

      {/* Info — `p-4 pt-3`. */}
      <div className={(list ? "flex-1 " : "") + "p-4 pt-3"}>
        <Link to={href}>
          <h3 className="text-sm text-foreground line-clamp-1 mb-1.5 hover:opacity-50 transition-opacity">
            {product.name}
          </h3>
        </Link>
        <div className="flex items-center justify-between">
          <div className="flex items-baseline gap-2">
            <span className="text-sm text-foreground">
              <Money amount={price} currency={product.currency} />
            </span>
            {hasDiscount && (
              <span className="text-[11px] text-muted-foreground line-through">
                <Money amount={compareAt} currency={product.currency} />
              </span>
            )}
          </div>
          {/* Mobile add — small bordered square (V2 mobileAddButton). */}
          <AddToCartButton
            product={product}
            className="md:hidden flex items-center justify-center w-8 h-8 border border-border hover:border-foreground transition-colors"
            label={<ShoppingCart size={13} />}
            soldOutLabel={<ShoppingCart size={13} />}
          />
        </div>
      </div>
    </div>
  );
}
