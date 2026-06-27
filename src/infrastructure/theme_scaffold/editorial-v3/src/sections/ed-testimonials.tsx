"use client";
import { motion } from "framer-motion";
import { Star } from "lucide-react";
import { useLocale } from "@numueg/theme-sdk";
import { asString, localized, type SectionRenderProps } from "./_shared";

/**
 * Editorial testimonials — faithful port of V2
 * themes/editorial/sections/testimonials/EdTestimonials.tsx.
 *
 * Reviews are read from per-review settings (review_N_name / _text / _city /
 * _rating) exactly as the V2 shared `getReviewsFromSettings` helper did — that
 * helper is inlined here so the section is self-contained in the bundle. The
 * `ed-card` shell, filled-foreground stars and avatar treatment are verbatim.
 */

interface TestimonialReview {
  name: string;
  city: string;
  text: string;
  rating: number;
}

function getReviewsFromSettings(s: Record<string, unknown>): TestimonialReview[] {
  const reviews: TestimonialReview[] = [];
  for (let i = 1; i <= 3; i++) {
    const name = s[`review_${i}_name`] as string | undefined;
    const text = s[`review_${i}_text`] as string | undefined;
    if (name && text) {
      reviews.push({
        name,
        city: (s[`review_${i}_city`] as string) ?? "",
        text,
        rating: (s[`review_${i}_rating`] as number) ?? 5,
      });
    }
  }
  return reviews;
}

export default function EdTestimonials({ instance }: SectionRenderProps) {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const title = asString(s.title) || localized(locale, "What Our Customers Say", "آراء العملاء");
  const reviews = getReviewsFromSettings(s);

  if (reviews.length === 0) return null;

  return (
    <section className="py-10 bg-[hsl(var(--background))]">
      <div className="container mx-auto px-4">
        <h2 className="text-xs font-bold uppercase tracking-widest text-center text-muted-foreground mb-8">
          {title}
        </h2>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {reviews.map((review, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.1 }}
              className="ed-card p-5"
            >
              {/* Stars */}
              <div className="flex items-center gap-1 mb-3">
                {Array.from({ length: review.rating }).map((_, j) => (
                  <Star
                    key={j}
                    size={14}
                    className="fill-foreground text-foreground"
                  />
                ))}
              </div>

              {/* Review text */}
              <p className="text-sm text-foreground leading-relaxed mb-4">
                "{review.text}"
              </p>

              {/* Reviewer */}
              <div className="flex items-center gap-2">
                <div className="w-8 h-8 rounded-full bg-[hsl(var(--ed-green))] flex items-center justify-center">
                  <span className="text-xs font-bold text-white">
                    {review.name[0]}
                  </span>
                </div>
                <div>
                  <p className="text-xs font-bold uppercase tracking-wide">
                    {review.name}
                  </p>
                  {review.city && (
                    <p className="text-[10px] text-muted-foreground">
                      {review.city}
                    </p>
                  )}
                </div>
              </div>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
