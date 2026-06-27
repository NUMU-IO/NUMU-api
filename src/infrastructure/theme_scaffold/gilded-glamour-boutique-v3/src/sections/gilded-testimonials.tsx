"use client";

import { motion } from "framer-motion";
import { Star, Quote } from "lucide-react";
import { useLocale, useResolvedSettings } from "@numueg/theme-sdk";
import {
  asNumber,
  asString,
  localized,
  readBlocksWithIds,
  useDemo,
  demoOrPlaceholder,
  type SectionRenderProps,
} from "./_shared";
import { InlineEditable } from "./_inline-editable";

/**
 * gilded-testimonials — faithful V3 port of the V2 GildedTestimonials
 * (numu-egyptian-bazaar/src/themes/gilded-glamour-boutique/sections/testimonials/GildedTestimonials.tsx).
 *
 * Warm `bg-card` band, centered 10px/0.3em uppercase eyebrow "Testimonials"
 * over a Montserrat-uppercase title, then a 3-up grid of sharp-edged bordered
 * review cards. Each card: a faint gold quote glyph top-left, a row of filled
 * gold stars (count = rating), the quote text, an 8px hairline divider, a gold
 * square avatar with the reviewer's initial, the name and the city. All V2
 * className strings kept verbatim, only the gold accent swapped from
 * `hsl(var(--gold))` to `var(--gilded-gold)` so the merchant's Accent picker
 * repaints it.
 *
 * Engine-wired: useResolvedSettings (global tokens + dynamic sources + draft
 * preview), a `testimonial` block list read via readBlocks (so the merchant
 * can add/remove/reorder reviews instead of the V2 fixed-3 review_N_* settings),
 * and InlineEditable on every text field. Falls back to three V2 default
 * reviews in demo/marketplace-preview mode, and to neutral blank placeholders
 * on a real store when nothing is configured.
 */

interface TestimonialReview {
  quote: string;
  name: string;
  city: string;
  rating: number;
  blockId?: string;
}

