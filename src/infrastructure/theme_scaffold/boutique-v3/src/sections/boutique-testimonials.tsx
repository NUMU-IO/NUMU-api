"use client";
import { useLocale } from "@numueg/theme-sdk";
import { motion } from "framer-motion";
import { Star, Quote } from "lucide-react";
import { asString, localized, type SectionRenderProps } from "./_shared";

interface TestimonialReview {
  name: string;
  city: string;
  text: string;
  rating: number;
}

/** Mirrors the V2 shared Testimonials.getReviewsFromSettings helper. */
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

const BoutiqueTestimonials = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const title = asString(s.title) || localized(locale, "What Our Customers Say ⭐", "رأي عملاءنا ⭐");
  const reviews = getReviewsFromSettings(s);

  if (reviews.length === 0) return null;

  return (
    <section
      className="py-10"
      style={{
        background:
          "linear-gradient(180deg, hsl(340 60% 97%), hsl(330 30% 98%))",
      }}
    >
      <div className="container mx-auto px-4">
        {/* Section heading */}
        <div className="text-center mb-8">
          <h2 className="text-xl font-bold text-foreground">{title}</h2>
          <div
            className="mx-auto mt-2 w-12 h-1 rounded-full"
            style={{
              background:
                "linear-gradient(90deg, hsl(340 75% 55%), hsl(320 60% 45%))",
            }}
          />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
          {reviews.map((review, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, y: 24 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.12, duration: 0.4 }}
              className="bg-card rounded-[var(--radius)] border border-border p-6 relative group hover:shadow-md transition-shadow duration-300"
            >
              {/* Decorative quote icon */}
              <div className="absolute top-4 left-4">
                <Quote
                  size={28}
                  className="opacity-[0.08]"
                  style={{ color: "hsl(340 75% 55%)" }}
                />
              </div>

              {/* Stars */}
              <div className="flex items-center gap-1 mb-4">
                {Array.from({ length: review.rating }).map((_, j) => (
                  <Star
                    key={j}
                    size={14}
                    className="fill-[hsl(var(--warning))] text-[hsl(var(--warning))]"
                  />
                ))}
              </div>

              {/* Review text */}
              <p className="text-sm text-foreground leading-relaxed mb-4">
                "{review.text}"
              </p>

              {/* Reviewer info */}
              <div className="flex items-center gap-3 pt-3 border-t border-border/60">
                <div
                  className="w-9 h-9 rounded-full flex items-center justify-center"
                  style={{
                    background:
                      "linear-gradient(135deg, hsl(340 75% 55%), hsl(320 60% 45%))",
                  }}
                >
                  <span className="text-xs font-bold text-white">
                    {review.name[0]}
                  </span>
                </div>
                <div>
                  <p className="text-xs font-bold text-foreground">
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
};

export default BoutiqueTestimonials;
