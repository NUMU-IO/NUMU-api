"use client";

import { useRef } from "react";
import { Link, useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import { motion, useScroll, useTransform } from "framer-motion";
import { asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * gilded-promo-banner — faithful V3 port of the V2 GildedPromoBanner
 * (numu-egyptian-bazaar/src/themes/gilded-glamour-boutique/sections/promo-banner/GildedPromoBanner.tsx).
 *
 * Centered scroll-fill statement banner: a static muted ghost layer of the
 * headline (`text-muted-foreground/20`) with a gold `motion.span` overlay
 * whose clipPath wipes left→right (inset(0 100% 0 0) → inset(0 0% 0 0)) as the
 * banner scrolls into view (useScroll offset ["start 0.9","start 0.3"]). Below
 * it an optional muted subtitle and an optional gold-outline CTA. All V2
 * className strings kept VERBATIM, only swapping the brand gold to the
 * repaintable `var(--gilded-gold)` token. Engine-wired: useResolvedSettings
 * (so global tokens + dynamic sources + draft preview resolve) and
 * InlineEditable on every editable text node.
 */
export default function GildedPromoBanner({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();

  const heading =
    asString(s.headline) ||
    localized(locale, "WHERE HERITAGE MEETS THE FUTURE", "حيث يلتقي التراث بالمستقبل");
  const body =
    asString(s.subtitle) ||
    localized(
      locale,
      "Every thread tells a story, every silhouette commands a room, and every piece is destined to become an heirloom.",
      "كل خيط بيحكي حكاية، وكل تصميم بيخطف الأنظار، وكل قطعة مقدّر لها تبقى إرث يتوارث.",
    );
  const ctaText = asString(s.cta_text);
  const ctaLink = asString(s.cta_link) || "/products";

  const ref = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({
    target: ref,
    offset: ["start 0.9", "start 0.3"],
  });

  const clipPath = useTransform(
    scrollYProgress,
    [0, 1],
    ["inset(0 100% 0 0)", "inset(0 0% 0 0)"],
  );

  return (
    <section className="py-16 md:py-24 lg:py-40" data-gilded-section={sectionId}>
      <div className="container mx-auto px-4 text-center">
        <div
          ref={ref}
          className="relative text-xl sm:text-2xl md:text-4xl lg:text-6xl font-bold tracking-[0.04em] sm:tracking-[0.08em] uppercase break-words"
        >
          {/* Static ghost layer (muted) — also carries the inline-edit handle */}
          <span className="text-muted-foreground/20 select-none">
            <InlineEditable sectionId={sectionId} settingKey="headline" value={heading} />
          </span>
          {/* Animated gold overlay revealed by the scroll-driven clipPath */}
          <motion.span
            style={{ clipPath }}
            aria-hidden="true"
            className="absolute inset-0 text-[var(--gilded-gold)]"
          >
            {heading}
          </motion.span>
        </div>

        {body && (
          <p className="mt-6 md:mt-8 text-sm md:text-base text-muted-foreground max-w-2xl mx-auto leading-relaxed">
            <InlineEditable
              sectionId={sectionId}
              settingKey="subtitle"
              value={body}
              multiline
            />
          </p>
        )}

        {ctaText && (
          <Link
            to={ctaLink}
            className="inline-block mt-6 md:mt-8 px-6 sm:px-8 py-2.5 sm:py-3 border border-[var(--gilded-gold)] text-foreground text-xs sm:text-sm tracking-[0.1em] sm:tracking-[0.15em] uppercase font-medium hover:bg-[var(--gilded-gold)] hover:text-foreground transition-all duration-300"
          >
            <InlineEditable sectionId={sectionId} settingKey="cta_text" value={ctaText} />
          </Link>
        )}
      </div>
    </section>
  );
}
