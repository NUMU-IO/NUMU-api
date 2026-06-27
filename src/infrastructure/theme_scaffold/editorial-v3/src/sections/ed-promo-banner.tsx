"use client";
import { useState } from "react";
import { Link, useLocale } from "@numueg/theme-sdk";
import { ArrowLeft, ShoppingBag } from "lucide-react";
import { applyImageTransform, asImageTransform, asString, localized, type SectionRenderProps } from "./_shared";

/**
 * Editorial promo banner — faithful port of V2
 * themes/editorial/sections/promo-banner/EdPromoBanner.tsx.
 *
 * Dark `ed-dark` band, right-aligned copy, square product image with a
 * ShoppingBag fallback when no image is set (V2 used a PLACEHOLDER_HERO
 * literal; on V3 the image is fully editable, so we fall back to the icon
 * tile rather than baking a placeholder URL into the bundle).
 */
export default function EdPromoBanner({ instance }: SectionRenderProps) {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const badge = asString(s.badge_text);
  const headline = asString(s.headline) || localized(locale, "Special Offer", "عرض خاص");
  const subtitle = asString(s.subtitle) || localized(locale, "Shop our latest collection", "تسوّق أحدث تشكيلاتنا");
  const ctaText = asString(s.cta_text) || localized(locale, "Shop Now", "تسوّق الآن");
  const ctaLink = asString(s.cta_link) || "/products";
  const imageUrl = asString(s.image_url);
  const imageTransform = asImageTransform(s.image_url);

  const [imageLoading, setImageLoading] = useState(true);
  const [imageError, setImageError] = useState(!imageUrl);

  return (
    <section className="py-6">
      <div className="container mx-auto px-4">
        <div className="relative overflow-hidden bg-[hsl(var(--ed-dark))] border border-border">
          <div className="flex flex-col md:flex-row items-center gap-6 p-6 md:p-10">
            {/* Text */}
            <div className="flex-1 text-center md:text-right">
              {badge && (
                <span className="inline-block px-3 py-1 border border-white/20 text-[0.65rem] font-bold uppercase tracking-wider text-white/80 mb-3">
                  {badge}
                </span>
              )}
              <h3 className="text-2xl md:text-3xl font-black text-white uppercase tracking-wide mb-2">
                {headline}
              </h3>
              <p className="text-white/60 text-sm mb-5">{subtitle}</p>
              <Link to={ctaLink} className="inline-flex items-center gap-2 ed-btn">
                {ctaText}
                <ArrowLeft size={14} />
              </Link>
            </div>

            {/* Image */}
            <div className="relative w-48 h-48 md:w-56 md:h-56 overflow-hidden shrink-0 border border-white/10">
              {imageError ? (
                <div className="w-full h-full bg-[hsl(var(--ed-green))] flex items-center justify-center">
                  <ShoppingBag className="h-16 w-16 text-white/60" />
                </div>
              ) : (
                <>
                  {imageLoading && (
                    <div className="absolute inset-0 bg-white/5 animate-pulse" />
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
    </section>
  );
}
