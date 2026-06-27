"use client";
import { Link, useLocale } from "@numueg/theme-sdk";
import { motion } from "framer-motion";
import { ArrowLeft } from "lucide-react";
import { applyImageTransform, asImageTransform, asString, localized, type SectionRenderProps } from "./_shared";

const NBHero = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const badge = asString(s.badge_text) || localized(locale, "🎉 Up to 25% off", "🎉 خصومات تصل لـ ٢٥٪");
  const headline = asString(s.headline) || localized(locale, "Discover the best products at the best prices", "اكتشف أحلى المنتجات بأفضل الأسعار");
  const subtitle = asString(s.subtitle) || localized(
    locale,
    "A curated selection of clothing and accessories, delivered across Egypt. High quality at fair prices.",
    "تشكيلة مميزة من الملابس والإكسسوارات بتوصيل لكل مصر. جودة عالية وأسعار مناسبة.",
  );
  const ctaText = asString(s.cta_text) || localized(locale, "Shop now", "تسوق الآن");
  const ctaLink = asString(s.cta_link, "/products");
  const secondaryText = asString(s.secondary_text) || localized(locale, "New clothing", "الملابس الجديدة");
  const secondaryLink = asString(s.secondary_link, "/products?category=clothing");
  const img1 = s.hero_image_url as string | undefined;
  const img2 = s.hero_image_2 as string | undefined;
  const img3 = s.hero_image_3 as string | undefined;
  const img4 = s.hero_image_4 as string | undefined;
  const img1Transform = asImageTransform(s.hero_image_url);
  const img2Transform = asImageTransform(s.hero_image_2);
  const img3Transform = asImageTransform(s.hero_image_3);
  const img4Transform = asImageTransform(s.hero_image_4);

  return (
    <section className="py-12 md:py-20">
      <div className="container mx-auto px-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-8 items-center">
          <motion.div
            initial={{ opacity: 0, x: 30 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.5 }}
          >
            {badge && (
              <span className="nb-badge px-3 py-1 rounded text-xs inline-block mb-4">
                {badge}
              </span>
            )}
            <h1 className="text-4xl md:text-6xl lg:text-7xl font-black leading-[0.95] mb-6">
              {headline}
            </h1>
            <p className="text-muted-foreground text-base md:text-lg mb-8 max-w-md font-medium">
              {subtitle}
            </p>
            <div className="flex flex-wrap gap-3">
              <Link
                to={ctaLink}
                className="inline-flex items-center gap-2 px-8 py-3.5 rounded-lg nb-btn text-sm"
              >
                {ctaText} <ArrowLeft size={18} />
              </Link>
              {secondaryText && (
                <Link
                  to={secondaryLink}
                  className="inline-flex items-center gap-2 px-8 py-3.5 rounded-lg nb-btn-secondary text-sm"
                >
                  {secondaryText}
                </Link>
              )}
            </div>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.5, delay: 0.2 }}
            className="hidden md:grid grid-cols-2 gap-4"
          >
            {img1 && !img2 ? (
              <div className="col-span-2 nb-img-frame rounded-lg overflow-hidden aspect-[16/9]">
                <img
                  src={img1}
                  alt=""
                  className="w-full h-full object-contain"
                  style={applyImageTransform(img1Transform, "contain")}
                />
              </div>
            ) : (img1 || img2 || img3 || img4) ? (
              <>
                <div className="space-y-4">
                  {img1 && (
                    <div className="nb-img-frame rounded-lg aspect-[3/4]">
                      <img
                        src={img1}
                        alt=""
                        className="w-full h-full object-contain"
                        style={applyImageTransform(img1Transform, "contain")}
                      />
                    </div>
                  )}
                  {img2 && (
                    <div className="nb-img-frame rounded-lg aspect-square">
                      <img
                        src={img2}
                        alt=""
                        className="w-full h-full object-contain"
                        style={applyImageTransform(img2Transform, "contain")}
                      />
                    </div>
                  )}
                </div>
                <div className="space-y-4 mt-8">
                  {img3 && (
                    <div className="nb-img-frame rounded-lg aspect-square">
                      <img
                        src={img3}
                        alt=""
                        className="w-full h-full object-contain"
                        style={applyImageTransform(img3Transform, "contain")}
                      />
                    </div>
                  )}
                  {img4 && (
                    <div className="nb-img-frame rounded-lg aspect-[3/4]">
                      <img
                        src={img4}
                        alt=""
                        className="w-full h-full object-contain"
                        style={applyImageTransform(img4Transform, "contain")}
                      />
                    </div>
                  )}
                </div>
              </>
            ) : null}
          </motion.div>
        </div>
      </div>
    </section>
  );
};

export default NBHero;
