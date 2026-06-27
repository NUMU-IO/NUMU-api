"use client";

import { useMemo, useState } from "react";
import {
  Link,
  Money,
  useLocale,
  useProducts,
  useResolvedSettings,
  type Product,
} from "@numueg/theme-sdk";
import { Search } from "lucide-react";
import {
  asNumber,
  asString,
  localized,
  usePageData,
  asImageUrl,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * lux-search-results — body for the `search` template, faithful to the V2
 * Luxury Minimal products page (uppercase tracked `lux-heading`, `lux-input`
 * underlined search, hairline empty state, the SAME `lux-product-card` grid as
 * the products page / product-grid). Engine-wired: `useResolvedSettings` so
 * global tokens + dynamic sources resolve, `InlineEditable` on the title.
 *
 * Data-wiring (kept from the working original): the visitor's query is seeded
 * from the host page descriptor (`usePageData().data.query` / `.q`, stashed by
 * the storefront /search route) and a live in-place box lets visitors refine
 * without a round-trip. Matching runs over the SSR-prefetched catalog
 * (`useProducts()`) by name/description so the customizer preview and the live
 * store both show results.
 */
export default function LuxSearchResults({
  instance,
  sectionId,
}: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();
  const { products } = useProducts();
  const pageData = usePageData();

  const title = asString(s.title) || localized(locale, "Search", "البحث");
  const searchPlaceholder = localized(
    locale,
    "Search products",
    "ابحث عن المنتجات",
  );
  const colsDesktop = Math.max(1, Math.min(5, asNumber(s.columns_desktop, 4)));
  const colsMobile = 2;

  const initialQuery =
    asString(pageData?.data?.query) || asString(pageData?.data?.q);
  const [query, setQuery] = useState(initialQuery);

  const matches: Product[] = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return [];
    return products.filter(
      (p) =>
        p.name?.toLowerCase().includes(needle) ||
        (p.description ?? "").toLowerCase().includes(needle) ||
        (p.tags ?? []).some((t) => t.toLowerCase().includes(needle)),
    );
  }, [query, products]);

  const hasQuery = Boolean(query.trim());
  const hasMatches = matches.length > 0;

  const gridStyle = {
    ["--cols-mobile" as string]: `repeat(${colsMobile},minmax(0,1fr))`,
    ["--cols-tablet" as string]: `repeat(${Math.min(3, colsDesktop)},minmax(0,1fr))`,
    ["--cols-desktop" as string]: `repeat(${colsDesktop},minmax(0,1fr))`,
  } as React.CSSProperties;

  return (
    <section
      className="bg-background min-h-[70vh]"
      data-lux-section={sectionId}
      data-testid="storefront-search-page"
    >
      <div className="container mx-auto px-4 py-10">
        {/* Breadcrumb */}
        <div className="flex items-center gap-2 text-[10px] uppercase tracking-[0.2em] text-muted-foreground mb-8">
          <Link to="/" className="hover:text-foreground transition-colors">
            {localized(locale, "Home", "الرئيسية")}
          </Link>
          <span>/</span>
          <span className="text-foreground">
            <InlineEditable
              sectionId={sectionId}
              settingKey="title"
              value={title}
            />
          </span>
        </div>

        {/* Title — shows the query when present */}
        <h1 className="lux-heading text-2xl md:text-3xl mb-10 text-foreground">
          {hasQuery ? (
            <>
              {localized(locale, "Results for", "نتائج البحث عن")}{" "}
              <span className="text-muted-foreground">“{query}”</span>
            </>
          ) : (
            <InlineEditable
              sectionId={sectionId}
              settingKey="title"
              value={title}
            />
          )}
        </h1>

        {/* Search input */}
        <form
          className="relative mb-6 max-w-md"
          onSubmit={(e) => e.preventDefault()}
          role="search"
        >
          <label htmlFor="lux-search-input" className="sr-only">
            {searchPlaceholder}
          </label>
          <Search
            size={16}
            aria-hidden="true"
            className="absolute end-3 top-1/2 -translate-y-1/2 text-muted-foreground"
          />
          <input
            id="lux-search-input"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={searchPlaceholder}
            className="w-full h-10 pe-9 ps-4 text-xs lux-input"
            data-testid="storefront-search-input"
          />
        </form>

        {/* Toolbar count (only with a query) */}
        {hasQuery && (
          <div className="flex items-center justify-between mb-6">
            <span className="text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
              {matches.length} {localized(locale, "products", "منتج")}
            </span>
          </div>
        )}

        {/* Results / empty states */}
        {hasMatches ? (
          <div
            className="grid gap-4 grid-cols-[var(--cols-mobile)] sm:grid-cols-[var(--cols-tablet)] md:grid-cols-[var(--cols-desktop)]"
            style={gridStyle}
            data-testid="storefront-search-grid"
          >
            {matches.map((product) => (
              <ProductCard
                key={product.id}
                product={product}
                locale={locale}
              />
            ))}
          </div>
        ) : (
          <div className="text-center py-20">
            <div className="w-12 h-px bg-border mx-auto mb-6" />
            <p className="text-sm mb-2 text-foreground">
              {hasQuery
                ? localized(locale, "No results", "لا توجد نتائج")
                : localized(
                    locale,
                    "Start typing to search",
                    "ابدأ الكتابة للبحث",
                  )}
            </p>
            <p className="text-xs text-muted-foreground">
              {hasQuery
                ? localized(
                    locale,
                    "Try a different keyword or browse the full collection",
                    "جرّب كلمة أخرى أو تصفّح كامل التشكيلة",
                  )
                : localized(
                    locale,
                    "Find products by name or keyword",
                    "ابحث عن المنتجات بالاسم أو الكلمة المفتاحية",
                  )}
            </p>
            <Link
              to="/products"
              className="inline-flex items-center gap-2 lux-btn-outline mt-8"
            >
              {localized(locale, "Browse all", "تصفّح الكل")}
            </Link>
          </div>
        )}
      </div>
    </section>
  );
}

