"use client";
import { Link, useCollections, useLocale } from "@numueg/theme-sdk";
import { motion } from "framer-motion";
import { asNumber, asString, localized, type SectionRenderProps } from "./_shared";

/**
 * Tech Wave categories — faithful port of the V2 in-tree
 * numu-egyptian-bazaar/src/themes/tech-wave/sections/categories/TechWaveCategories.tsx
 * (neon-framed category mosaic with dark gradient overlay), re-plumbed on
 * the V3 SDK. V2 read categories from the bazaar ProductsContext; here we
 * read them from the SDK's `useCollections()` (SSR-prefetched on the home
 * route). The SDK Collection carries `image_url` + `product_count` instead
 * of V2's `image`/`count`/`icon`, so we map onto those.
 */
const TechWaveCategories = ({ instance }: SectionRenderProps) => {
  const { collections, loading } = useCollections();
  const isLoading = loading;
  const s = instance.settings ?? {};
  const locale = useLocale();
  const title = asString(s.title) || localized(locale, "Shop by category", "تسوق حسب الفئة");
  const colsDesktop = asNumber(s.columns_desktop, 5);
  const colsMobile = asNumber(s.columns_mobile, 3);
  const maxItems = asNumber(s.max_items, 0);
  const displayCategories = maxItems > 0 ? collections.slice(0, maxItems) : collections;

  // Hide section if no categories
  if (!isLoading && displayCategories.length === 0) return null;

  const cssVars = {
    "--cols-mobile": colsMobile,
    "--cols-desktop": colsDesktop,
  } as React.CSSProperties;

  const gridClassName =
    "grid gap-3 grid-cols-[repeat(var(--cols-mobile),minmax(0,1fr))] md:grid-cols-[repeat(var(--cols-desktop),minmax(0,1fr))]";

  return (
    <section className="py-8">
      <div className="container mx-auto px-4">
        <h2 className="text-xl font-bold mb-5 text-[hsl(var(--foreground))]">
          {title}
        </h2>

        {isLoading ? (
          <div className={gridClassName} style={cssVars}>
            {Array.from({ length: colsDesktop }).map((_, i) => (
              <div key={i} className="rounded-xl bg-muted aspect-square animate-pulse" />
            ))}
          </div>
        ) : (
          <div className={gridClassName} style={cssVars}>
            {displayCategories.map((cat, i) => (
              <motion.div
                key={cat.id}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.08 }}
              >
                <Link
                  to={`/products?category=${cat.slug || cat.id}`}
                  className="group relative block rounded-xl overflow-hidden aspect-square tw-img-frame"
                >
                  {cat.image_url ? (
                    <img
                      src={cat.image_url}
                      alt={cat.name}
                      className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"
                    />
                  ) : (
                    <div className="absolute inset-0 vn-shimmer" />
                  )}
                  <div className="absolute inset-0 bg-gradient-to-t from-[hsl(220,40%,5%/0.85)] via-[hsl(220,40%,5%/0.3)] to-transparent" />
                  <div className="absolute bottom-0 right-0 left-0 p-2.5">
                    <span className="text-white font-bold text-sm">
                      {cat.name}
                    </span>
                    <span className="text-white/60 text-[10px] block">
                      {cat.product_count} {localized(locale, "products", "منتج")}
                    </span>
                  </div>
                </Link>
              </motion.div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
};

export default TechWaveCategories;
