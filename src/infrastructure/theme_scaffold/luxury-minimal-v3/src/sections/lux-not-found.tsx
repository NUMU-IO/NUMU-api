"use client";

import { Link, useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import { motion } from "framer-motion";
import { ArrowLeft } from "lucide-react";
import { asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * lux-not-found — body for the `404` template, in the Luxury Minimal voice.
 * Centered, airy column: an oversized hairline-thin "404" lux-heading, a
 * 10px/0.3em uppercase muted eyebrow, a quiet muted subtext, and a single
 * solid-black `lux-btn` CTA back home. Sharp edges, mono palette, gold accent
 * lives in the global tokens. Engine-wired: useResolvedSettings (global tokens
 * + dynamic sources) + InlineEditable on every text field. All copy bilingual.
 */
export default function LuxNotFound({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();

  const heading = asString(s.heading) || localized(locale, "404", "٤٠٤");
  const eyebrow =
    asString(s.eyebrow) || localized(locale, "Page not found", "الصفحة غير موجودة");
  const subtext =
    asString(s.subtext) ||
    localized(
      locale,
      "The page you're looking for can't be found. Let's get you back to the shop.",
      "الصفحة التي تبحث عنها غير متاحة. دعنا نعيدك إلى المتجر.",
    );
  const ctaText = asString(s.cta_text) || localized(locale, "Back to home", "العودة للرئيسية");
  const ctaLink = asString(s.cta_link) || "/";

  return (
    <section
      className="min-h-[60vh] flex items-center justify-center px-4 py-24"
      data-lux-section={sectionId}
    >
      <motion.div
        initial={{ opacity: 0, y: 24 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.8 }}
        className="text-center max-w-lg"
      >
        <h1 className="lux-heading text-[88px] sm:text-[140px] md:text-[180px] leading-none text-foreground select-none">
          <InlineEditable sectionId={sectionId} settingKey="heading" value={heading} />
        </h1>
        {eyebrow && (
          <p className="text-[10px] uppercase tracking-[0.3em] text-muted-foreground mt-6">
            <InlineEditable sectionId={sectionId} settingKey="eyebrow" value={eyebrow} />
          </p>
        )}
        {subtext && (
          <p className="text-muted-foreground text-sm leading-relaxed mt-4 max-w-sm mx-auto">
            <InlineEditable
              sectionId={sectionId}
              settingKey="subtext"
              value={subtext}
              multiline
            />
          </p>
        )}
        {ctaText && (
          <Link to={ctaLink} className="inline-flex items-center gap-2 lux-btn mt-8">
            <ArrowLeft size={14} aria-hidden="true" className="rtl:-scale-x-100" />
            <InlineEditable sectionId={sectionId} settingKey="cta_text" value={ctaText} />
          </Link>
        )}
      </motion.div>
    </section>
  );
}
