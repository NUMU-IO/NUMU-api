"use client";
import { Link, Money, useProducts, useLocale, type Product } from "@numueg/theme-sdk";
import { ArrowLeft } from "lucide-react";
import { asString, asNumber, localized, type SectionRenderProps } from "./_shared";

const HEADING_SHADOW = "0 1px 0 hsl(35 30% 100% / 0.6)";

const SkeuFeaturedCollection = ({ instance }: SectionRenderProps) => {
  const { products, loading: isLoading } = useProducts();
  const s = instance.settings ?? {};
  const locale = useLocale();
  const title = asString(s.title) || localized(locale, "New arrivals ✨", "وصل حديثاً ✨");
  const subtitle = asString(s.subtitle);
  const viewAllLink = asString(s.view_all_link) || "/products";
  const count = asNumber(s.product_count, 4);
  const cols = asNumber(s.columns, 4);
  const viewAllText = asString(s.view_all_text) || localized(locale, "View all", "عرض الكل");

  // Optional manual product selection, else the SSR-prefetched catalog.
  const manualIds = Array.isArray(s.product_ids)
    ? (s.product_ids as unknown[]).filter((x): x is string => typeof x === "string" && x.length > 0)
    : [];
  const collectionProducts = manualIds.length > 0
    ? manualIds
        .map((id) => products.find((p) => p.id === id || p.slug === id))
        .filter((p): p is NonNullable<typeof p> => !!p)
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
            <h2
              className="text-xl font-bold"
              style={{ textShadow: HEADING_SHADOW }}
            >
              {title}
            </h2>
            {subtitle && (
              <p className="text-sm text-muted-foreground mt-0.5">
                {subtitle}
              </p>
            )}
          </div>
          <Link
            to={viewAllLink}
            className="text-sm font-bold flex items-center gap-1 skeu-chip px-4 py-2 rounded-lg text-primary"
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
              <SkeuProductCard key={product.id} product={product} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
};

/** Inline skeuomorphic product card — framed image + tactile info block. */
function SkeuProductCard({ product }: { product: Product }) {
  const locale = useLocale();
  const price = product.variants?.[0]?.price ?? product.price ?? 0;
  const compareAt = product.compare_at_price;
  const hasDiscount = typeof compareAt === "number" && compareAt > price;
  const outOfStock = product.in_stock === false;
  const primary = product.images?.[0]?.url;

  return (
    <Link
      to={`/product/${product.slug || product.id}`}
      className="vn-product-card group block"
      data-testid="storefront-product-card"
    >
      <div className="relative skeu-img-frame rounded-xl overflow-hidden aspect-[3/4]">
        {primary ? (
          <img
            src={primary}
            alt={product.name}
            className="vn-product-image w-full h-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="absolute inset-0 vn-shimmer" />
        )}
        {hasDiscount && !outOfStock && (
          <span className="absolute top-2 start-2 skeu-badge px-2.5 py-1 rounded-lg text-[10px] font-bold">
            {localized(locale, "Sale", "خصم")}
          </span>
        )}
        {outOfStock && (
          <div className="absolute inset-0 bg-black/40 flex items-center justify-center">
            <span className="skeu-chip text-foreground text-[11px] px-3 py-1.5 rounded-lg font-bold">
              {localized(locale, "Sold out", "نفذت الكمية")}
            </span>
          </div>
        )}
      </div>
      <div className="mt-3 px-1">
        <h3 className="text-sm font-bold text-foreground line-clamp-1">
          {product.name}
        </h3>
        <div className="flex items-baseline gap-2 mt-1">
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

export default SkeuFeaturedCollection;
