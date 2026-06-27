"use client";
import { motion } from "framer-motion";
import { useLocale } from "@numueg/theme-sdk";
import { Star, Quote } from "lucide-react";
import { localized, type SectionRenderProps } from "./_shared";

/**
 * Modern testimonials — faithful port of the V2 in-tree ModernTestimonials
 * (numu-egyptian-bazaar/src/themes/modern/sections/testimonials/…).
 *
 * V2 imported `getReviewsFromSettings` from the shared base Testimonials
 * component; that one-screen helper is inlined here (same review_{n}_* schema)
 * so the section is self-contained on the V3 SDK. All markup classNames are
 * kept verbatim.
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

const ModernTestimonials = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const title = s.title ?? localized(locale, "What our customers say", "رأي عملاءنا");
  const reviews = getReviewsFromSettings(s);

  if (reviews.length === 0) return null;

  return (
    <section className="py-12 bg-[hsl(var(--hero-bg))]">
      <div className="container mx-auto px-4">
        <div className="text-center mb-8">
          <h2 className="text-2xl font-bold text-[hsl(var(--foreground))]">
            {title}
          </h2>
          <div className="mt-2 mx-auto w-12 h-1 rounded-full store-gradient" />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {reviews.map((review, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, y: 24 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.12, duration: 0.4 }}
              className="relative bg-[hsl(var(--card))] rounded-2xl border border-[hsl(var(--border))] p-6 shadow-sm hover:shadow-md transition-shadow duration-200"
            >
              {/* Decorative quote icon */}
              <Quote
                size={28}
                className="absolute top-4 left-4 text-[hsl(var(--primary)/0.1)]"
              />

              {/* Stars */}
              <div className="flex items-center gap-1 mb-4">
                {Array.from({ length: review.rating }).map((_, j) => (
                  <Star
                    key={j}
                    size={15}
                    className="fill-[hsl(var(--primary))] text-[hsl(var(--primary))]"
                  />
                ))}
              </div>

              {/* Review text */}
              <p className="text-sm text-[hsl(var(--foreground))] leading-relaxed mb-5">
                "{review.text}"
              </p>

              {/* Reviewer info */}
              <div className="flex items-center gap-3 pt-4 border-t border-[hsl(var(--border))]">
                <div className="w-9 h-9 rounded-full store-gradient flex items-center justify-center shadow-sm">
                  <span className="text-xs font-bold text-white">
                    {review.name[0]}
                  </span>
                </div>
                <div>
                  <p className="text-sm font-bold text-[hsl(var(--foreground))]">
                    {review.name}
                  </p>
                  {review.city && (
                    <p className="text-[11px] text-[hsl(var(--muted-foreground))]">
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
};

export default ModernTestimonials;
