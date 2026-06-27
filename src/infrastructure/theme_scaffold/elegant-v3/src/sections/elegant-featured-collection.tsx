"use client";
import { Link, Money, useProducts, useLocale } from "@numueg/theme-sdk";
import { ArrowLeft } from "lucide-react";
import { asNumber, asString, localized, type SectionRenderProps } from "./_shared";

const ElegantFeaturedCollection = ({ instance }: SectionRenderProps) => {
  const { products } = useProducts();
  const isLoading = false;
  const s = instance.settings ?? {};
  const locale = useLocale();

  const title = asString(s.title) || localized(locale, "New Arrivals", "وصل حديثاً");
  const subtitle = asString(s.subtitle);
  const viewAllText = asString(s.view_all_text) || localized(locale, "View All", "عرض الكل");
  const viewAllLink = asString(s.view_all_link) || "/products";
  const count = asNumber(s.product_count, 4);
  const cols = asNumber(s.columns, 4);
  const manualIds = Array.isArray(s.product_ids)
    ? (s.product_ids as unknown[]).filter((x): x is string => typeof x === "string" && x.length > 0)
    : [];

  // Resolve collection — fall back to all products if filtered is empty
  const tagged = products;
  const autoProducts = tagged.length > 0 ? tagged : products;

  const collectionProducts = manualIds.length > 0
    ? manualIds
        .map((id) => products.find((p) => p.id === id || p.slug === id))
        .filter((p): p is NonNullable<typeof p> => !!p)
    : autoProducts;

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
    <section className="py-12 md:py-16">
      <div className="container mx-auto px-4">
        {/* Heading area */}
        <div className="flex items-end justify-between mb-8">
          <div>
            <h2
              className="text-xl md:text-2xl font-semibold text-foreground"
              style={{ fontFamily: "var(--font-heading, serif)" }}
            >
              {title}
            </h2>
            {subtitle && (
              <p className="text-sm text-muted-foreground mt-1.5">
                {subtitle}
              </p>
            )}
            {/* Decorative underline */}
            <div className="w-10 h-px bg-primary/40 mt-3" />
          </div>

          <Link
            to={viewAllLink}
            className="text-xs font-semibold tracking-wide text-primary flex items-center gap-1 hover:opacity-70 transition-opacity"
          >
            {viewAllText} <ArrowLeft size={13} />
          </Link>
        </div>

        {/* Loading skeleton */}
        {isLoading ? (
          <div className={gridClassName} style={cssVars}>
            {Array.from({ length: cols }).map((_, i) => (
              <div
                key={i}
                className="bg-muted/60 aspect-[3/4] animate-pulse rounded-sm"
              />
            ))}
          </div>
        ) : displayProducts.length === 0 ? (
          /* Empty state */
          <p className="text-sm text-muted-foreground text-center py-14">
            {localized(locale, "No products yet", "لا توجد منتجات بعد")}
          </p>
        ) : (
          /* Product grid */
          <div className={gridClassName} style={cssVars}>
            {displayProducts.map((product) => (
              <Link
                key={product.id}
                to={`/product/${product.slug || product.id}`}
                className="eg-product-card group block"
                data-testid="storefront-product-card"
              >
                {/* Image — classic soft frame */}
                <div className="relative aspect-[3/4] overflow-hidden rounded-lg bg-[hsl(var(--accent))] mb-3">
                  {product.images?.[0]?.url ? (
                    <img
                      src={product.images[0].url}
                      alt={product.name}
                      className="eg-product-image w-full h-full object-cover"
                      loading="lazy"
                    />
                  ) : (
                    <div className="absolute inset-0 eg-shimmer" />
                  )}
                </div>

                {/* Info — classic, minimal */}
                <div>
                  <h3 className="text-sm font-medium line-clamp-1 mb-0.5 text-foreground group-hover:text-[hsl(var(--primary))] transition-colors">
                    {product.name}
                  </h3>
                  <div className="flex items-baseline gap-2">
                    <span className="text-sm font-semibold text-foreground">
                      <Money amount={product.variants?.[0]?.price ?? product.price ?? 0} currency={product.currency} />
                    </span>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </section>
  );
};

export default ElegantFeaturedCollection;
