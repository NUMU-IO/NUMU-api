"use client";
import { Link, Money, useProducts, useLocale, type Product } from "@numueg/theme-sdk";
import { ArrowLeft } from "lucide-react";
import { asNumber, asString, localized, type SectionRenderProps } from "./_shared";

/**
 * Editorial featured collection — faithful port of V2
 * themes/editorial/sections/featured-collection/EdFeaturedCollection.tsx.
 *
 * V2 resolved new/bestseller buckets from `useProducts()`; on V3 the SDK
 * exposes a single `products` catalog, so every collection_tag falls back to
 * the full catalog (mirrors V2's "fall back to all products if filtered is
 * empty"). Cards inlined from the V2 EdProductCard (`ed-card` shell, 3:4
 * image, name + price) with SDK <Money>.
 */
export default function EdFeaturedCollection({ instance }: SectionRenderProps) {
  const { products } = useProducts();
  const isLoading = false;
  const s = instance.settings ?? {};
  const locale = useLocale();
  const title = asString(s.title) || localized(locale, "New Arrivals", "وصل حديثاً");
  const viewAllLink = asString(s.view_all_link) || "/products";
  const viewAllText = asString(s.view_all_text) || localized(locale, "View all", "عرض الكل");
  const count = asNumber(s.product_count, 4);
  const cols = asNumber(s.columns, 4);

  const manualIds = Array.isArray(s.product_ids)
    ? (s.product_ids as unknown[]).filter((x): x is string => typeof x === "string" && x.length > 0)
    : [];

  // V3 catalog is a single list — every tag resolves to the full catalog.
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
    <section className="py-8">
      <div className="container mx-auto px-4">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-[11px] font-bold uppercase tracking-[0.2em] text-muted-foreground">
            {title}
          </h2>
          <Link
            to={viewAllLink}
            className="text-[11px] font-bold uppercase tracking-[0.15em] text-foreground flex items-center gap-1 hover:opacity-60 transition-opacity"
          >
            {viewAllText} <ArrowLeft size={12} />
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
              <EdProductCard key={product.id} product={product} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

/** Inline Editorial product card — mirrors V2 EdProductCard's markup/classes. */
function EdProductCard({ product }: { product: Product }) {
  const price = product.variants?.[0]?.price ?? product.price ?? 0;
  const primary = product.images?.[0]?.url;
  return (
    <Link
      to={`/product/${product.slug || product.id}`}
      className="ed-card group block"
      data-testid="storefront-product-card"
    >
      <div className="overflow-hidden aspect-[3/4] relative bg-muted">
        {primary ? (
          <img
            src={primary}
            alt={product.name}
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-700"
            loading="lazy"
          />
        ) : (
          <div className="absolute inset-0 bg-muted" />
        )}
      </div>
      <div className="p-3 pt-3">
        <h3 className="font-semibold text-sm text-foreground line-clamp-1 mb-1 hover:opacity-60 transition-opacity">
          {product.name}
        </h3>
        <span className="font-bold text-sm text-foreground">
          <Money amount={price} currency={product.currency} />
        </span>
      </div>
    </Link>
  );
}
