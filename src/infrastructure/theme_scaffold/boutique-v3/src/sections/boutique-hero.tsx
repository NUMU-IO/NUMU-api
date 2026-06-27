"use client";
import { Link, useLocale } from "@numueg/theme-sdk";
import { motion } from "framer-motion";
import { ArrowLeft } from "lucide-react";
import { applyImageTransform, asImageTransform, asString, localized, type SectionRenderProps } from "./_shared";

const BoutiqueHero = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();

  const headline = asString(s.headline) || localized(locale, "Discover Your Elegance", "اكتشفي أناقتك");
  const subtitle =
    asString(s.subtitle) ||
    localized(
      locale,
      "A curated edit of fashion and accessories — refined design and beautiful quality.",
      "تشكيلة مميزة من الأزياء والإكسسوارات بتصميم راقٍ وجودة عالية",
    );
  const ctaText = asString(s.cta_text) || localized(locale, "Shop Now", "تسوقي الآن");
  const ctaLink = asString(s.cta_link) || "/products";
  const heroImageUrl = asString(s.hero_image_url) || undefined;
  const heroImageTransform = asImageTransform(s.hero_image_url);

  return (
    <section
      className="relative overflow-hidden"
      style={{
        background:
          "linear-gradient(135deg, hsl(var(--hero-bg)), hsl(var(--accent)), hsl(var(--background)))",
      }}
    >
      <div className="container mx-auto px-4">
        <div className="grid grid-cols-1 md:grid-cols-2 min-h-[70vh] items-center gap-8">
          {/* Content side */}
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, ease: "easeOut" }}
            className="py-16 md:py-20 text-center md:text-start"
          >
            <h1 className="text-4xl md:text-5xl lg:text-6xl font-bold leading-tight text-foreground mb-4">
              {headline}
            </h1>

            {/* Decorative ornament between headline and subtitle */}
            <div className="flex items-center justify-center md:justify-start gap-3 my-6">
              <span className="block w-8 h-px bg-primary/40" />
              <span className="block w-2 h-2 rounded-full bg-primary/50" />
              <span className="block w-8 h-px bg-primary/40" />
            </div>

            <p className="text-muted-foreground text-base md:text-lg leading-relaxed mb-8 max-w-md mx-auto md:mx-0">
              {subtitle}
            </p>

            <Link
              to={ctaLink}
              className="inline-flex items-center gap-2 px-8 py-3.5 rounded-full font-semibold text-sm text-primary-foreground transition-all duration-300 hover:shadow-[0_0_20px_hsl(var(--primary)/0.4)] hover:scale-[1.03]"
              style={{ background: "hsl(var(--primary))" }}
            >
              {ctaText} <ArrowLeft size={16} />
            </Link>
          </motion.div>

          {/* Image side */}
          {heroImageUrl && (
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: 0.8, delay: 0.2, ease: "easeOut" }}
              className="hidden md:flex items-center justify-center"
            >
              <div className="relative rounded-3xl overflow-hidden shadow-xl">
                <img
                  src={heroImageUrl}
                  alt=""
                  className="w-full h-auto object-contain max-h-[65vh]"
                  style={applyImageTransform(heroImageTransform, "contain")}
                />
                {/* Soft overlay tint */}
                <div className="absolute inset-0 bg-gradient-to-t from-primary/10 to-transparent pointer-events-none" />
              </div>
            </motion.div>
          )}
        </div>
      </div>
    </section>
  );
};

export default BoutiqueHero;
