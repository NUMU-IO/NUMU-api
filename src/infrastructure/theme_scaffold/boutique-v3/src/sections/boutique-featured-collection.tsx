"use client";
import { Link, Money, useProducts, useLocale } from "@numueg/theme-sdk";
import { ArrowLeft } from "lucide-react";
import { asNumber, asString, localized, type SectionRenderProps } from "./_shared";

const BoutiqueFeaturedCollection = ({ instance }: SectionRenderProps) => {
  const { products } = useProducts();
  const isLoading = false;
  const s = instance.settings ?? {};
  const locale = useLocale();

  const title = asString(s.title) || localized(locale, "New Arrivals", "وصل حديثاً");
  const viewAllLink = asString(s.view_all_link) || "/products";
  const viewAllText = asString(s.view_all_text) || localized(locale, "View All", "عرض الكل");
  const productCount = asNumber(s.product_count, 4);
  const columns = asNumber(s.columns, 4);

  // Resolve collection — tag-filtered lists aren't exposed on the V3 SDK, so
  // fall back to the full product list (matches V2's empty-tag fallback).
  const tagged = products;
  const collectionProducts = tagged.length > 0 ? tagged : products;

  const displayProducts = collectionProducts.slice(0, productCount);

  // Hide entire section if no products
  if (!isLoading && displayProducts.length === 0) return null;

  const cssVars = {
    "--cols-mobile": 2,
    "--cols-desktop": columns,
  } as React.CSSProperties;

  const gridClassName =
    "grid gap-5 grid-cols-[repeat(var(--cols-mobile),minmax(0,1fr))] md:grid-cols-[repeat(var(--cols-desktop),minmax(0,1fr))]";

  return (
    <section className="py-14">
      <div className="container mx-auto px-4">
        {/* Section header with decorative divider */}
        <div className="text-center mb-10">
          <h2 className="text-2xl md:text-3xl font-bold text-foreground mb-3">
            {title}
          </h2>
          <div className="flex items-center justify-center gap-3">
            <span className="block w-12 h-px bg-primary/30" />
            <span className="block w-1.5 h-1.5 rounded-full bg-primary/50" />
            <span className="block w-12 h-px bg-primary/30" />
          </div>
        </div>

        {isLoading ? (
          <div className={gridClassName} style={cssVars}>
            {Array.from({ length: columns }).map((_, i) => (
              <div
                key={i}
                className="rounded-2xl bg-muted aspect-[3/4] animate-pulse"
              />
            ))}
          </div>
        ) : (
          <>
            <div className={gridClassName} style={cssVars}>
              {displayProducts.map((product) => (
                <div
                  key={product.id}
                  className="group transition-all duration-300 hover:scale-[1.02] hover:shadow-lg rounded-2xl"
                >
                  <Link
                    to={`/product/${product.slug || product.id}`}
                    className="block rounded-2xl overflow-hidden bg-card border border-border/50"
                    data-testid="storefront-product-card"
                  >
                    <div className="relative aspect-[3/4] overflow-hidden bg-accent/30">
                      {product.images?.[0]?.url && (
                        <img
                          src={product.images[0].url}
                          alt={product.name}
                          className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105"
                          loading="lazy"
                        />
                      )}
                    </div>
                    <div className="p-3 text-center">
                      <h3 className="font-semibold text-sm text-foreground line-clamp-1 group-hover:text-primary transition-colors">
                        {product.name}
                      </h3>
                      <p className="text-sm font-bold text-primary mt-1.5">
                        <Money amount={product.variants?.[0]?.price ?? product.price ?? 0} currency={product.currency} />
                      </p>
                    </div>
                  </Link>
                </div>
              ))}
            </div>

            {/* View all link */}
            <div className="text-center mt-8">
              <Link
                to={viewAllLink}
                className="inline-flex items-center gap-2 text-sm font-semibold text-primary hover:text-primary/80 transition-colors border-b border-primary/30 hover:border-primary/60 pb-0.5"
              >
                {viewAllText} <ArrowLeft size={14} />
              </Link>
            </div>
          </>
        )}
      </div>
    </section>
  );
};

export default BoutiqueFeaturedCollection;
