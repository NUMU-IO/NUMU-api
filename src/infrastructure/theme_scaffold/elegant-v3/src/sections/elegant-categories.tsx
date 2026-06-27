"use client";
import { Link, useCollections, useLocale } from "@numueg/theme-sdk";
import { motion } from "framer-motion";
import { asNumber, asString, localized, type SectionRenderProps } from "./_shared";

const ElegantCategories = ({ instance }: SectionRenderProps) => {
  const { collections, loading: isLoading } = useCollections();
  const s = instance.settings ?? {};
  const locale = useLocale();

  const title = asString(s.title) || localized(locale, "Shop by Category", "تسوق حسب الفئة");
  const colsDesktop = asNumber(s.columns_desktop, 4);
  const colsMobile = asNumber(s.columns_mobile, 2);
  const maxItems = asNumber(s.max_items, 0);

  const displayCategories =
    maxItems > 0 ? collections.slice(0, maxItems) : collections;

  // Hide section if no categories
  if (!isLoading && displayCategories.length === 0) return null;

  const cssVars = {
    "--cols-mobile": colsMobile,
    "--cols-desktop": colsDesktop,
  } as React.CSSProperties;

  return (
    <section className="py-12 md:py-16">
      <div className="container mx-auto px-4">
        {/* Section heading */}
        <div className="mb-8">
          <h2
            className="text-xl md:text-2xl font-semibold text-foreground"
            style={{ fontFamily: "var(--font-heading, serif)" }}
          >
            {title}
          </h2>
          <div className="w-10 h-px bg-primary/40 mt-3" />
        </div>

        {/* Loading skeleton */}
        {isLoading ? (
          <div
            className="grid gap-4 grid-cols-[repeat(var(--cols-mobile),minmax(0,1fr))] md:grid-cols-[repeat(var(--cols-desktop),minmax(0,1fr))]"
            style={cssVars}
          >
            {Array.from({ length: colsDesktop }).map((_, i) => (
              <div
                key={i}
                className="bg-muted/60 aspect-[4/5] animate-pulse rounded-sm"
              />
            ))}
          </div>
        ) : displayCategories.length === 0 ? (
          /* Empty state */
          <p className="text-sm text-muted-foreground text-center py-14">
            {localized(locale, "No categories yet", "لا توجد فئات بعد")}
          </p>
        ) : (
          /* Category grid */
          <div
            className="grid gap-4 grid-cols-[repeat(var(--cols-mobile),minmax(0,1fr))] md:grid-cols-[repeat(var(--cols-desktop),minmax(0,1fr))]"
            style={cssVars}
          >
            {displayCategories.map((cat, i) => (
              <motion.div
                key={cat.id}
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.08, duration: 0.5 }}
              >
                <Link
                  to={`/products?category=${cat.id}`}
                  className="group relative block overflow-hidden aspect-[4/5]"
                >
                  {/* Category image */}
                  {cat.image_url ? (
                    <img
                      src={cat.image_url}
                      alt={cat.name}
                      className="w-full h-full object-cover transition-transform duration-700 ease-out group-hover:scale-105"
                    />
                  ) : (
                    <div className="w-full h-full eg-shimmer" />
                  )}

                  {/* Warm overlay */}
                  <div className="absolute inset-0 bg-gradient-to-t from-[hsl(30_30%_10%/0.65)] via-[hsl(30_30%_10%/0.15)] to-transparent transition-opacity duration-500 group-hover:from-[hsl(30_30%_10%/0.75)]" />

                  {/* Category label */}
                  <div className="absolute bottom-0 left-0 right-0 p-4 md:p-5">
                    <span
                      className="text-white font-semibold text-base md:text-lg block"
                      style={{ fontFamily: "var(--font-heading, serif)" }}
                    >
                      {cat.name}
                    </span>
                    <span className="text-white/60 text-xs mt-0.5 block">
                      {cat.product_count} {localized(locale, "items", "منتج")}
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

export default ElegantCategories;
