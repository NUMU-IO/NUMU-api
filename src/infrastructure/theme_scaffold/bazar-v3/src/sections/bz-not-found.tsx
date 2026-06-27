"use client";

import { Link, useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import { ShoppingBag } from "lucide-react";
import { asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * bz-not-found — body for the `404` template, in the Bazar voice. A big
 * amber "404" over a wavy hero, a headline + subhead, and a CTA back to
 * the shop. Keeps a 404 feeling like the store, not a server error page.
 */
export default function BzNotFound({
  instance,
  sectionId,
}: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();
  const status = asString(s.status_label) || "404";
  const headline = asString(s.headline) || localized(locale, "LOST IN THE BAZAR", "تهت في بازار");
  const subhead =
    asString(s.subhead) ||
    localized(locale, "The page you're after wandered off. Let's get you back to the good stuff.", "الصفحة اللي بتدوّر عليها مش موجودة. تعالى نرجّعك للحاجات الحلوة.");
  const ctaLabel = asString(s.cta_label) || localized(locale, "BACK TO SHOP", "رجوع للتسوّق");
  const ctaHref = asString(s.cta_href) || "/products";

  return (
    <section
      className="bz-wavy-bg min-h-[70vh] flex items-center justify-center px-6 py-20 relative overflow-hidden"
      data-bz-section={sectionId}
    >
      {/* Decorative bazar blobs */}
      <div
        aria-hidden="true"
        className="absolute -top-10 -end-10 w-48 h-48 bg-[var(--bz-cream)]/30 bz-blob pointer-events-none"
      />
      <div
        aria-hidden="true"
        className="absolute -bottom-12 -start-8 w-40 h-40 bg-[var(--bz-navy)]/15 bz-blob bz-blob-delay-2 pointer-events-none"
      />

      <div className="relative text-center max-w-xl">
        <div className="bz-heading text-[80px] sm:text-[120px] md:text-[160px] leading-none text-[var(--bz-dark)]">
          <InlineEditable
            sectionId={sectionId}
            settingKey="status_label"
            value={status}
          />
        </div>
        <h1 className="bz-heading text-2xl sm:text-3xl md:text-4xl text-[var(--bz-dark)] mt-2">
          <InlineEditable
            sectionId={sectionId}
            settingKey="headline"
            value={headline}
          />
        </h1>
        <p className="text-sm md:text-base text-[var(--bz-dark)]/70 leading-relaxed mt-4 max-w-md mx-auto">
          <InlineEditable
            sectionId={sectionId}
            settingKey="subhead"
            value={subhead}
            multiline
          />
        </p>
        <Link
          to={ctaHref}
          className="bz-btn bz-btn-filled inline-flex items-center gap-2 px-8 py-3.5 text-xs mt-8"
        >
          <ShoppingBag size={14} aria-hidden="true" />
          <InlineEditable
            sectionId={sectionId}
            settingKey="cta_label"
            value={ctaLabel}
          />
        </Link>
      </div>
    </section>
  );
}
