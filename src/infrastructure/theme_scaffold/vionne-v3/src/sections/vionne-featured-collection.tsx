"use client";
import { Link, Money, useLocale, useProducts, useResolvedSettings } from "@numueg/theme-sdk";
import { ArrowRight } from "lucide-react";
import { asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

const VionneFeaturedCollection = ({ instance, sectionId }: SectionRenderProps) => {
  const { products } = useProducts();
  const locale = useLocale();
  const isLoading = false;
  const s = useResolvedSettings(instance);
  const title = asString(s.title) || localized(locale, "New Arrivals", "وصل حديثًا");
  const subtitle = asString(s.subtitle);
  const tag = asString(s.collection_tag) || "new";
  const viewAllText = asString(s.view_all_text) || localized(locale, "View all", "عرض الكل");
  const viewAllLink = asString(s.view_all_link) || "/products";
  const count = Number(s.product_count ?? 8);
  const cols = Number(s.columns ?? 4);
  const manualIds = Array.isArray(s.product_ids)
    ? (s.product_ids as unknown[]).filter((x): x is string => typeof x === "string" && x.length > 0)
    : [];

  const tagged = products;
  const autoProducts = tagged.length > 0 ? tagged : products;

  const collectionProducts = manualIds.length > 0
    ? manualIds
        .map((id) => products.find((p) => p.id === id || p.slug === id))
        .filter((p): p is NonNullable<typeof p> => !!p)
    : autoProducts;

  const displayProducts = collectionProducts.slice(0, count);

  if (!isLoading && displayProducts.length === 0) return null;

  const cssVars = {
    "--cols-mobile": 2,
    "--cols-tablet": Math.min(3, cols),
    "--cols-desktop": cols,
  } as React.CSSProperties;

  const gridClassName =
    "grid gap-4 md:gap-5 grid-cols-[repeat(var(--cols-mobile),minmax(0,1fr))] sm:grid-cols-[repeat(var(--cols-tablet),minmax(0,1fr))] md:grid-cols-[repeat(var(--cols-desktop),minmax(0,1fr))]";

  return (
    <section className="py-10 md:py-14 bg-background">
      <div className="container mx-auto px-4">
        <div className="flex items-end justify-between mb-6 md:mb-8 gap-4">
          <div>
            {subtitle && (
              <span className="vn-eyebrow block mb-1.5">
                <InlineEditable sectionId={sectionId} settingKey="subtitle" value={subtitle} />
              </span>
            )}
            <h2 className="vn-heading text-2xl md:text-3xl">
              <InlineEditable sectionId={sectionId} settingKey="title" value={title} />
            </h2>
          </div>
          <Link
            to={viewAllLink}
            className="vn-label inline-flex items-center gap-1.5 text-[var(--vn-ink)] hover:opacity-70 transition-opacity shrink-0 pb-2"
          >
            <InlineEditable sectionId={sectionId} settingKey="view_all_text" value={viewAllText} />
            <ArrowRight size={14} />
          </Link>
        </div>

        {isLoading ? (
          <div className={gridClassName} style={cssVars}>
            {Array.from({ length: cols }).map((_, i) => (
              <div key={i} className="aspect-[3/4] vn-shimmer rounded" />
            ))}
          </div>
        ) : (
          <div className={gridClassName} style={cssVars}>
            {displayProducts.map((product) => (
              <Link
                key={product.id}
                to={`/product/${product.slug || product.id}`}
                className="vn-product-card group block"
                data-testid="storefront-product-card"
              >
                {/* Image */}
                <div className="relative aspect-[3/4] overflow-hidden bg-muted/30 mb-3 group-hover:scale-[1.02] transition-transform duration-500">
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
                  <h3 className="text-[13px] font-medium text-foreground/90 line-clamp-1 group-hover:text-foreground transition-colors">
                    {product.name}
                  </h3>

                  <div className="flex items-baseline gap-2 pt-0.5">
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

export default VionneFeaturedCollection;
