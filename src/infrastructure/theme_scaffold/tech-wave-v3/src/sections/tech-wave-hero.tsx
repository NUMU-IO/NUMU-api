"use client";
import { Link, useLocale } from "@numueg/theme-sdk";
import { motion } from "framer-motion";
import { ArrowLeft, Truck, ShieldCheck, CreditCard } from "lucide-react";
import { applyImageTransform, asImageTransform, asImageUrl, asString, localized, type SectionRenderProps } from "./_shared";

/**
 * Tech Wave hero — faithful port of the V2 in-tree
 * numu-egyptian-bazaar/src/themes/tech-wave/sections/hero/TechWaveHero.tsx
 * (wave section + neon CTA + 2×2 image mosaic + mobile trust strip),
 * re-plumbed on the V3 SDK (`Link`, settings from `instance.settings`).
 */
const TechWaveHero = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const badge = asString(s.badge_text) || localized(locale, "\u{1F389} Up to 25% off", "\u{1F389} خصومات تصل لـ ٢٥٪");
  const headline = asString(s.headline) || localized(locale, "Discover the best products at the best prices", "اكتشف أحلى المنتجات بأفضل الأسعار");
  const subtitle =
    asString(s.subtitle) ||
    localized(
      locale,
      "A curated lineup of clothing and accessories delivered across Egypt. Great quality at prices that work for you.",
      "تشكيلة مميزة من الملابس والإكسسوارات بتوصيل لكل مصر. جودة عالية وأسعار مناسبة.",
    );
  const ctaText = asString(s.cta_text) || localized(locale, "Shop now", "تسوق الآن");
  const ctaLink = asString(s.cta_link) || "/products";
  const secondaryText = asString(s.secondary_text) || localized(locale, "New arrivals", "الملابس الجديدة");
  const secondaryLink = asString(s.secondary_link) || "/products?category=clothing";
  const showTrust = s.show_trust_strip !== false;
  const img1 = asImageUrl(s.hero_image_url);
  const img2 = asImageUrl(s.hero_image_2);
  const img3 = asImageUrl(s.hero_image_3);
  const img4 = asImageUrl(s.hero_image_4);
  const img1Transform = asImageTransform(s.hero_image_url);
  const img2Transform = asImageTransform(s.hero_image_2);
  const img3Transform = asImageTransform(s.hero_image_3);
  const img4Transform = asImageTransform(s.hero_image_4);

  return (
    <>
      {/* Hero — wave section with neon accents */}
      <section className="tw-wave-section relative overflow-hidden">
        <div className="container mx-auto px-4 py-14 md:py-20 relative z-[1]">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-8 items-center">
            <motion.div
              initial={{ opacity: 0, x: 30 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.6 }}
            >
              {badge && (
                <span className="inline-block px-4 py-1.5 rounded-lg text-sm font-bold mb-4 tw-badge">
                  {badge}
                </span>
              )}
              <h1 className="text-3xl md:text-5xl lg:text-6xl font-black text-[hsl(var(--foreground))] mb-4 leading-tight">
                {headline}
              </h1>
              <p className="text-[hsl(var(--muted-foreground))] text-base md:text-lg mb-8 max-w-md">
                {subtitle}
              </p>
              <div className="flex flex-wrap gap-3">
                <Link
                  to={ctaLink}
                  className="inline-flex items-center gap-2 px-8 py-3.5 rounded-xl tw-neon-btn text-sm relative z-[1]"
                >
                  {ctaText}
                  <ArrowLeft size={18} />
                </Link>
                {secondaryText && (
                  <Link
                    to={secondaryLink}
                    className="inline-flex items-center gap-2 px-8 py-3.5 rounded-xl tw-btn-secondary text-sm"
                  >
                    {secondaryText}
                  </Link>
                )}
              </div>
            </motion.div>
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: 0.6, delay: 0.2 }}
              className="hidden md:grid grid-cols-2 gap-4"
            >
              <div className="space-y-4">
                {img1 && (
                  <div className="tw-img-frame rounded-xl overflow-hidden aspect-[3/4]">
                    <img
                      src={img1}
                      alt=""
                      className="w-full h-full object-contain"
                      style={applyImageTransform(img1Transform, "contain")}
                    />
                  </div>
                )}
                {img2 && (
                  <div className="tw-img-frame rounded-xl overflow-hidden aspect-square">
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
                  <div className="tw-img-frame rounded-xl overflow-hidden aspect-square">
                    <img
                      src={img3}
                      alt=""
                      className="w-full h-full object-contain"
                      style={applyImageTransform(img3Transform, "contain")}
                    />
                  </div>
                )}
                {img4 && (
                  <div className="tw-img-frame rounded-xl overflow-hidden aspect-[3/4]">
                    <img
                      src={img4}
                      alt=""
                      className="w-full h-full object-contain"
                      style={applyImageTransform(img4Transform, "contain")}
                    />
                  </div>
                )}
              </div>
            </motion.div>
          </div>
        </div>
      </section>

      {/* Trust Strip */}
      {showTrust && (
        <section
          className="py-4 md:hidden"
          style={{ borderBottom: "1px solid hsl(195 100% 50% / 0.1)" }}
        >
          <div className="container mx-auto px-4">
            <div className="flex gap-6 overflow-x-auto scrollbar-hide">
              {[
                { icon: Truck, label: localized(locale, "Fast shipping", "شحن سريع") },
                { icon: ShieldCheck, label: localized(locale, "Quality guarantee", "ضمان الجودة") },
                { icon: CreditCard, label: localized(locale, "Secure payment", "دفع آمن") },
              ].map((item, i) => (
                <div key={i} className="flex items-center gap-2 shrink-0">
                  <item.icon
                    size={16}
                    className="text-[hsl(var(--primary))]"
                  />
                  <span className="text-xs font-semibold text-[hsl(var(--muted-foreground))] whitespace-nowrap">
                    {item.label}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </section>
      )}
    </>
  );
};

export default TechWaveHero;
