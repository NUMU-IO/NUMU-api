"use client";
import { Link, useCollections, useLocale } from "@numueg/theme-sdk";
import { asString, localized, type SectionRenderProps } from "./_shared";

const RsCategories = ({ instance }: SectionRenderProps) => {
  const { collections } = useCollections();
  const locale = useLocale();
  const s = instance.settings ?? {};
  const label = asString(s.label) || localized(locale, "CATEGORIES", "الأقسام");
  const title = asString(s.title) || localized(locale, "Shop by category", "تسوّق حسب القسم");

  if (collections.length === 0) return null;

  return (
    <section className="rs-section">
      <div className="max-w-[1440px] mx-auto px-6 md:px-12">
        <div className="text-center mb-16">
          <p className="rs-label mb-3">{label}</p>
          <h2 className="rs-headline-md text-[hsl(var(--rs-primary))]">
            {title}
          </h2>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-x-8 gap-y-12">
          {collections.map((cat) => (
            <Link
              key={cat.id}
              to={`/products?category=${cat.slug || cat.id}`}
              className="group"
            >
              {/* Image container — portrait aspect ratio */}
              <div className="aspect-[3/4] overflow-hidden bg-[hsl(var(--rs-surface-low))] mb-4 transition-all duration-500 hover:ring-1 hover:ring-[hsl(var(--rs-primary)/0.2)]">
                {cat.image_url ? (
                  <img
                    src={cat.image_url}
                    alt={cat.name}
                    className="w-full h-full object-cover rs-img-zoom"
                    loading="lazy"
                  />
                ) : (
                  <div className="w-full h-full flex items-center justify-center">
                    <span className="text-[hsl(var(--rs-primary)/0.15)] font-[var(--heading-font,'Cormorant_Garamond')] italic text-4xl">
                      {cat.name?.[0]}
                    </span>
                  </div>
                )}
              </div>

              {/* Category name */}
              <p className="rs-label text-center group-hover:text-[hsl(var(--rs-primary))] transition-colors duration-300">
                {cat.name}
              </p>
            </Link>
          ))}
        </div>
      </div>
    </section>
  );
};

export default RsCategories;
