"use client";
import { motion } from "framer-motion";
import { useLocale } from "@numueg/theme-sdk";
import { Star } from "lucide-react";
import { asNumber, asString, localized, type SectionRenderProps } from "./_shared";

interface Review {
  name: string;
  city: string;
  text: string;
  rating: number;
}

function getReviewsFromSettings(s: Record<string, unknown>): Review[] {
  const reviews: Review[] = [];
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

const bgColors = ["bg-primary", "bg-secondary", "bg-accent"];

const NBTestimonials = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const title = asString(s.title) || localized(locale, "What our customers say ⭐", "رأي عملاءنا ⭐");
  const reviews = getReviewsFromSettings(s);

  if (reviews.length === 0) return null;

  return (
    <section className="py-10">
      <div className="container mx-auto px-4">
        <h2 className="text-2xl font-black text-center mb-8">{title}</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {reviews.map((review, i) => {
            const bg = bgColors[i % bgColors.length];
            return (
              <motion.div
                key={i}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.1 }}
                className={`nb-card rounded-xl p-5 ${bg}`}
              >
                <div className="flex items-center gap-1 mb-3">
                  {Array.from({ length: review.rating }).map((_, j) => (
                    <Star
                      key={j}
                      size={14}
                      className="fill-foreground text-foreground nb-star"
                    />
                  ))}
                </div>
                <p className="text-sm font-medium leading-relaxed mb-4">
                  "{review.text}"
                </p>
                <div className="flex items-center gap-2">
                  <div className="w-8 h-8 rounded-full border-[2px] border-foreground flex items-center justify-center bg-card font-black text-xs">
                    {review.name[0]}
                  </div>
                  <div>
                    <p className="text-xs font-black">{review.name}</p>
                    {review.city && (
                      <p className="text-[10px] text-muted-foreground font-medium">
                        {review.city}
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
};

export default NBTestimonials;
