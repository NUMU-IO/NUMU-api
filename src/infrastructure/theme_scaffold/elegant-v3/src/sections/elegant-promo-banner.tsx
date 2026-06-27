"use client";
import { useState } from "react";
import { Link, useLocale } from "@numueg/theme-sdk";
import { ArrowLeft, ShoppingBag } from "lucide-react";
import { applyImageTransform, asImageTransform, asString, localized, type SectionRenderProps } from "./_shared";

const ElegantPromoBanner = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const badge = asString(s.badge_text);
  const headline = asString(s.headline) || localized(locale, "Special Offer", "عرض خاص");
  const subtitle = asString(s.subtitle) || localized(locale, "Shop our latest collection", "تسوق أحدث تشكيلاتنا");
  const ctaText = asString(s.cta_text) || localized(locale, "Shop Now", "تسوق الآن");
  const ctaLink = asString(s.cta_link) || "/products";
  const imageUrl = asString(s.image_url);
  const imageTransform = asImageTransform(s.image_url);

  const [imageLoading, setImageLoading] = useState(true);
  const [imageError, setImageError] = useState(!imageUrl);

  return (
    <section className="py-8">
      <div className="container mx-auto px-4">
        <div
          className="relative overflow-hidden rounded-lg border border-border"
          style={{ background: "hsl(var(--hero-bg))" }}
        >
          {/* Decorative corner accents */}
          <div
            className="absolute top-0 left-0 w-24 h-24 opacity-10 pointer-events-none"
            style={{
              background:
                "radial-gradient(circle at top left, hsl(30 50% 30%), transparent 70%)",
            }}
          />
          <div
            className="absolute bottom-0 right-0 w-24 h-24 opacity-10 pointer-events-none"
            style={{
              background:
                "radial-gradient(circle at bottom right, hsl(30 50% 30%), transparent 70%)",
            }}
          />

          <div className="flex flex-col md:flex-row items-center gap-8 p-8 md:p-12">
            {/* Text content */}
            <div className="flex-1 text-center md:text-right">
              {badge && (
                <span
                  className="inline-block px-4 py-1 rounded-sm text-xs font-semibold tracking-wide uppercase mb-4 border"
                  style={{
                    borderColor: "hsl(30 50% 30% / 0.3)",
                    color: "hsl(30 50% 30%)",
                    background: "hsl(30 50% 30% / 0.08)",
                  }}
                >
                  {badge}
                </span>
              )}
              <h3 className="text-2xl md:text-3xl font-bold mb-3 text-foreground leading-tight">
                {headline}
              </h3>
              <p className="text-muted-foreground text-sm leading-relaxed mb-6 max-w-md md:mr-0 mx-auto">
                {subtitle}
              </p>
              <Link
                to={ctaLink}
                className="inline-flex items-center gap-2 px-7 py-3 rounded-md text-sm font-semibold transition-all duration-200 hover:opacity-90 shadow-sm"
                style={{
                  background: "hsl(30 50% 30%)",
                  color: "hsl(45 30% 97%)",
                }}
              >
                {ctaText}
                <ArrowLeft size={15} />
              </Link>
            </div>

            {/* Image */}
            <div
              className="relative w-48 h-48 md:w-56 md:h-56 rounded-lg overflow-hidden shrink-0 border border-border"
              style={{ boxShadow: "0 4px 20px hsl(30 50% 30% / 0.1)" }}
            >
              {imageError ? (
                <div
                  className="w-full h-full flex items-center justify-center"
                  style={{
                    background:
                      "linear-gradient(135deg, hsl(30 50% 30%), hsl(30 40% 20%))",
                  }}
                >
                  <ShoppingBag className="h-16 w-16 text-white/60" />
                </div>
              ) : (
                <>
                  {imageLoading && (
                    <div className="absolute inset-0 bg-muted animate-pulse rounded-lg" />
                  )}
                  <img
                    src={imageUrl}
                    alt=""
                    className={`w-full h-full object-cover transition-opacity duration-500 ${
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
    </section>
  );
};

export default ElegantPromoBanner;
