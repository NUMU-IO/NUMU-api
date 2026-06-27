"use client";
import { Link, useCollections, useLocale } from "@numueg/theme-sdk";
import { motion } from "framer-motion";
import { asNumber, asString, localized, type SectionRenderProps } from "./_shared";

const BoutiqueCategories = ({ instance }: SectionRenderProps) => {
  const { collections } = useCollections();
  const isLoading = false;
  const s = instance.settings ?? {};
  const locale = useLocale();

  const title = asString(s.title) || localized(locale, "Shop by Category", "تسوقي حسب الفئة");
  const maxItems = asNumber(s.max_items, 0);

  const displayCategories =
    maxItems > 0 ? collections.slice(0, maxItems) : collections;

  // Hide section if no categories
  if (!isLoading && displayCategories.length === 0) return null;

  return (
    <section className="py-14">
      <div className="container mx-auto px-4">
        {/* Section header */}
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
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-5">
            {Array.from({ length: 4 }).map((_, i) => (
              <div
                key={i}
                className="rounded-2xl bg-muted aspect-square animate-pulse"
              />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-5">
            {displayCategories.map((cat, i) => (
              <motion.div
                key={cat.id}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.08, duration: 0.5 }}
              >
                <Link
                  to={`/products?category=${cat.slug ?? cat.id}`}
                  className="group block rounded-2xl overflow-hidden bg-card border border-border/50 shadow-sm transition-all duration-300 hover:-translate-y-1 hover:shadow-md hover:border-primary/20"
                >
                  {/* Category image */}
                  <div className="aspect-square overflow-hidden bg-accent/30">
                    {cat.image_url && (
                      <img
                        src={cat.image_url}
                        alt={cat.name}
                        className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105"
                      />
                    )}
                  </div>

                  {/* Category name */}
                  <div className="p-4 text-center">
                    <h3 className="font-semibold text-sm text-foreground group-hover:text-primary transition-colors">
                      {cat.name}
                    </h3>
                    <p className="text-xs text-muted-foreground mt-1">
                      {cat.product_count} {localized(locale, "items", "منتج")}
                    </p>
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

export default BoutiqueCategories;
