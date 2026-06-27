"use client";
import { Link, useLocale } from "@numueg/theme-sdk";
import { ArrowLeft } from "lucide-react";
import { applyImageTransform, asImageTransform, asString, localized, type SectionRenderProps } from "./_shared";

const NBPromoBanner = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const badge = asString(s.badge_text) || localized(locale, "Limited offer 🔥", "عرض محدود 🔥");
  const headline = asString(s.headline) || localized(locale, "25% off all accessories", "خصم ٢٥٪ على كل الإكسسوارات");
  const subtitle = asString(s.subtitle) || localized(locale, "Offer runs until the end of the month. Don't miss out!", "العرض ساري لنهاية الشهر. متفوتش الفرصة!");
  const ctaText = asString(s.cta_text) || localized(locale, "Shop accessories", "تسوق الإكسسوارات");
  const ctaLink = asString(s.cta_link, "/products?category=accessories");
  const imageUrl = s.image_url as string | undefined;
  const imageTransform = asImageTransform(s.image_url);
  const diagonalText = s.diagonal_text as string | undefined;

  return (
    <section className="py-8">
      <div className="container mx-auto px-4">
        <div className="nb-card rounded-xl overflow-hidden relative">
          <div className="flex flex-col md:flex-row items-center gap-6 p-6 md:p-10">
            <div className="flex-1 text-center md:text-right">
              {badge && (
                <span className="nb-badge-pink px-3 py-1 rounded text-xs inline-block mb-3">
                  {badge}
                </span>
              )}
              <h3 className="text-2xl md:text-4xl font-black mb-3">
                {headline}
              </h3>
              <p className="text-muted-foreground text-sm mb-5 font-medium">
                {subtitle}
              </p>
              <Link
                to={ctaLink}
                className="inline-flex items-center gap-2 px-6 py-3 rounded-lg nb-btn-accent text-sm"
              >
                {ctaText} <ArrowLeft size={16} />
              </Link>
            </div>
            {imageUrl && (
              <div className="w-48 h-48 md:w-56 md:h-56 nb-img-frame rounded-xl shrink-0 rotate-2">
                <img
                  src={imageUrl}
                  alt=""
                  className="w-full h-full object-cover"
                  style={applyImageTransform(imageTransform, "cover")}
                />
              </div>
            )}
          </div>
          {/* Diagonal banner */}
          {diagonalText && (
            <div className="absolute -bottom-2 -left-4 -right-4 nb-diagonal-banner py-2 text-center text-sm">
              {diagonalText}
            </div>
          )}
        </div>
      </div>
    </section>
  );
};

export default NBPromoBanner;
