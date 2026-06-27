"use client";
import { motion } from "framer-motion";
import { Star } from "lucide-react";
import { useLocale } from "@numueg/theme-sdk";
import { asString, localized, type SectionRenderProps } from "./_shared";

/**
 * Tech Wave testimonials — faithful port of the V2 in-tree
 * numu-egyptian-bazaar/src/themes/tech-wave/sections/testimonials/TechWaveTestimonials.tsx
 * (neon glass review cards with star rating + gradient avatar), re-plumbed
 * on the V3 SDK.
 *
 * The V2 `getReviewsFromSettings` helper (lived in the shared Testimonials
 * section) is inlined here so reviews are read from the numbered
 * `review_N_*` settings exactly as in V2.
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

const TechWaveTestimonials = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const title = asString(s.title) || localized(locale, "What our customers say ⭐", "رأي عملاءنا ⭐");
  const reviews = getReviewsFromSettings(s);

  if (reviews.length === 0) return null;

  return (
    <section className="py-8 tw-section">
      <div className="container mx-auto px-4">
        <h2 className="text-xl font-bold text-center mb-6 text-[hsl(var(--foreground))]">
          {title}
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {reviews.map((review, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.1 }}
              className="tw-card rounded-2xl p-5"
            >
              <div className="flex items-center gap-1 mb-3">
                {Array.from({ length: review.rating }).map((_, j) => (
                  <Star
                    key={j}
                    size={14}
                    className="fill-[hsl(var(--warning))] text-[hsl(var(--warning))] tw-star"
                  />
                ))}
              </div>
              <p className="text-sm text-[hsl(var(--foreground))] leading-relaxed mb-3">
                &ldquo;{review.text}&rdquo;
              </p>
              <div className="flex items-center gap-2">
                <div
                  className="w-8 h-8 rounded-full flex items-center justify-center"
                  style={{
                    background:
                      "linear-gradient(135deg, hsl(195 100% 50%), hsl(260 100% 69%))",
                    boxShadow: "0 0 8px hsl(195 100% 50% / 0.3)",
                  }}
                >
                  <span className="text-xs font-bold text-[hsl(220,40%,7%)]">
                    {review.name[0]}
                  </span>
                </div>
                <div>
                  <p className="text-xs font-bold text-[hsl(var(--foreground))]">
                    {review.name}
                  </p>
                  <p className="text-[10px] text-[hsl(var(--muted-foreground))]">
                    {review.city}
                  </p>
                </div>
              </div>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
};

export default TechWaveTestimonials;
