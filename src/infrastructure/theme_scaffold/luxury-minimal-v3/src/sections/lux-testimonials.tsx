"use client";

import { Star } from "lucide-react";
import { useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import {
  asNumber,
  asString,
  localized,
  readBlocks,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * lux-testimonials — faithful V3 port of the V2 LuxTestimonials
 * (numu-egyptian-bazaar/src/themes/luxury-minimal/sections/testimonials/LuxTestimonials.tsx).
 *
 * Centered 10px/0.3em eyebrow title over a 3-up grid of bordered review cards:
 * a star row (lucide Star, filled at 70% opacity, count = rating), an italic
 * quote at 80% opacity, an 8px hairline divider, the reviewer name, and an
 * optional city. All V2 className strings kept verbatim. Engine-wired:
 * useResolvedSettings (so global tokens + dynamic sources resolve), a `review`
 * block list read via readBlocks (so the merchant can add/remove/reorder
 * reviews), and InlineEditable on every text field. Falls back to three V2
 * default reviews when no blocks are configured.
 */

interface TestimonialReview {
  text: string;
  name: string;
  city: string;
  rating: number;
  blockId?: string;
}

export default function LuxTestimonials({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();

  const title =
    asString(s.title) || localized(locale, "What Our Clients Say", "رأي عملاءنا");

  // Read merchant-configured reviews from `review` blocks (add/remove/reorder
  // in the editor) via the shared readBlocks helper.
  const blockReviews: TestimonialReview[] = readBlocks(instance, "review").map((r) => ({
    text: asString(r.text),
    name: asString(r.name),
    city: asString(r.city),
    rating: asNumber(r.rating, 5),
  }));

  // V2 default reviews when the merchant has configured none.
  const defaultReviews: TestimonialReview[] = [
    {
      text: localized(
        locale,
        "Exceptional quality and an effortlessly elegant fit. My new favourite piece.",
        "جودة استثنائية وقصّة أنيقة بلا تكلّف. أصبحت قطعتي المفضّلة.",
      ),
      name: localized(locale, "Layla Hassan", "ليلى حسن"),
      city: localized(locale, "Cairo", "القاهرة"),
      rating: 5,
    },
    {
      text: localized(
        locale,
        "The attention to detail is unmatched. Refined, timeless and beautifully made.",
        "الاهتمام بالتفاصيل لا يُضاهى. راقٍ وخالد ومصنوع بإتقان.",
      ),
      name: localized(locale, "Omar Khaled", "عمر خالد"),
      city: localized(locale, "Alexandria", "الإسكندرية"),
      rating: 5,
    },
    {
      text: localized(
        locale,
        "Fast delivery and a truly luxurious experience from start to finish.",
        "توصيل سريع وتجربة فاخرة حقًّا من البداية إلى النهاية.",
      ),
      name: localized(locale, "Nour Adel", "نور عادل"),
      city: localized(locale, "Giza", "الجيزة"),
      rating: 5,
    },
  ];

  const usingBlocks = blockReviews.length > 0;
  const reviews = usingBlocks ? blockReviews : defaultReviews;

  if (reviews.length === 0) return null;

  return (
    <section className="py-14 bg-foreground/[0.02]" data-lux-section={sectionId}>
      <div className="container mx-auto px-4">
        <p className="text-[10px] uppercase tracking-[0.3em] text-muted-foreground text-center mb-8">
          <InlineEditable sectionId={sectionId} settingKey="title" value={title} />
        </p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {reviews.map((review, i) => {
            const rating = Math.max(1, Math.min(5, Math.round(review.rating || 5)));
            return (
              <div key={review.blockId ?? i} className="border border-foreground/8 p-6 text-center">
                <div className="flex items-center justify-center gap-0.5 mb-4">
                  {Array.from({ length: rating }).map((_, j) => (
                    <Star
                      key={j}
                      size={12}
                      className="fill-foreground/70 text-foreground/70"
                      aria-hidden="true"
                    />
                  ))}
                </div>
                <p className="text-sm text-foreground/80 leading-relaxed mb-4 italic">
                  &quot;
                  <InlineEditable
                    sectionId={sectionId}
                    blockId={review.blockId}
                    settingKey="text"
                    value={review.text}
                    multiline
                  />
                  &quot;
                </p>
                <div className="w-8 h-px bg-foreground/15 mx-auto mb-3" />
                <p className="text-xs font-medium tracking-wide">
                  <InlineEditable
                    sectionId={sectionId}
                    blockId={review.blockId}
                    settingKey="name"
                    value={review.name}
                  />
                </p>
                {review.city && (
                  <p className="text-[10px] text-muted-foreground mt-0.5">
                    <InlineEditable
                      sectionId={sectionId}
                      blockId={review.blockId}
                      settingKey="city"
                      value={review.city}
                    />
                  </p>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
