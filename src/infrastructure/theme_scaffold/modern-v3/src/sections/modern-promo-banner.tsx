"use client";
import { useState } from "react";
import { Link, useLocale } from "@numueg/theme-sdk";
import { ArrowLeft, ShoppingBag, Sparkles } from "lucide-react";
import {
  applyImageTransform,
  asImageTransform,
  localized,
  type SectionRenderProps,
} from "./_shared";

/**
 * Read an image-picker URL. The value is a plain URL string for legacy/no-crop
 * settings, or an `{ url, alt, transform }` object once the merchant frames the
 * image in the editor. Returns "" for any other shape so the bag fallback still
 * renders exactly as before.
 */
function imgUrl(v: unknown): string {
  if (typeof v === "string") return v;
  if (v && typeof v === "object") {
    const r = v as Record<string, unknown>;
    if (typeof r.url === "string") return r.url;
    if (typeof r.src === "string") return r.src;
  }
  return "";
}

/**
 * Modern promo banner — faithful port of the V2 in-tree ModernPromoBanner
 * (numu-egyptian-bazaar/src/themes/modern/sections/promo-banner/…).
 *
 * Kept verbatim: the teal accent bar, the badge + headline + subtitle + CTA,
 * the framed image with loading shimmer and the ShoppingBag fallback. V2 used a
 * `PLACEHOLDER_HERO` import as the default image; in V3 (no shared placeholder
 * module) an empty image just shows the store-gradient + bag fallback, which is
 * the same end state V2 reached on a broken image.
 */
const ModernPromoBanner = ({ instance }: SectionRenderProps) => {
  const s = instance.settings ?? {};
  const locale = useLocale();
  const badge = s.badge_text ?? "";
  const headline = s.headline ?? localized(locale, "Special Offer", "عرض خاص");
  const subtitle = s.subtitle ?? localized(locale, "Shop our latest collection", "تسوق أحدث تشكيلاتنا");
  const ctaText = s.cta_text ?? localized(locale, "Shop Now", "تسوق الآن");
  const ctaLink = s.cta_link ?? "/products";
  const imageUrl = imgUrl(s.image_url);
  // Non-destructive focal/zoom/rotation the merchant framed on the banner image.
  // Undefined → the <img> renders exactly as before.
  const imageTransform = asImageTransform(s.image_url);

  const [imageLoading, setImageLoading] = useState(true);
  const [imageError, setImageError] = useState(!imageUrl);

  return (
    <section className="py-8">
      <div className="container mx-auto px-4">
        <div className="relative rounded-2xl overflow-hidden bg-[hsl(var(--hero-bg))] border border-[hsl(var(--border))]">
          {/* Decorative teal accent top bar */}
          <div className="h-1 w-full store-gradient" />

          <div className="flex flex-col md:flex-row items-center gap-8 p-8 md:p-12">
            {/* Text content */}
            <div className="flex-1 text-center md:text-right">
              {badge && (
                <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-[hsl(var(--primary)/0.1)] text-[hsl(var(--primary))] text-xs font-bold mb-4">
                  <Sparkles size={12} />
                  {badge}
                </span>
              )}
              <h3 className="text-2xl md:text-3xl font-black text-[hsl(var(--foreground))] mb-3 leading-tight">
                {headline}
              </h3>
              <p className="text-[hsl(var(--muted-foreground))] text-sm mb-6 max-w-md md:mr-0 mx-auto">
                {subtitle}
              </p>
              <Link
                to={ctaLink}
                className="inline-flex items-center gap-2 px-7 py-3 rounded-2xl store-gradient text-white font-bold text-sm hover:opacity-90 transition-all duration-200 shadow-lg shadow-[hsl(var(--primary)/0.25)]"
              >
                {ctaText}
                <ArrowLeft size={16} />
              </Link>
            </div>

            {/* Image */}
            <div className="w-52 h-52 md:w-60 md:h-60 rounded-2xl overflow-hidden shadow-xl shrink-0 ring-2 ring-[hsl(var(--primary)/0.15)]">
              {imageError ? (
                <div className="w-full h-full store-gradient flex items-center justify-center">
                  <ShoppingBag className="h-16 w-16 text-white/60" />
                </div>
              ) : (
                <>
                  {imageLoading && (
                    <div className="absolute inset-0 bg-[hsl(var(--muted))] animate-pulse rounded-2xl" />
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
};

export default ModernPromoBanner;
