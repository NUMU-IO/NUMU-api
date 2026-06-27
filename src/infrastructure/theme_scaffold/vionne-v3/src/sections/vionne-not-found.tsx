"use client";

import { Link, useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import { motion } from "framer-motion";
import { asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * vionne-not-found — body for the `404` template, in the Vionne voice: an
 * oversized hairline "404", a quiet headline + subhead, and an underlined CTA
 * back to the catalog. Upgrades the host's neutral 404 backstop to a branded
 * one. All copy editable + bilingual.
 */
export default function VionneNotFound({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();

  const status = asString(s.status_label) || "404";
  const headline =
    asString(s.headline) || localized(locale, "This page slipped away", "هذه الصفحة غير موجودة");
  const subhead =
    asString(s.subhead) ||
    localized(
      locale,
      "The page you're looking for can't be found. Let's get you back to the collection.",
      "الصفحة التي تبحثين عنها غير متاحة. دعينا نعيدك إلى التشكيلة.",
    );
  const ctaText = asString(s.cta_text) || localized(locale, "Back to shop", "العودة للتسوق");
  const ctaLink = asString(s.cta_link) || "/products";

  return (
    <section className="bg-background min-h-[70vh] flex items-center justify-center px-4 py-24">
      <motion.div
        initial={{ opacity: 0, y: 24 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.8 }}
        className="text-center max-w-lg"
      >
        <p className="vn-heading text-[88px] sm:text-[140px] md:text-[180px] leading-none text-[var(--vn-ink)]/10 select-none">
          <InlineEditable sectionId={sectionId} settingKey="status_label" value={status} />
        </p>
        {headline && (
          <h1 className="vn-heading text-2xl md:text-3xl text-[var(--vn-ink)] -mt-6 md:-mt-10">
            <InlineEditable sectionId={sectionId} settingKey="headline" value={headline} />
          </h1>
        )}
        {subhead && (
          <p className="text-sm text-[var(--vn-muted)] leading-relaxed mt-4 max-w-sm mx-auto">
            <InlineEditable sectionId={sectionId} settingKey="subhead" value={subhead} multiline />
          </p>
        )}
        {ctaText && (
          <Link
            to={ctaLink}
            className="vn-label inline-block mt-8 text-[var(--vn-ink)] border-b border-[var(--vn-ink)] pb-1 text-xs"
          >
            <InlineEditable sectionId={sectionId} settingKey="cta_text" value={ctaText} />
          </Link>
        )}
      </motion.div>
    </section>
  );
}
