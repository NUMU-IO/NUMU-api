"use client";

import { Star, Quote } from "lucide-react";
import { useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import {
  asNumber,
  asString,
  localized,
  readBlocks,
  useDemo,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

interface Review {
  name: string;
  city: string;
  text: string;
  rating: number;
}

const fallbackReviews = (locale: string | undefined): Review[] => [
  { name: localized(locale, "Sarah A.", "سارة أ."), city: localized(locale, "Cairo", "القاهرة"), text: localized(locale, "The quality exceeded my expectations. Fast delivery too!", "الجودة فاقت توقعاتي. والتوصيل كمان كان سريع!"), rating: 5 },
  { name: localized(locale, "Mohamed K.", "محمد ك."), city: localized(locale, "Alexandria", "الإسكندرية"), text: localized(locale, "First time ordering online and it was perfect. Exactly as described.", "أول مرة أطلب أونلاين وكانت تجربة ممتازة. زي الوصف بالظبط."), rating: 5 },
  { name: localized(locale, "Nour H.", "نور ح."), city: localized(locale, "Mansoura", "المنصورة"), text: localized(locale, "Great prices and accurate sizing. WhatsApp support was very helpful.", "أسعار حلوة والمقاسات مظبوطة. ودعم الواتساب ساعدني جدًا."), rating: 4 },
];

const initials = (name: string): string => {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
};

const clampRating = (value: unknown): number => {
  const n = Number(value);
  if (!Number.isFinite(n)) return 5;
  return Math.max(1, Math.min(5, Math.round(n)));
};

const BzTestimonials = ({ instance, sectionId }: SectionRenderProps) => {
  const s = useResolvedSettings(instance);
  const demo = useDemo();
  const locale = useLocale();
  const title = asString(s.title) || localized(locale, "WHAT THEY SAY", "آراء عملائنا");

  // Reviews come from editor `review` blocks; fall back to the curated demo
  // set (or neutral placeholders outside demo mode) when none configured.
  const configured: Review[] = readBlocks(instance, "review")
    .map((r) => ({
      name: asString(r.name),
      city: asString(r.city),
      text: asString(r.text),
      rating: clampRating(asNumber(r.rating, 5)),
    }))
    .filter((r) => r.name && r.text);
  // Configured review blocks win. Otherwise show the curated demo set ONLY in
  // marketplace preview (demo); on an installed store with no reviews yet we
  // render nothing rather than blank/placeholderized cards. The merchant adds
  // `review` blocks to populate it.
  const reviews =
    configured.length > 0
      ? configured
      : demo
        ? fallbackReviews(locale).map((r) => ({ ...r, rating: clampRating(r.rating) }))
        : [];

  if (reviews.length === 0) return null;

  return (
    <section className="py-12 md:py-16 lg:py-24 bg-[var(--bz-navy)]">
      <div className="container mx-auto px-4">
        <h2 className="bz-heading text-2xl sm:text-3xl md:text-4xl text-center text-[var(--bz-cream)] mb-8 md:mb-12">
          <InlineEditable sectionId={sectionId} settingKey="title" value={title} />
        </h2>

        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4 md:gap-6">
          {reviews.map((review, i) => (
            <figure
              key={`${review.name}-${i}`}
              className="relative bg-[var(--bz-cream)] rounded-2xl p-6 sm:p-7 bz-card-hover flex flex-col"
            >
              <Quote
                size={36}
                aria-hidden
                className="absolute top-4 right-4 rtl:right-auto rtl:left-4 text-[var(--bz-amber)]/25"
              />

              <div className="flex items-center gap-1 mb-4" aria-label={`${review.rating} out of 5 stars`}>
                {Array.from({ length: 5 }).map((_, j) => (
                  <Star
                    key={j}
                    size={16}
                    className={
                      j < review.rating
                        ? "fill-[var(--bz-amber)] text-[var(--bz-amber)]"
                        : "fill-transparent text-[var(--bz-dark)]/15"
                    }
                  />
                ))}
              </div>

              <blockquote className="relative text-sm md:text-base text-[var(--bz-dark)]/85 leading-relaxed mb-6 flex-1">
                &ldquo;{review.text}&rdquo;
              </blockquote>

              <figcaption className="flex items-center gap-3 pt-4 border-t border-[var(--bz-dark)]/10">
                <span
                  aria-hidden
                  className="flex items-center justify-center w-10 h-10 rounded-full bg-[var(--bz-navy)] text-[var(--bz-cream)] bz-label text-sm shrink-0"
                >
                  {initials(review.name)}
                </span>
                <span className="flex flex-col min-w-0">
                  <span className="bz-label text-[var(--bz-dark)] truncate">
                    {review.name}
                  </span>
                  <span className="text-xs sm:text-sm text-[var(--bz-gray)] truncate">
                    {review.city}
                  </span>
                </span>
              </figcaption>
            </figure>
          ))}
        </div>
      </div>
    </section>
  );
};

export default BzTestimonials;
