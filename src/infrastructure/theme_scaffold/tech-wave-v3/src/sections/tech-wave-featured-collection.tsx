"use client";
import { Link, Money, useProducts, useLocale, type Product } from "@numueg/theme-sdk";
import { ArrowLeft } from "lucide-react";
import { asNumber, asString, localized, type SectionRenderProps } from "./_shared";

/**
 * Tech Wave featured collection — faithful port of the V2 in-tree
 * numu-egyptian-bazaar/src/themes/tech-wave/sections/featured-collection/TechWaveFeaturedCollection.tsx
 * (section header + neon "view all" chip + responsive product grid),
 * re-plumbed on the V3 SDK.
 *
 * V2 read tagged buckets (newArrivals/bestSellers) from the bazaar
 * ProductsContext. The V3 SDK `useProducts()` only exposes the flat
 * SSR-prefetched catalog (no tag buckets), so — like the Vionne port — we
 * use the full catalog and slice to `product_count`. The V2 `<ProductCard>`
 * shared component isn't available in V3, so the card markup is inlined with
 * the Tech Wave `tw-card` / neon look.
 */
const TechWaveFeaturedCollection = ({ instance }: SectionRenderProps) => {
  const { products, loading } = useProducts();
  const isLoading = loading;
  const s = instance.settings ?? {};
  const locale = useLocale();
  const title = asString(s.title) || localized(locale, "New arrivals ✨", "وصل حديثاً ✨");
  const subtitle = asString(s.subtitle);
  const viewAllLink = asString(s.view_all_link) || "/products";
  const viewAllText = asString(s.view_all_text) || localized(locale, "View all", "عرض الكل");
  const count = asNumber(s.product_count, 4);
  const cols = asNumber(s.columns, 4);

  const manualIds = Array.isArray(s.product_ids)
    ? (s.product_ids as unknown[]).filter((x): x is string => typeof x === "string" && x.length > 0)
    : [];

  const collectionProducts = manualIds.length > 0
    ? manualIds
        .map((id) => products.find((p) => p.id === id || p.slug === id))
        .filter((p): p is Product => !!p)
    : products;

  const displayProducts = collectionProducts.slice(0, count);

  // Hide entire section if no products
  if (!isLoading && displayProducts.length === 0) return null;

  const cssVars = {
    "--cols-mobile": 2,
    "--cols-desktop": cols,
  } as React.CSSProperties;

  const gridClassName =
    "grid gap-3 md:gap-4 grid-cols-[repeat(var(--cols-mobile),minmax(0,1fr))] md:grid-cols-[repeat(var(--cols-desktop),minmax(0,1fr))]";

  return (
    <section className="py-6">
      <div className="container mx-auto px-4">
        <div className="flex items-center justify-between mb-5">
          <div>
            <h2 className="text-xl font-bold text-[hsl(var(--foreground))]">
              {title}
            </h2>
            {subtitle && (
              <p className="text-sm text-[hsl(var(--muted-foreground))] mt-0.5">
                {subtitle}
              </p>
            )}
          </div>
          <Link
            to={viewAllLink}
            className="text-sm font-bold flex items-center gap-1 tw-chip px-4 py-2 rounded-lg text-[hsl(var(--primary))]"
          >
            {viewAllText} <ArrowLeft size={14} />
          </Link>
        </div>

        {isLoading ? (
          <div className={gridClassName} style={cssVars}>
            {Array.from({ length: cols }).map((_, i) => (
              <div
                key={i}
                className="rounded-2xl bg-muted aspect-[3/4] animate-pulse"
              />
            ))}
          </div>
        ) : (
          <div className={gridClassName} style={cssVars}>
            {displayProducts.map((product) => (
              <TechWaveProductCard key={product.id} product={product} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
};

/** Inline Tech Wave product card — mirrors the V2 TechWaveProductCard look
 *  (neon-bordered glass card, sale badge, hover glow). */
function TechWaveProductCard({ product }: { product: Product }) {
  const locale = useLocale();
  const price = product.variants?.[0]?.price ?? product.price ?? 0;
  const compareAt = product.compare_at_price;
  const hasDiscount = typeof compareAt === "number" && compareAt > price;
  const outOfStock = product.in_stock === false;
  const primary = product.images?.[0]?.url;

  return (
    <Link
      to={`/product/${product.slug || product.id}`}
      className="tw-card group block rounded-2xl overflow-hidden"
      data-testid="storefront-product-card"
    >
      <div className="relative aspect-[3/4] overflow-hidden">
        {primary ? (
          <img
            src={primary}
            alt={product.name}
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"
            loading="lazy"
          />
        ) : (
          <div className="absolute inset-0 vn-shimmer" />
        )}
        {hasDiscount && !outOfStock && (
          <span className="absolute top-2.5 start-2.5 tw-badge-accent px-2.5 py-1 rounded-lg text-[10px] font-bold">
            {localized(locale, "Sale", "خصم")}
          </span>
        )}
        {outOfStock && (
          <div className="absolute inset-0 bg-[hsl(220,40%,5%/0.7)] flex items-center justify-center">
            <span className="tw-chip text-[hsl(var(--foreground))] text-[11px] px-3 py-1.5 rounded-full">
              {localized(locale, "Sold out", "نفذت الكمية")}
            </span>
          </div>
        )}
      </div>
      <div className="p-3">
        <h3 className="text-sm font-bold text-[hsl(var(--foreground))] line-clamp-1">
          {product.name}
        </h3>
        <div className="flex items-baseline gap-2 mt-1">
          <span className="text-sm font-bold tw-neon-text">
            <Money amount={price} currency={product.currency} />
          </span>
          {hasDiscount && (
            <span className="text-xs text-[hsl(var(--muted-foreground))] line-through">
              <Money amount={compareAt} currency={product.currency} />
            </span>
          )}
        </div>
      </div>
    </Link>
  );
}

export default TechWaveFeaturedCollection;
