"use client";
import { Link, Money, useProducts, useLocale } from "@numueg/theme-sdk";
import { ArrowLeft } from "lucide-react";
import { localized, type SectionRenderProps } from "./_shared";

/**
 * Modern featured collection — faithful port of the V2 in-tree
 * ModernFeaturedCollection
 * (numu-egyptian-bazaar/src/themes/modern/sections/featured-collection/…).
 *
 * V2 read `useProducts().{newArrivals,bestSellers,products,isLoading}` and
 * rendered the shared `<ProductCard>`. The V3 SDK's `useProducts()` returns a
 * single `{products}` list (no per-tag buckets), so — per the V3 port recipe —
 * the tag-based bucket selection collapses to `products`, `isLoading` is false,
 * and the product card is inlined (SDK `Money`, variant-first price). The
 * section header / grid / "view all" markup keeps V2's classNames verbatim.
 */
const ModernFeaturedCollection = ({ instance }: SectionRenderProps) => {
  const { products } = useProducts();
  const isLoading = false;
  const s = instance.settings ?? {};
  const locale = useLocale();
  const title = s.title ?? localized(locale, "New arrivals", "وصل حديثاً");
  const subtitle = s.subtitle ?? localized(locale, "The latest products we've added", "أحدث المنتجات اللي ضفناها");
  const viewAllText = s.view_all_text ?? localized(locale, "View all", "عرض الكل");
  const viewAllLink = s.view_all_link ?? "/products";
  const count = Number(s.product_count ?? 4);
  const cols = Number(s.columns ?? 4);

  // V3: a single product list — manual picks (product_ids) win, else the
  // catalog order; the V2 tag buckets aren't exposed on the SDK.
  const manualIds = Array.isArray(s.product_ids)
    ? (s.product_ids as unknown[]).filter((x): x is string => typeof x === "string" && x.length > 0)
    : [];
  const collectionProducts =
    manualIds.length > 0
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
    "grid gap-4 md:gap-6 grid-cols-[repeat(var(--cols-mobile),minmax(0,1fr))] md:grid-cols-[repeat(var(--cols-desktop),minmax(0,1fr))]";

  return (
    <section className="py-10 md:py-14">
      <div className="container mx-auto px-4">
        {/* Header area */}
        <div className="flex items-end justify-between mb-8 pb-4 border-b border-border">
          <div>
            <h2 className="text-2xl md:text-3xl font-bold text-foreground">
              {title}
            </h2>
            {subtitle && (
              <p className="text-sm text-muted-foreground mt-1.5">
                {subtitle}
              </p>
            )}
          </div>
          <Link
            to={viewAllLink}
            className="inline-flex items-center gap-1.5 px-5 py-2.5 rounded-xl bg-primary/10 text-primary text-sm font-bold hover:bg-primary/20 transition-colors shrink-0"
          >
            {viewAllText}
            <ArrowLeft size={14} />
          </Link>
        </div>

        {/* Product grid */}
        {isLoading ? (
          <div className={gridClassName} style={cssVars}>
            {Array.from({ length: cols }).map((_, i) => (
              <div key={i} className="space-y-3">
                <div className="rounded-2xl bg-muted aspect-[3/4] animate-pulse" />
                <div className="h-4 bg-muted rounded-lg w-3/4 animate-pulse" />
                <div className="h-3 bg-muted rounded-lg w-1/2 animate-pulse" />
              </div>
            ))}
          </div>
        ) : displayProducts.length === 0 ? (
          <div className="text-center py-16">
            <p className="text-muted-foreground text-sm">
              {localized(locale, "No products yet", "لا توجد منتجات بعد")}
            </p>
          </div>
        ) : (
          <div className={gridClassName} style={cssVars}>
            {displayProducts.map((product) => {
              const price = product.variants?.[0]?.price ?? product.price ?? 0;
              const compareAt = product.compare_at_price;
              const hasDiscount = typeof compareAt === "number" && compareAt > price;
              return (
                <Link
                  key={product.id}
                  to={`/product/${product.slug || product.id}`}
                  className="vn-product-card group block"
                  data-testid="storefront-product-card"
                >
                  {/* Image */}
                  <div className="relative aspect-[3/4] overflow-hidden rounded-2xl bg-muted shadow-sm group-hover:shadow-lg transition-shadow duration-300 mb-3">
                    {product.images?.[0]?.url ? (
                      <img
                        src={product.images[0].url}
                        alt={product.name}
                        className="vn-product-image w-full h-full object-cover"
                        loading="lazy"
                      />
                    ) : (
                      <div className="absolute inset-0 vn-shimmer" />
                    )}
                  </div>

                  {/* Info */}
                  <div className="space-y-1">
                    <h3 className="text-sm font-bold text-foreground line-clamp-1 group-hover:text-primary transition-colors">
                      {product.name}
                    </h3>
                    <div className="flex items-baseline gap-2 pt-0.5">
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
            })}
          </div>
        )}
      </div>
    </section>
  );
};

export default ModernFeaturedCollection;
