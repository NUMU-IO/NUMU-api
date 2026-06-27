"use client";

import { useRef } from "react";
import { Link, useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import { motion, useScroll, useTransform } from "framer-motion";
import {
  applyImageTransform,
  asImageAlt,
  asImageTransform,
  asImageUrl,
  asString,
  localized,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * gilded-hero — faithful V3 port of the V2 GildedHero
 * (numu-egyptian-bazaar/src/themes/gilded-glamour-boutique/sections/hero/GildedHero.tsx).
 *
 * Full-screen parallax hero (`md:h-screen min-h-[80vh]`). Optional merchant
 * image renders `object-contain` (preserve aspect) over the gold→ink gradient
 * fallback, with a dark `bg-foreground/40` overlay. On mobile, a mobile-specific
 * image is treated as portrait art (4:5 container); a desktop-only image renders
 * at natural aspect so wide banners aren't crushed. Centered content: a Montserrat
 * uppercase wide-tracked H1, an uppercase letter-spaced subtitle, and a gold-fill
 * CTA that scales on hover. Stagger delays 0.2 / 0.5 / 0.8s, matching V2 verbatim.
 *
 * Engine-wired: useResolvedSettings (global tokens + dynamic sources + draft
 * preview), bilingual defaults via localized(), InlineEditable on every
 * section-level text field, and non-destructive image transforms.
 */
export default function GildedHero({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();

  const headline =
    asString(s.headline) || localized(locale, "THE NEW EMPIRE", "الإمبراطورية الجديدة");
  const subtitle =
    asString(s.subtitle) ||
    localized(
      locale,
      "Curated Excellence & Timeless Precision",
      "تميّز منتقى بعناية وإتقان لا يعرف الزمن",
    );
  const ctaText = asString(s.cta_text) || localized(locale, "Discover Collection", "اكتشف التشكيلة");
  const ctaLink = asString(s.cta_link) || "/products";

  const heroImageUrl = asImageUrl(s.hero_image_url) || undefined;
  const heroImageUrlMobile = asImageUrl(s.hero_image_mobile) || undefined;
  const heroImageAlt = asImageAlt(s.hero_image_url) || headline;
  // When the merchant uploads a mobile-specific image, treat it as
  // intentionally portrait-cropped art and render it in a tall 4:5 container.
  // When they only have a desktop image, fall back to natural aspect on mobile
  // so a wide banner isn't crushed/cropped.
  const mobileImage = heroImageUrlMobile || heroImageUrl;
  const hasMobileArt = Boolean(heroImageUrlMobile);
  // Non-destructive image transforms (focal/zoom/rotation). With no saved
  // transform asImageTransform → undefined and applyImageTransform → {}, so
  // the image renders exactly as before.
  const heroImageTransform = asImageTransform(s.hero_image_url);
  const heroImageMobileTransform = asImageTransform(s.hero_image_mobile);
  const enableParallax = s.enable_parallax !== false;

  const ref = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({
    target: ref,
    offset: ["start start", "end start"],
  });

  const y = useTransform(scrollYProgress, [0, 1], ["0%", "20%"]);
  const opacity = useTransform(scrollYProgress, [0, 0.8], [1, 0]);

  return (
    <section
      ref={ref}
      data-gilded-section={sectionId}
      className="relative w-full overflow-hidden bg-foreground md:min-h-[80vh] md:h-screen"
    >
      {/* Mobile background.
          - If the merchant uploaded a mobile-specific image, render it
            contain-fit in a tall portrait container — they've designed art
            for this aspect.
          - Otherwise the desktop image renders in-flow at its natural
            aspect, so wide banners aren't cropped or letterboxed. */}
      <div className="relative md:hidden">
        {hasMobileArt ? (
          <div className="aspect-[4/5] w-full overflow-hidden">
            <img
              src={mobileImage}
              alt={heroImageAlt}
              className="w-full h-full object-contain"
              style={applyImageTransform(heroImageMobileTransform, "contain")}
            />
          </div>
        ) : mobileImage ? (
          <img src={mobileImage} alt={heroImageAlt} className="block w-full h-auto" />
        ) : (
          <div
            className="aspect-[4/5] w-full"
            style={{
              background:
                "linear-gradient(135deg, hsl(var(--gold-dark)), hsl(var(--foreground)))",
            }}
          />
        )}
        <div className="absolute inset-0 bg-foreground/40 pointer-events-none" />
      </div>

      {/* Desktop parallax background — object-contain over the gold→ink gradient. */}
      <motion.div
        style={enableParallax ? { y } : undefined}
        className="hidden md:block absolute inset-0"
      >
        <div
          className="absolute inset-0"
          style={{
            background:
              "linear-gradient(135deg, hsl(var(--gold-dark)), hsl(var(--foreground)))",
          }}
        />
        {heroImageUrl ? (
          <img
            src={heroImageUrl}
            alt=""
            aria-hidden="true"
            className="relative w-full h-full object-contain"
            style={applyImageTransform(heroImageTransform, "contain")}
          />
        ) : null}
        <div className="absolute inset-0 bg-foreground/40" />
      </motion.div>

      {/* Content */}
      <motion.div
        style={{ opacity }}
        className="absolute inset-0 z-10 flex flex-col items-center justify-center w-full h-full text-center px-4"
      >
        <motion.h1
          initial={{ opacity: 0, y: 30 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 0.2 }}
          className="text-3xl sm:text-5xl md:text-7xl lg:text-8xl font-bold tracking-[0.04em] sm:tracking-[0.08em] text-card uppercase"
          style={{ fontFamily: "'Montserrat', sans-serif" }}
        >
          <InlineEditable sectionId={sectionId} settingKey="headline" value={headline} />
        </motion.h1>
        <motion.p
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 0.5 }}
          className="mt-4 sm:mt-6 text-[10px] sm:text-sm md:text-base tracking-[0.15em] sm:tracking-[0.3em] uppercase text-card/90 font-light px-2"
        >
          <InlineEditable
            sectionId={sectionId}
            settingKey="subtitle"
            value={subtitle}
            multiline
          />
        </motion.p>
        {ctaText && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.8 }}
            className="mt-8 sm:mt-10"
          >
            <Link
              to={ctaLink}
              className="inline-block px-6 sm:px-10 py-3 sm:py-4 bg-gold text-foreground text-xs sm:text-sm tracking-[0.15em] sm:tracking-[0.2em] uppercase font-semibold hover:scale-[1.04] transition-all duration-200 ease-out"
            >
              <InlineEditable sectionId={sectionId} settingKey="cta_text" value={ctaText} />
            </Link>
          </motion.div>
        )}
      </motion.div>
    </section>
  );
}
