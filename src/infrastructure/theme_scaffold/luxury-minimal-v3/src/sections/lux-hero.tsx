"use client";

import { Link, useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import { motion } from "framer-motion";
import { ArrowLeft } from "lucide-react";
import {
  applyImageTransform,
  asImageTransform,
  asImageUrl,
  asString,
  localized,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * lux-hero — faithful V3 port of the V2 LuxHero
 * (numu-egyptian-bazaar/src/themes/luxury-minimal/sections/hero/LuxHero.tsx).
 *
 * Split 2-column layout: an image on the LEFT (hidden on mobile,
 * `object-contain` over a light-gray frame — matches V2 verbatim), refined
 * content on the RIGHT — a
 * 10px/0.3em eyebrow badge, an uppercase wide-tracked `lux-heading`, a muted
 * subtitle, and a solid-black `lux-btn` CTA with a trailing ArrowLeft. All V2
 * className strings kept verbatim. Engine-wired: useResolvedSettings (so global
 * tokens + dynamic sources resolve) and InlineEditable on every text field.
 */
export default function LuxHero({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();

  const badgeText = asString(s.badge_text);
  const headline =
    asString(s.headline) ||
    localized(locale, "Discover the Art of Refined Elegance", "اكتشف فن الأناقة الراقية");
  const subtitle =
    asString(s.subtitle) ||
    localized(
      locale,
      "A curated edit of clothing and accessories — contemporary design, exceptional quality.",
      "تشكيلة مميزة من الملابس والإكسسوارات بتصميم عصري وجودة عالية.",
    );
  const ctaText = asString(s.cta_text) || localized(locale, "Shop Now", "تسوق الآن");
  const ctaLink = asString(s.cta_link) || "/products";
  const heroImageUrl = asImageUrl(s.hero_image_url);
  // Non-destructive focal/zoom/rotation. Undefined → image renders unchanged.
  const heroImageTransform = asImageTransform(s.hero_image_url);

  return (
    <section className="relative" data-lux-section={sectionId}>
      <div className="container mx-auto px-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-0 min-h-[70vh] items-center">
          {/* Left: Image (object-contain over a light-gray frame — V2 parity) */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 1 }}
            className="hidden md:block h-full"
          >
            <div className="h-full bg-[hsl(var(--lux-gray))]">
              {heroImageUrl && (
                <img
                  src={heroImageUrl}
                  alt=""
                  className="w-full h-full object-contain"
                  style={applyImageTransform(heroImageTransform, "contain")}
                  loading="eager"
                />
              )}
            </div>
          </motion.div>

          {/* Right: Content */}
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.3 }}
            className="py-16 md:py-20 md:pl-12 md:pr-16"
          >
            {badgeText && (
              <p className="text-[10px] uppercase tracking-[0.3em] text-muted-foreground mb-6">
                <InlineEditable sectionId={sectionId} settingKey="badge_text" value={badgeText} />
              </p>
            )}
            <h1 className="lux-heading text-3xl md:text-4xl text-foreground mb-6 leading-relaxed">
              <InlineEditable sectionId={sectionId} settingKey="headline" value={headline} />
            </h1>
            <p className="text-muted-foreground text-sm leading-relaxed mb-8 max-w-sm">
              <InlineEditable
                sectionId={sectionId}
                settingKey="subtitle"
                value={subtitle}
                multiline
              />
            </p>
            {ctaText && (
              <Link to={ctaLink} className="inline-flex items-center gap-2 lux-btn">
                <InlineEditable sectionId={sectionId} settingKey="cta_text" value={ctaText} />
                <ArrowLeft size={14} aria-hidden="true" className="rtl:-scale-x-100" />
              </Link>
            )}
          </motion.div>
        </div>
      </div>
    </section>
  );
}
