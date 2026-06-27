"use client";
import { Star } from "lucide-react";
import { useLocale } from "@numueg/theme-sdk";
import { asString, asNumber, localized, type SectionRenderProps } from "./_shared";

interface TestimonialReview {
  name: string;
  city: string;
  text: string;
  rating: number;
}

/** Inlined from V2 themes/sections/testimonials/Testimonials.ts getReviewsFromSettings. */
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
        rating: asNumber(s[`review_${i}_rating`], 5) || 5,
      });
    }
  }
  return reviews;
}

const KGTestimonials = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const title = asString(s.title) || localized(locale, "WHAT THEY SAY", "آراء العملاء");
  const reviews = getReviewsFromSettings(s);

  if (reviews.length === 0) return null;

  return (
    <section
      className="kg-testimonials"
      style={{ background: "#fcfbf7", padding: "32px 0" }}
    >
      <div style={{ maxWidth: "1280px", margin: "0 auto", padding: "0 16px" }}>
        <h2
          className="kg-heading"
          style={{
            fontSize: "1.25rem",
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "-0.02em",
            color: "#121212",
            textAlign: "center",
            margin: "0 0 24px",
          }}
        >
          {title}
        </h2>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
            gap: "8px",
          }}
        >
          {reviews.map((review, i) => (
            <div
              key={i}
              style={{
                padding: "20px",
                background: "transparent",
                border: "none",
                boxShadow: "none",
                borderRadius: 0,
              }}
            >
              {/* Stars */}
              <div
                style={{
                  display: "flex",
                  gap: "2px",
                  marginBottom: "12px",
                }}
              >
                {Array.from({ length: review.rating }).map((_, j) => (
                  <Star
                    key={j}
                    size={13}
                    fill="#121212"
                    color="#121212"
                    strokeWidth={0}
                  />
                ))}
              </div>

              <p
                style={{
                  fontSize: "0.8125rem",
                  color: "#121212",
                  lineHeight: 1.6,
                  margin: "0 0 16px",
                }}
              >
                "{review.text}"
              </p>

              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <div
                  style={{
                    width: "28px",
                    height: "28px",
                    background: "#121212",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    borderRadius: 0,
                  }}
                >
                  <span
                    style={{
                      fontSize: "0.6875rem",
                      fontWeight: 700,
                      color: "#fcfbf7",
                    }}
                  >
                    {review.name[0]}
                  </span>
                </div>
                <div>
                  <p
                    style={{
                      fontSize: "0.6875rem",
                      fontWeight: 700,
                      textTransform: "uppercase",
                      letterSpacing: "0.04em",
                      color: "#121212",
                      margin: 0,
                    }}
                  >
                    {review.name}
                  </p>
                  {review.city && (
                    <p
                      style={{
                        fontSize: "0.625rem",
                        color: "rgba(18,18,18,0.5)",
                        margin: 0,
                      }}
                    >
                      {review.city}
                    </p>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};

export default KGTestimonials;
