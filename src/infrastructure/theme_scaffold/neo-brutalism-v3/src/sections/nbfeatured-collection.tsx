"use client";
import { Link, Money, useProducts, useLocale, type Product } from "@numueg/theme-sdk";
import { ArrowLeft, Star } from "lucide-react";
import { motion } from "framer-motion";
import { asNumber, asString, asArray, localized, type SectionRenderProps } from "./_shared";

/**
 * Neo-brutalism featured collection — faithful V2 port re-plumbed on the V3 SDK.
 *
 * V2 source: themes/neo-brutalism/sections/featured-collection/NBFeaturedCollection.tsx
 * + components/store/neo-brutalism/NBProductCard.tsx (inline card markup below).
 *
 * Data: `useProducts()` returns the SSR-prefetched catalog. V2 split products
 * into newArrivals / bestSellers; on the V3 SDK there's only the flat catalog,
 * so (matching the vionne port) every tag falls back to the full product list
 * sliced to `product_count`. Manual product picks (`product_ids`) win when set.
 */
const NBFeaturedCollection = ({ instance }: SectionRenderProps) => {
  const { products } = useProducts();
  const isLoading = false;
  const s = instance.settings ?? {};
  const locale = useLocale();
  const title = asString(s.title) || localized(locale, "New arrivals ✨", "وصل حديثاً ✨");
  const subtitle = asString(s.subtitle, "");
  const tag = asString(s.collection_tag, "new");
  const viewAllLink = asString(s.view_all_link, "/products");
  const count = asNumber(s.product_count, 4);
  const cols = asNumber(s.columns, 4);
  const viewAllText = asString(s.view_all_text) || localized(locale, "View all", "عرض الكل");

  const manualIds = asArray(s.product_ids).filter(
    (x): x is string => typeof x === "string" && x.length > 0,
  );

  // V3 SDK has no tag-segmented lists; fall back to the full catalog.
  const autoProducts = products;
  const collectionProducts = manualIds.length > 0
    ? manualIds
        .map((id) => products.find((p) => p.id === id || p.slug === id))
        .filter((p): p is Product => Boolean(p))
    : autoProducts;

  const displayProducts = collectionProducts.slice(0, Math.max(1, count));

  // Hide entire section if no products
  if (!isLoading && displayProducts.length === 0) return null;

  // Use nb-section-alt background for best sellers
  const isAlt = tag === "bestseller";

  const cssVars = {
    "--cols-mobile": 2,
    "--cols-desktop": cols,
  } as React.CSSProperties;

  const gridClassName =
    "grid gap-4 grid-cols-[repeat(var(--cols-mobile),minmax(0,1fr))] md:grid-cols-[repeat(var(--cols-desktop),minmax(0,1fr))]";

  return (
    <section className={`py-8 ${isAlt ? "nb-section-alt" : ""}`}>
      <div className="container mx-auto px-4">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-2xl font-black">{title}</h2>
            {subtitle && (
              <p className="text-sm text-muted-foreground mt-0.5 font-medium">
                {subtitle}
              </p>
            )}
          </div>
          <Link
            to={viewAllLink}
            className="text-sm font-black flex items-center gap-1 nb-chip px-4 py-2 rounded-lg"
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
              <NBProductCard key={product.id} product={product} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
};

/** Inline neo-brutalism product card — mirrors NBProductCard's markup/classes. */
export function NBProductCard({ product }: { product: Product }) {
  const locale = useLocale();
  const price = product.variants?.[0]?.price ?? product.price ?? 0;
  const compareAt = product.compare_at_price;
  const hasDiscount = typeof compareAt === "number" && compareAt > price;
  const discountPercent = hasDiscount
    ? Math.round(((compareAt - price) / compareAt) * 100)
    : 0;
  const isNew = (product.tags ?? []).some((t) => t.toLowerCase() === "new");
  const primary = product.images?.[0]?.url;

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
      className="nb-card rounded-lg overflow-hidden group"
      data-testid="storefront-product-card"
    >
      <Link to={`/product/${product.slug || product.id}`} className="block">
        {/* Image */}
        <div className="relative aspect-[3/4] overflow-hidden bg-muted">
          {primary ? (
            <img
              src={primary}
              alt={product.name}
              className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
              loading="lazy"
            />
          ) : (
            <div className="absolute inset-0 vn-shimmer" />
          )}
          {/* Badges */}
          <div className="absolute top-2 right-2 flex flex-col gap-1">
            {hasDiscount && (
              <span className="nb-badge-pink px-2 py-0.5 text-[10px] rounded">
                {localized(locale, `${discountPercent}% off`, `خصم ${discountPercent}%`)}
              </span>
            )}
            {isNew && (
              <span className="nb-badge px-2 py-0.5 text-[10px] rounded">
                {localized(locale, "New", "جديد")}
              </span>
            )}
          </div>
        </div>
      </Link>

      {/* Info */}
      <div className="p-3 border-t-[3px] border-foreground">
        <Link to={`/product/${product.slug || product.id}`}>
          <h3 className="font-black text-sm line-clamp-2 mb-1 hover:text-primary transition-colors">
            {product.name}
          </h3>
        </Link>

        <div className="flex items-center justify-between">
          <div className="flex items-baseline gap-2">
            <span className="text-lg font-black">
              <Money amount={price} currency={product.currency} />
            </span>
            {hasDiscount && (
              <span className="text-xs text-muted-foreground line-through">
                <Money amount={compareAt} currency={product.currency} />
              </span>
            )}
          </div>
          {product.in_stock === false && (
            <span className="text-[10px] font-black text-destructive uppercase">
              {localized(locale, "Sold out", "نفذ")}
            </span>
          )}
        </div>
      </div>
    </motion.div>
  );
}

export default NBFeaturedCollection;
