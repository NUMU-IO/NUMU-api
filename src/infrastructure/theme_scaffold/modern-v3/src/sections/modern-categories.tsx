"use client";
import { Link, useCollections, useLocale } from "@numueg/theme-sdk";
import { motion } from "framer-motion";
import { localized, type SectionRenderProps } from "./_shared";

/**
 * Modern categories grid — faithful port of the V2 in-tree ModernCategories
 * (numu-egyptian-bazaar/src/themes/modern/sections/categories/ModernCategories.tsx).
 *
 * V2 read its data from `useProducts().categories` ({id,image,name,icon,count}).
 * The V3 SDK's nearest equivalent is `useCollections()` — Collection carries
 * `id` / `name` / `image_url` / `product_count`, so we map onto those (there's
 * no per-category emoji icon on the SDK Collection, so that decorative line is
 * dropped; everything else — grid CSS vars, overlay, hover scale, the AR copy —
 * is kept verbatim from V2).
 */

const containerVariants = {
  hidden: {},
  visible: {
    transition: {
      staggerChildren: 0.08,
    },
  },
};

const cardVariants = {
  hidden: { opacity: 0, y: 24 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.5, ease: [0.25, 0.1, 0.25, 1] as [number, number, number, number] },
  },
};

const ModernCategories = ({ instance }: SectionRenderProps) => {
  const { collections: categories, loading: isLoading } = useCollections();
  const s = instance.settings ?? {};
  const locale = useLocale();
  const title = s.title ?? localized(locale, "Shop by category", "تسوق حسب الفئة");
  const colsDesktop = Number(s.columns_desktop ?? 4);
  const colsMobile = Number(s.columns_mobile ?? 2);
  const maxItems = Number(s.max_items ?? 0);

  const displayCategories =
    maxItems > 0 ? categories.slice(0, maxItems) : categories;

  // Hide section if no categories
  if (!isLoading && displayCategories.length === 0) return null;

  const cssVars = {
    "--cols-mobile": colsMobile,
    "--cols-desktop": colsDesktop,
  } as React.CSSProperties;

  return (
    <section className="py-10 md:py-14">
      <div className="container mx-auto px-4">
        <h2 className="text-2xl md:text-3xl font-bold text-foreground mb-8">
          {title}
        </h2>

        {isLoading ? (
          <div
            className="grid gap-4 grid-cols-[repeat(var(--cols-mobile),minmax(0,1fr))] md:grid-cols-[repeat(var(--cols-desktop),minmax(0,1fr))]"
            style={cssVars}
          >
            {Array.from({ length: colsDesktop }).map((_, i) => (
              <div
                key={i}
                className="rounded-2xl bg-muted aspect-[4/5] animate-pulse"
              />
            ))}
          </div>
        ) : displayCategories.length === 0 ? (
          <div className="text-center py-16">
            <p className="text-muted-foreground text-sm">{localized(locale, "No categories yet", "لا توجد فئات بعد")}</p>
          </div>
        ) : (
          <motion.div
            className="grid gap-4 grid-cols-[repeat(var(--cols-mobile),minmax(0,1fr))] md:grid-cols-[repeat(var(--cols-desktop),minmax(0,1fr))]"
            style={cssVars}
            variants={containerVariants}
            initial="hidden"
            animate="visible"
          >
            {displayCategories.map((cat) => (
              <motion.div key={cat.id} variants={cardVariants}>
                <Link
                  to={`/products?category=${cat.slug || cat.id}`}
                  className="group relative block rounded-2xl overflow-hidden aspect-[4/5] shadow-sm hover:shadow-lg transition-shadow duration-300"
                >
                  {cat.image_url ? (
                    <img
                      src={cat.image_url}
                      alt={cat.name}
                      className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"
                    />
                  ) : (
                    <div className="w-full h-full store-gradient" />
                  )}
                  {/* Gradient overlay */}
                  <div className="absolute inset-0 bg-gradient-to-t from-black/65 via-black/20 to-transparent" />

                  {/* Category info */}
                  <div className="absolute bottom-0 left-0 right-0 p-4">
                    <span className="text-white font-bold text-base block">
                      {cat.name}
                    </span>
                    <span className="text-white/60 text-xs">
                      {cat.product_count} {localized(locale, "products", "منتج")}
                    </span>
                  </div>
                </Link>
              </motion.div>
            ))}
          </motion.div>
        )}
      </div>
    </section>
  );
};

export default ModernCategories;
