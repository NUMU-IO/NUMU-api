"use client";
import { motion } from "framer-motion";
import { Star } from "lucide-react";
import { useLocale } from "@numueg/theme-sdk";
import { asString, asNumber, localized, type SectionRenderProps } from "./_shared";

const HEADING_SHADOW = "0 1px 0 hsl(35 30% 100% / 0.5)";

interface TestimonialReview {
  name: string;
  city: string;
  text: string;
  rating: number;
}

/** Inlined from the V2 shared Testimonials helper. */
function getReviewsFromSettings(s: Record<string, unknown>): TestimonialReview[] {
  const reviews: TestimonialReview[] = [];
  for (let i = 1; i <= 3; i++) {
    const name = asString(s[`review_${i}_name`]);
    const text = asString(s[`review_${i}_text`]);
    if (name && text) {
      reviews.push({
        name,
        city: asString(s[`review_${i}_city`]),
        text,
        rating: asNumber(s[`review_${i}_rating`], 5),
      });
    }
  }
  return reviews;
}

const SkeuTestimonials = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const title = asString(s.title) || localized(locale, "What our customers say ⭐", "رأي عملاءنا ⭐");
  const reviews = getReviewsFromSettings(s);

  if (reviews.length === 0) return null;

  return (
    <section className="py-8 skeu-section">
      <div className="container mx-auto px-4">
        <h2
          className="text-xl font-bold text-center mb-6"
          style={{ textShadow: HEADING_SHADOW }}
        >
          {title}
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {reviews.map((review, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.1 }}
              className="skeu-card rounded-2xl p-5"
            >
              <div className="relative z-[1]">
                <div className="flex items-center gap-1 mb-3">
                  {Array.from({ length: review.rating }).map((_, j) => (
                    <Star
                      key={j}
                      size={14}
                      className="fill-[hsl(var(--warning))] text-[hsl(var(--warning))] skeu-star"
                    />
                  ))}
                </div>
                <p className="text-sm text-foreground leading-relaxed mb-3">
                  "{review.text}"
                </p>
                <div className="flex items-center gap-2">
                  <div className="w-8 h-8 rounded-full skeu-btn flex items-center justify-center">
                    <span className="text-xs font-bold text-white">
                      {review.name[0]}
                    </span>
                  </div>
                  <div>
                    <p className="text-xs font-bold">{review.name}</p>
                    <p className="text-[10px] text-muted-foreground">
                      {review.city}
                    </p>
                  </div>
                </div>
              </div>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
};

export default SkeuTestimonials;
