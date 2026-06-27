"use client";
import { useState } from "react";
import { Link, useLocale } from "@numueg/theme-sdk";
import { ArrowLeft, ShoppingBag } from "lucide-react";
import { applyImageTransform, asImageTransform, asString, asImageUrl, localized, type SectionRenderProps } from "./_shared";

const HEADING_SHADOW = "0 1px 0 hsl(35 30% 100% / 0.5)";

const SkeuPromoBanner = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const badge = asString(s.badge_text);
  const headline = asString(s.headline) || localized(locale, "Special offer", "عرض خاص");
  const subtitle = asString(s.subtitle) || localized(locale, "Shop our latest collection", "تسوّق أحدث تشكيلة");
  const ctaText = asString(s.cta_text) || localized(locale, "Shop now", "تسوق الآن");
  const ctaLink = asString(s.cta_link) || "/products";
  const imageUrl = asImageUrl(s.image_url);
  const imageTransform = asImageTransform(s.image_url);

  const [imageError, setImageError] = useState(!imageUrl);

  return (
    <section className="py-6">
      <div className="container mx-auto px-4">
        <div className="skeu-card skeu-elevated rounded-2xl overflow-hidden">
          <div className="flex flex-col md:flex-row items-center gap-6 p-6 md:p-10 relative z-[1]">
            <div className="flex-1 text-center md:text-right">
              {badge && (
                <span className="skeu-badge px-3 py-1 rounded-lg text-xs font-bold mb-3 inline-block">
                  {badge}
                </span>
              )}
              <h3
                className="text-2xl md:text-3xl font-black mb-2"
                style={{ textShadow: HEADING_SHADOW }}
              >
                {headline}
              </h3>
              <p className="text-muted-foreground text-sm mb-4">{subtitle}</p>
              <Link
                to={ctaLink}
                className="inline-flex items-center gap-2 px-6 py-3 rounded-xl skeu-btn text-sm"
              >
                {ctaText}
                <ArrowLeft size={16} />
              </Link>
            </div>
            <div className="w-48 h-48 md:w-56 md:h-56 skeu-img-frame rounded-xl overflow-hidden shrink-0">
              {imageError || !imageUrl ? (
                <div className="w-full h-full store-gradient flex items-center justify-center">
                  <ShoppingBag className="h-16 w-16 text-white/60" />
                </div>
              ) : (
                <img
                  src={imageUrl}
                  alt=""
                  className="w-full h-full object-cover"
                  style={applyImageTransform(imageTransform, "cover")}
                  onError={() => setImageError(true)}
                />
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};

export default SkeuPromoBanner;
