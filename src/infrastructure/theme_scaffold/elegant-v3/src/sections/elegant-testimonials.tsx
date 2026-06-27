"use client";
import { motion } from "framer-motion";
import { Star, Quote } from "lucide-react";
import { useLocale } from "@numueg/theme-sdk";
import { asString, localized, type SectionRenderProps } from "./_shared";

interface TestimonialReview {
  name: string;
  city: string;
  text: string;
  rating: number;
}

/** Reads up to 3 reviews from the section settings (review_N_* keys). */
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

const ElegantTestimonials = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const title = asString(s.title) || localized(locale, "What Our Clients Say", "رأي عملائنا");
  const reviews = getReviewsFromSettings(s);

  if (reviews.length === 0) return null;

  return (
    <section
      className="py-12"
      style={{ background: "hsl(var(--hero-bg))" }}
    >
      <div className="container mx-auto px-4">
        {/* Section header */}
        <div className="text-center mb-8">
          <h2 className="text-2xl font-bold text-foreground">{title}</h2>
          <div
            className="mx-auto mt-3 w-12 h-0.5 rounded-full"
            style={{ background: "hsl(30 50% 30%)" }}
          />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {reviews.map((review, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, y: 24 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.12, duration: 0.4 }}
              className="relative bg-card rounded-lg border border-border p-6"
              style={{
                boxShadow: "0 2px 12px hsl(30 50% 30% / 0.06)",
              }}
            >
              {/* Decorative quote icon */}
              <Quote
                size={28}
                className="absolute top-4 left-4 opacity-[0.08]"
                style={{ color: "hsl(30 50% 30%)" }}
              />

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
              <p className="text-sm text-foreground leading-relaxed mb-5 italic">
                "{review.text}"
              </p>

              {/* Divider */}
              <div
                className="w-8 h-px mb-4"
                style={{ background: "hsl(30 50% 30% / 0.2)" }}
              />

              {/* Author */}
              <div className="flex items-center gap-3">
                <div
                  className="w-9 h-9 rounded-full flex items-center justify-center"
                  style={{
                    background:
                      "linear-gradient(135deg, hsl(30 50% 30%), hsl(30 40% 20%))",
                  }}
                >
                  <span className="text-xs font-semibold text-white">
                    {review.name[0]}
                  </span>
                </div>
                <div>
                  <p className="text-sm font-semibold text-foreground">
                    {review.name}
                  </p>
                  {review.city && (
                    <p className="text-[11px] text-muted-foreground">
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

export default ElegantTestimonials;
