"use client";
import { Link, useCollections, useLocale } from "@numueg/theme-sdk";
import { motion } from "framer-motion";
import { asString, asNumber, localized, type SectionRenderProps } from "./_shared";

const HEADING_SHADOW = "0 1px 0 hsl(35 30% 100% / 0.6)";
const LABEL_SHADOW = "0 1px 3px rgba(0,0,0,0.6)";

const SkeuCategories = ({ instance }: SectionRenderProps) => {
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
    <section className="py-8">
      <div className="container mx-auto px-4">
        <h2
          className="text-xl font-bold mb-5"
          style={{ textShadow: HEADING_SHADOW }}
        >
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
                  className="group relative block rounded-xl overflow-hidden aspect-square skeu-img-frame"
                >
                  {cat.image_url ? (
                    <img
                      src={cat.image_url}
                      alt={cat.name}
                      className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"
                      loading="lazy"
                    />
                  ) : (
                    <div className="w-full h-full vn-shimmer" />
                  )}
                  <div
                    className="absolute inset-0 bg-gradient-to-t from-black/70 via-black/20 to-transparent"
                    style={{ margin: "4px" }}
                  />
                  <div className="absolute bottom-1 right-1 left-1 p-2.5">
                    <span
                      className="text-white font-bold text-sm"
                      style={{ textShadow: LABEL_SHADOW }}
                    >
                      {cat.name}
                    </span>
                    {typeof cat.product_count === "number" && cat.product_count > 0 && (
                      <span className="text-white/70 text-[10px] block">
                        {cat.product_count} {localized(locale, "products", "منتج")}
                      </span>
                    )}
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

export default SkeuCategories;