/** Inline luxury-minimal product card (mirrors the products page card). */
function ProductCard({
  product,
  locale,
}: {
  product: Product;
  locale?: string;
}) {
  const price = product.variants?.[0]?.price ?? product.price ?? 0;
  const compareAt = product.compare_at_price;
  const hasDiscount = typeof compareAt === "number" && compareAt > price;
  const outOfStock = product.in_stock === false;
  const primary = asImageUrl(product.images?.[0]);
  const secondary = asImageUrl(product.images?.[1]);

  return (
    <Link
      to={`/product/${product.slug || product.id}`}
      className="lux-product-card group block"
      data-testid="storefront-product-card"
    >
      <div className="relative overflow-hidden bg-[hsl(var(--lux-gray))] aspect-[3/4]">
        {primary ? (
          <img
            src={primary}
            alt={product.name}
            className="lux-product-image absolute inset-0 w-full h-full object-cover group-hover:scale-[1.03] transition-transform duration-700 ease-out"
            loading="lazy"
          />
        ) : (
          <div className="absolute inset-0 lux-shimmer" />
        )}
        {secondary && (
          <img
            src={secondary}
            alt=""
            className="lux-product-image-secondary"
            loading="lazy"
          />
        )}

        {product.tags?.[0] && !outOfStock && (
          <span className="absolute top-3 start-3 text-[10px] uppercase tracking-[0.2em] px-2.5 py-1 bg-white/95 text-foreground">
            {product.tags[0]}
          </span>
        )}
        {hasDiscount && !outOfStock && (
          <span className="absolute top-3 start-3 text-[10px] uppercase tracking-[0.2em] px-2.5 py-1 bg-foreground text-background">
            {localized(locale, "Sale", "تخفيض")}
          </span>
        )}
        {outOfStock && (
          <div className="absolute inset-0 bg-white/65 flex items-center justify-center">
            <span className="text-[10px] uppercase tracking-[0.2em] text-foreground bg-white px-3 py-1.5 border border-border">
              {localized(locale, "Sold out", "نفذت الكمية")}
            </span>
          </div>
        )}
      </div>

      <div className="mt-3">
        <p className="text-sm text-foreground line-clamp-1 mb-1.5 group-hover:opacity-50 transition-opacity">
          {product.name}
        </p>
        <div className="flex items-baseline gap-2 mt-1">
          <span className="text-sm text-foreground">
            <Money amount={price} currency={product.currency} />
          </span>
          {hasDiscount && (
            <span className="text-[11px] text-muted-foreground line-through">
              <Money amount={compareAt} currency={product.currency} />
            </span>
          )}
        </div>
      </div>
    </Link>
  );
}
