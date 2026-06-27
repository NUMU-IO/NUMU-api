"use client";

import { useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import { asNumber, asString, localized, type SectionRenderProps } from "./_shared";
import { InlineEditable } from "./_inline-editable";

const BzMarquee = ({ instance, sectionId }: SectionRenderProps) => {
  const s = useResolvedSettings(instance);
  const locale = useLocale();
  const text =
    asString(s.text) ||
    localized(locale, "PREMIUM QUALITY • BAZAR • BOLD DESIGN • EXCLUSIVE DROPS •", "جودة فاخرة • بازار • تصميم جريء • إصدارات حصرية •");
  // Clamp to the schema range (1–10) so a stray value can't render hundreds
  // of spans.
  const repeatCount = Math.max(1, Math.min(10, asNumber(s.repeat_count, 2)));

  return (
    <section className="bg-[var(--bz-navy)] py-5 overflow-hidden" aria-label={text}>
      <div className="bz-marquee-track">
        {/* First copy is the editable one (visible to merchants in the
            customizer); the remaining copies are decorative duplicates so the
            CSS loop reads as a seamless cycle. */}
        <span className="bz-heading text-lg sm:text-xl md:text-2xl lg:text-3xl text-[var(--bz-amber)] whitespace-nowrap mx-6 sm:mx-8">
          <InlineEditable sectionId={sectionId} settingKey="text" value={text} />
        </span>
        {[...Array(Math.max(0, repeatCount - 1))].map((_, i) => (
          <span
            key={i}
            aria-hidden="true"
            className="bz-heading text-lg sm:text-xl md:text-2xl lg:text-3xl text-[var(--bz-amber)] whitespace-nowrap mx-6 sm:mx-8"
          >
            {text}
          </span>
        ))}
      </div>
    </section>
  );
};

export default BzMarquee;