export default function GildedTestimonials({ instance, sectionId }: SectionRenderProps) {
  const s = useResolvedSettings(instance);
  const locale = useLocale();
  const demo = useDemo();

  const title =
    asString(s.title) || localized(locale, "What Our Clients Say", "آراء عملائنا");

  // Merchant-configured reviews via `testimonial` blocks (add/remove/reorder
  // in the editor) — the V3 replacement for V2's fixed review_1/2/3_* settings.
  // Use the real editor block id (readBlocksWithIds) so InlineEditable can match
  // the block — a synthetic positional id silently no-ops in the editor.
  const blockReviews: TestimonialReview[] = readBlocksWithIds(instance, "testimonial").map(
    ({ id, settings: r }) => ({
      quote: asString(r.quote),
      name: asString(r.name),
      city: asString(r.city),
      rating: asNumber(r.rating, 5),
      blockId: id,
    }),
  );

  // V2 default reviews (bilingual) — shown only in demo/marketplace preview;
  // on a real store with nothing configured they become blank placeholders.
  const defaultReviews: TestimonialReview[] = [
    {
      quote: localized(
        locale,
        "Absolutely exquisite craftsmanship. Each piece feels like a true heirloom.",
        "حرفية رائعة بكل المقاييس. كل قطعة تبدو كإرث ثمين حقيقي.",
      ),
      name: localized(locale, "Yasmine Fouad", "ياسمين فؤاد"),
      city: localized(locale, "Cairo", "القاهرة"),
      rating: 5,
    },
    {
      quote: localized(
        locale,
        "The attention to detail is unmatched. My wardrobe has never felt so refined.",
        "الاهتمام بالتفاصيل لا يُضاهى. لم تكن خزانة ملابسي بهذه الأناقة من قبل.",
      ),
      name: localized(locale, "Karim Saleh", "كريم صالح"),
      city: localized(locale, "Alexandria", "الإسكندرية"),
      rating: 5,
    },
    {
      quote: localized(
        locale,
        "Timeless elegance with impeccable quality. Worth every single penny.",
        "أناقة خالدة وجودة لا تشوبها شائبة. تستحق كل قرش.",
      ),
      name: localized(locale, "Nourhan Adel", "نورهان عادل"),
      city: localized(locale, "Giza", "الجيزة"),
      rating: 5,
    },
  ];

  const usingBlocks = blockReviews.length > 0;
  const reviews = usingBlocks
    ? blockReviews
    : demoOrPlaceholder(demo, defaultReviews);

  if (reviews.length === 0) return null;

  return (
    <section className="py-12 md:py-20 lg:py-32 bg-card" data-gilded-section={sectionId}>
      <div className="container mx-auto px-4">
        {/* Section heading */}
        <div className="text-center mb-10 md:mb-16">
          <p className="text-[10px] sm:text-xs tracking-[0.2em] sm:tracking-[0.3em] uppercase text-muted-foreground mb-3 md:mb-4">
            {localized(locale, "Testimonials", "شهادات العملاء")}
          </p>
          <h2 className="gld-heading text-xl sm:text-2xl md:text-4xl font-bold text-foreground uppercase tracking-[0.04em] sm:tracking-[0.08em]">
            <InlineEditable sectionId={sectionId} settingKey="title" value={title} />
          </h2>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 md:gap-6 lg:gap-8">
          {reviews.map((review, i) => {
            const rating = Math.max(0, Math.min(5, Math.round(review.rating || 0)));
            const initial = review.name ? review.name.trim().charAt(0) : "";
            // On a fresh store a placeholder card has neither name nor quote —
            // suppress the 5-star row + empty gold avatar so we don't show 5
            // stars over blank text.
            const hasContent = Boolean(review.name || review.quote);
            return (
              <motion.div
                key={review.blockId ?? i}
                initial={{ opacity: 0, y: 24 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ delay: i * 0.12, duration: 0.4 }}
                className="border border-border p-5 sm:p-6 md:p-8 relative group hover:border-[var(--gilded-gold)]/30 transition-colors duration-300"
              >
                {/* Decorative quote glyph */}
                <div className="absolute top-6 start-6">
                  <Quote
                    size={28}
                    className="text-[var(--gilded-gold)]/10 rtl:-scale-x-100"
                    aria-hidden="true"
                  />
                </div>

                {/* Stars */}
                {hasContent && (
                  <div className="flex items-center gap-1 mb-6">
                    {Array.from({ length: rating }).map((_, j) => (
                      <Star
                        key={j}
                        size={14}
                        className="fill-[var(--gilded-gold)] text-[var(--gilded-gold)]"
                        aria-hidden="true"
                      />
                    ))}
                  </div>
                )}

                {/* Review text */}
                <p className="text-sm text-foreground leading-relaxed mb-6">
                  &ldquo;
                  <InlineEditable
                    sectionId={sectionId}
                    blockId={review.blockId}
                    settingKey="quote"
                    value={review.quote}
                    multiline
                  />
                  &rdquo;
                </p>

                {/* Reviewer info */}
                <div className="flex items-center gap-3 pt-4 border-t border-border/60">
                  {hasContent && (
                    <div className="w-9 h-9 bg-[var(--gilded-gold)] flex items-center justify-center shrink-0">
                      <span className="text-xs font-bold text-foreground">{initial}</span>
                    </div>
                  )}
                  <div>
                    <p className="text-xs font-bold text-foreground tracking-[0.05em]">
                      <InlineEditable
                        sectionId={sectionId}
                        blockId={review.blockId}
                        settingKey="name"
                        value={review.name}
                      />
                    </p>
                    {review.city && (
                      <p className="text-[10px] text-muted-foreground">
                        <InlineEditable
                          sectionId={sectionId}
                          blockId={review.blockId}
                          settingKey="city"
                          value={review.city}
                        />
                      </p>
                    )}
                  </div>
                </div>
              </motion.div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
