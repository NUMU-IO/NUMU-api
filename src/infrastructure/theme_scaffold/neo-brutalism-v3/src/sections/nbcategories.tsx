"use client";
import { Link, useCollections, useLocale } from "@numueg/theme-sdk";
import { motion } from "framer-motion";
import { asNumber, asString, localized, type SectionRenderProps } from "./_shared";

/**
 * Neo-brutalism category grid — faithful V2 port re-plumbed on the V3 SDK.
 *
 * V2 source: themes/neo-brutalism/sections/categories/NBCategories.tsx
 *
 * Data: V2 read categories off ProductsContext (with .image / .icon / .count).
 * The V3 SDK exposes the catalog's collections via `useCollections()`
 * ({ id, name, slug, image_url, product_count }). There's no per-collection
 * emoji icon on the SDK shape, so the icon glyph is dropped; everything else
 * (image tile, gradient overlay, name, product count) maps over faithfully.
 */
const NBCategories = ({ instance }: SectionRenderProps) => {
  const { collections, loading: isLoading } = useCollections();
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
    <section className="py-10">
      <div className="container mx-auto px-4">
        <h2 className="text-2xl font-black mb-6 inline-block bg-primary px-3 py-1 border-[3px] border-foreground shadow-[4px_4px_0_hsl(0_0%_10%)]">
          {title}
        </h2>

        {!isLoading && displayCategories.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-10">
            {localized(locale, "No categories yet", "لا توجد فئات بعد")}
          </p>
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
                  className="group block rounded-lg overflow-hidden nb-img-frame aspect-square relative"
                >
                  {cat.image_url ? (
                    <img
                      src={cat.image_url}
                      alt={cat.name}
                      className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                    />
                  ) : (
                    <div className="w-full h-full bg-secondary" />
                  )}
                  <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/20 to-transparent" />
                  <div className="absolute bottom-0 right-0 left-0 p-3">
                    <span className="text-white font-black text-sm">
                      {cat.name}
                    </span>
                    <span className="text-white/60 text-[10px] block font-bold">
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

export default NBCategories;
