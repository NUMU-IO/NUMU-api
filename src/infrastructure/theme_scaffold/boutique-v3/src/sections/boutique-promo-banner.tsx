"use client";
import { useState } from "react";
import { Link, useLocale } from "@numueg/theme-sdk";
import { ArrowLeft, ShoppingBag, Sparkles } from "lucide-react";
import { applyImageTransform, asImageTransform, asString, localized, type SectionRenderProps } from "./_shared";

const BoutiquePromoBanner = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const badge = asString(s.badge_text);
  const headline = asString(s.headline) || localized(locale, "Special Offer", "عرض خاص");
  const subtitle = asString(s.subtitle) || localized(locale, "Shop our latest collection", "تسوّقي أحدث تشكيلاتنا");
  const ctaText = asString(s.cta_text) || localized(locale, "Shop Now", "تسوّقي الآن");
  const ctaLink = asString(s.cta_link) || "/products";
  const imageUrl = asString(s.image_url);
  const imageTransform = asImageTransform(s.image_url);

  const [imageLoading, setImageLoading] = useState(true);
  const [imageError, setImageError] = useState(!imageUrl);

  return (
    <section className="py-8">
      <div className="container mx-auto px-4">
        <div
          className="relative rounded-[var(--radius)] overflow-hidden border border-border"
          style={{
            background:
              "linear-gradient(135deg, hsl(340 75% 97%), hsl(320 60% 95%), hsl(340 75% 97%))",
          }}
        >
          {/* Decorative dots */}
          <div
            className="absolute inset-0 opacity-[0.03] pointer-events-none"
            style={{
              backgroundImage:
                "radial-gradient(hsl(340 75% 55%) 1px, transparent 1px)",
              backgroundSize: "20px 20px",
            }}
          />

          <div className="relative flex flex-col md:flex-row items-center gap-8 p-8 md:p-12">
            {/* Text content */}
            <div className="flex-1 text-center md:text-right">
              {badge && (
                <span
                  className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-full text-xs font-bold mb-4"
                  style={{
                    background: "hsl(340 75% 55% / 0.12)",
                    color: "hsl(340 75% 45%)",
                  }}
                >
                  <Sparkles size={12} />
                  {badge}
                </span>
              )}
              <h3 className="text-2xl md:text-3xl font-black text-foreground mb-3 leading-tight">
                {headline}
              </h3>
              <p className="text-muted-foreground text-sm leading-relaxed mb-6 max-w-md md:mr-0 mx-auto">
                {subtitle}
              </p>
              <Link
                to={ctaLink}
                className="inline-flex items-center gap-2 px-7 py-3.5 rounded-full text-white font-bold text-sm transition-all duration-300 hover:shadow-lg hover:scale-[1.02] active:scale-[0.98]"
                style={{
                  background:
                    "linear-gradient(135deg, hsl(340 75% 55%), hsl(320 60% 45%))",
                  boxShadow: "0 4px 14px hsl(340 75% 55% / 0.3)",
                }}
              >
                {ctaText}
                <ArrowLeft size={16} />
              </Link>
            </div>

            {/* Image */}
            <div
              className="w-52 h-52 md:w-60 md:h-60 rounded-[var(--radius)] overflow-hidden shrink-0 relative"
              style={{
                boxShadow: "0 8px 30px hsl(340 75% 55% / 0.15)",
              }}
            >
              {/* Pink border glow */}
              <div
                className="absolute -inset-[2px] rounded-[var(--radius)]"
                style={{
                  background:
                    "linear-gradient(135deg, hsl(340 75% 55% / 0.3), hsl(320 60% 45% / 0.15))",
                }}
              />
              <div className="absolute inset-0 rounded-[var(--radius)] overflow-hidden">
                {imageError ? (
                  <div className="w-full h-full store-gradient flex items-center justify-center">
                    <ShoppingBag className="h-16 w-16 text-white/60" />
                  </div>
                ) : (
                  <>
                    {imageLoading && (
                      <div className="absolute inset-0 bg-muted animate-pulse" />
                    )}
                    <img
                      src={imageUrl}
                      alt=""
                      className={`w-full h-full object-cover transition-opacity duration-300 ${
                        imageLoading ? "opacity-0" : "opacity-100"
                      }`}
                      style={applyImageTransform(imageTransform, "cover")}
                      onLoad={() => setImageLoading(false)}
                      onError={() => {
                        setImageLoading(false);
                        setImageError(true);
                      }}
                    />
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};

export default BoutiquePromoBanner;
