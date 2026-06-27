"use client";

import { Link, useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import { motion } from "framer-motion";
import { ArrowLeft } from "lucide-react";
import { asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * gilded-not-found — body for the `404` template, in the Gilded voice: an
 * oversized faded gold status ("404") behind a quiet uppercase Montserrat
 * headline, a muted subhead, and a single gold CTA back to the catalog. Warm
 * beige canvas, sharp edges, gold accent. Keeps a missing page feeling like
 * the brand rather than a server error.
 *
 * Engine-wired: useResolvedSettings (global tokens + dynamic sources + draft
 * preview) + InlineEditable on every text field. All copy bilingual EN/AR and
 * RTL-correct (the back-arrow flips via rtl:-scale-x-100).
 */
export default function GildedNotFound({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();

  const status = asString(s.status_label) || localized(locale, "404", "٤٠٤");
  const headline =
    asString(s.headline) || localized(locale, "Page not found", "الصفحة غير موجودة");
  const subhead =
    asString(s.subhead) ||
    localized(
      locale,
      "The page you're looking for doesn't exist. Let's get you back to the collection.",
      "الصفحة التي تبحث عنها غير موجودة. دعنا نعيدك إلى التشكيلة.",
    );
  const ctaText = asString(s.cta_text) || localized(locale, "Back to shop", "العودة للتسوق");
  const ctaLink = asString(s.cta_link) || "/products";

  return (
    <section
      className="bg-background min-h-[70vh] flex items-center justify-center px-4 py-24"
      data-testid="storefront-404-page"
      data-gilded-section={sectionId}
    >
      <motion.div
        initial={{ opacity: 0, y: 24 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.8 }}
        className="text-center max-w-lg"
      >
        {status && (
          <p className="gld-heading text-[88px] sm:text-[140px] md:text-[180px] leading-none text-[var(--gilded-gold)]/15 select-none">
            <InlineEditable sectionId={sectionId} settingKey="status_label" value={status} />
          </p>
        )}
        {headline && (
          <h1 className="gld-heading text-2xl md:text-3xl text-foreground -mt-4 md:-mt-8">
            <InlineEditable sectionId={sectionId} settingKey="headline" value={headline} />
          </h1>
        )}
        {subhead && (
          <p className="text-muted-foreground text-sm leading-relaxed mt-4 max-w-sm mx-auto">
            <InlineEditable
              sectionId={sectionId}
              settingKey="subhead"
              value={subhead}
              multiline
            />
          </p>
        )}
        {ctaText && (
          <Link to={ctaLink} className="gld-btn mt-8">
            <ArrowLeft size={14} aria-hidden="true" className="rtl:-scale-x-100" />
            <InlineEditable sectionId={sectionId} settingKey="cta_text" value={ctaText} />
          </Link>
        )}
      </motion.div>
    </section>
  );
}
