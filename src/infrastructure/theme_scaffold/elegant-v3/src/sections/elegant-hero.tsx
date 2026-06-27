"use client";
import { Link, useLocale } from "@numueg/theme-sdk";
import { motion } from "framer-motion";
import { ArrowLeft } from "lucide-react";
import { applyImageTransform, asImageTransform, asString, localized, type SectionRenderProps } from "./_shared";

const ElegantHero = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();

  const headline =
    asString(s.headline) ||
    localized(locale, "Discover Timeless Elegance", "اكتشف روائع التصميم الأنيق");
  const subtitle =
    asString(s.subtitle) ||
    localized(
      locale,
      "A luxurious edit, hand-picked to suit your refined taste — exceptional quality and unrivalled elegance.",
      "تشكيلة فاخرة مختارة بعناية لتناسب ذوقك الرفيع. جودة استثنائية وأناقة لا تُضاهى.",
    );
  const ctaText = asString(s.cta_text) || localized(locale, "Shop Now", "تسوق الآن");
  const ctaLink = asString(s.cta_link) || "/products";
  const heroImage = asString(s.hero_image_url) || undefined;
  const heroImageTransform = asImageTransform(s.hero_image_url);

  return (
    <section className="relative min-h-[70vh] flex items-center overflow-hidden">
      {/* Background image with warm overlay */}
      {heroImage && (
        <div className="absolute inset-0">
          <img
            src={heroImage}
            alt=""
            className="w-full h-full object-contain"
            style={applyImageTransform(heroImageTransform, "contain")}
          />
          <div className="absolute inset-0 bg-gradient-to-l from-[hsl(30_40%_12%/0.85)] via-[hsl(30_40%_12%/0.7)] to-[hsl(30_40%_12%/0.4)]" />
        </div>
      )}

      {/* Fallback warm background when no image */}
      {!heroImage && (
        <div className="absolute inset-0 bg-[hsl(var(--hero-bg))]" />
      )}

      <div className="container mx-auto px-4 relative z-10">
        <div className="max-w-xl md:max-w-2xl py-16 md:py-24">
          <motion.div
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, ease: "easeOut" }}
          >
            {/* Headline — large, serif-feeling weight */}
            <h1
              className={`text-3xl md:text-5xl lg:text-6xl font-bold leading-tight mb-6 ${
                heroImage ? "text-white" : "text-foreground"
              }`}
              style={{ fontFamily: "var(--font-heading, serif)" }}
            >
              {headline}
            </h1>

            {/* Thin decorative line */}
            <div
              className={`w-16 h-px mb-6 ${
                heroImage ? "bg-white/50" : "bg-primary/40"
              }`}
            />

            {/* Subtitle */}
            <p
              className={`text-base md:text-lg leading-relaxed mb-10 max-w-lg ${
                heroImage
                  ? "text-white/80"
                  : "text-muted-foreground"
              }`}
            >
              {subtitle}
            </p>

            {/* Single elegant CTA */}
            <Link
              to={ctaLink}
              className={`inline-flex items-center gap-2 px-8 py-3.5 text-sm font-semibold tracking-wide transition-all duration-300 ${
                heroImage
                  ? "border border-white/60 text-white hover:bg-white hover:text-[hsl(30_40%_12%)]"
                  : "border border-primary text-primary hover:bg-primary hover:text-primary-foreground"
              }`}
            >
              {ctaText}
              <ArrowLeft size={16} />
            </Link>
          </motion.div>
        </div>
      </div>
    </section>
  );
};

export default ElegantHero;
